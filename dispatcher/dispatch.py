"""
Foto2Video - dispatcher (gira su GitHub Actions ogni 10 minuti).

1. Se il notebook Kaggle e' gia' in esecuzione o in coda, non fa nulla
   (il worker sta gia' leggendo i messaggi).
2. Altrimenti guarda i messaggi in attesa su Telegram SENZA confermarli.
3. Se c'e' almeno una foto di un utente autorizzato, avvia il notebook
   su Kaggle (kaggle kernels push) e avvisa in chat.
4. Altrimenti (vocali, copioni, comandi) passa la mano allo studio
   (`python -m studio`, regia virtuale su CPU) tramite l'output `action=studio`.

Variabili d'ambiente: TELEGRAM_TOKEN, ALLOWED_USER_IDS, KAGGLE_USERNAME,
KAGGLE_KEY (anche token KGAT_), KERNEL_DIR (cartella con worker.py e kernel-metadata.json).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import requests

ACTIVE_STATES = ("running", "queued")
# Stati in cui e' sicuro avviare: il kernel non sta girando. Con uno stato
# sconosciuto (API giu', output CLI cambiato) NON avviamo: meglio aspettare
# il prossimo giro che rischiare due worker in parallelo.
STARTABLE_STATES = ("complete", "error", "cancel", "missing")

def tg(token: str, method: str, retries: int = 3, **params):
    for attempt in range(retries):
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=30)
            data = r.json()
            break
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def parse_ids(value: str) -> set:
    return {int(v) for v in re.findall(r"-?\d+", value or "")}


def is_job(update: dict) -> bool:
    msg = update.get("message") or {}
    return bool(msg.get("photo")) or msg.get("document", {}).get("mime_type", "").startswith("image/")


def kaggle_env():
    """I token nuovi (KGAT_...) vanno passati alla CLI come KAGGLE_API_TOKEN (auth Bearer)."""
    key = os.environ.get("KAGGLE_KEY", "")
    if key.startswith("KGAT_") and not os.environ.get("KAGGLE_API_TOKEN"):
        os.environ["KAGGLE_API_TOKEN"] = os.environ.pop("KAGGLE_KEY")


def classify_status(out: str) -> str:
    # Formato tipico: 'user/slug has status "KernelWorkerStatus.RUNNING"'
    out = out.lower()
    if "401" in out or "unauthorized" in out:
        return "unknown:credenziali kaggle non valide"
    # La CLI 2.x risponde "Cannot access kernel ... 'kernels.get' was denied" anche
    # per un kernel che non esiste ancora (primo avvio). Le credenziali sono gia'
    # escluse dal controllo sopra, quindi qui il kernel semplicemente non c'e'.
    if "404" in out or "not found" in out or "cannot access kernel" in out:
        return "missing"
    for state in ("running", "queued", "complete", "error", "cancel"):
        if state in out:
            return state
    return "unknown:" + out.strip()[:200]


def kernel_status(kernel_id: str) -> str:
    p = subprocess.run(["kaggle", "kernels", "status", kernel_id], capture_output=True, text=True)
    return classify_status(p.stdout + p.stderr)


def is_audio(update: dict) -> bool:
    msg = update.get("message") or {}
    return bool(msg.get("voice") or msg.get("audio"))


def decide(updates: list, allowed: set, status: str) -> str:
    """Logica pura, testabile: 'skip' | 'idle' | 'start' | 'studio'.

    - start: la prima cosa in coda di un utente autorizzato e' una foto -> worker GPU su Kaggle;
    - studio: tutto il resto (vocali, copioni, comandi, estranei) -> `python -m studio` su
      GitHub Actions, che si ferma da solo alla prima foto e la lascia in coda per Kaggle.
    """
    if status in ACTIVE_STATES or status not in STARTABLE_STATES:
        return "skip"
    if not updates:
        return "idle"
    mine = [u for u in updates if (u.get("message") or {}).get("from", {}).get("id") in allowed]
    if mine and is_job(mine[0]) and mine[0] is updates[0]:
        return "start"
    return "studio"


def set_output(**values):
    """Passa la decisione ai passi successivi del workflow (installano ffmpeg/whisper solo se serve)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def main():
    kaggle_env()
    token = os.environ["TELEGRAM_TOKEN"]
    allowed = parse_ids(os.environ.get("ALLOWED_USER_IDS", ""))
    kernel_dir = os.environ.get("KERNEL_DIR", "kaggle")
    with open(os.path.join(kernel_dir, "kernel-metadata.json"), encoding="utf-8") as f:
        kernel_id = json.load(f)["id"]

    # ORDINE IMPORTANTE: prima Kaggle, poi Telegram. Se il worker e' attivo sta
    # facendo long polling; una nostra getUpdates gli farebbe chiudere la
    # connessione (409 Conflict). Quindi Telegram si legge solo a worker spento.
    status = kernel_status(kernel_id)
    print(f"[dispatch] kernel {kernel_id}: {status}")
    if status in ACTIVE_STATES:
        print("[dispatch] worker gia' attivo, esco")
        return
    if status not in STARTABLE_STATES:
        print(f"[dispatch] stato incerto ({status}), non avvio nulla")
        return

    # timeout=0 e nessun offset: legge senza confermare nulla
    updates = tg(token, "getUpdates", timeout=0, allowed_updates=["message"])
    action = decide(updates, allowed, status)
    print(f"[dispatch] {len(updates)} messaggi in attesa -> {action}")
    tts = any(re.search(r"(?i)#voce\b", (u.get("message") or {}).get("text") or "") for u in updates)
    # media: serve ffmpeg/whisper (vocale o voce sintetica); tts: serve anche kokoro
    llm = any(re.search(r"(?i)#idea\b", (u.get("message") or {}).get("text") or "") for u in updates)
    set_output(action=action, media=int(tts or any(is_audio(u) for u in updates)), tts=int(tts), llm=int(llm))

    if action == "start":
        p = subprocess.run(["kaggle", "kernels", "push", "-p", kernel_dir], capture_output=True, text=True)
        print(p.stdout, p.stderr)
        out = (p.stdout + p.stderr).lower()
        ok = p.returncode == 0 and "error" not in out
        quota = "quota" in out or "exceeded" in out
        chats = {u["message"]["chat"]["id"] for u in updates
                 if is_job(u) and (not allowed or u["message"]["from"]["id"] in allowed)}
        if ok:
            text = "🚀 Accendo la GPU, il video arriva tra qualche minuto."
        elif quota:
            text = "⚠️ Quota GPU settimanale di Kaggle esaurita: riprovo al prossimo giro."
        else:
            text = "⚠️ Non riesco ad avviare la GPU su Kaggle. Riprovo al prossimo giro."
        for chat_id in chats:
            tg(token, "sendMessage", chat_id=chat_id, text=text)
        if not ok:
            sys.exit(1)


if __name__ == "__main__":
    main()
