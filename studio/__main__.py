"""Studio: vocale su Telegram -> video con la regia virtuale (gira su GitHub Actions, CPU).

    python -m studio

Variabili d'ambiente: TELEGRAM_TOKEN, ALLOWED_USER_IDS, ASSETS_DIR (default "assets"),
CHARACTER (sottocartella del personaggio, default "betforge"), WHISPER_MODEL (default "small").

Uso dal bot: si manda un **vocale** con il contenuto e, per avere sottotitoli esatti, il
**copione** come messaggio di testo (prima o dopo il vocale, entro 10 minuti).
Tag nel copione: #verticale / #orizzontale / #quadrato (un solo formato), #nointro (senza intro),
#voce (niente vocale: il testo lo legge la voce sintetica, e il lavoro parte subito).
Le foto non si toccano: restano in coda per il worker su Kaggle.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time

import requests

PAIR_WINDOW = 600     # s: distanza massima tra vocale e copione
WAIT_VOICE = 90       # s: un vocale senza copione aspetta un po', il copione potrebbe arrivare dopo
WAIT_TEXT = 900       # s: un copione senza vocale aspetta il vocale (registrare richiede tempo)

# Testi dei comandi (Markdown di Telegram). I tag in `codice` si copiano con un tocco.
HELP = ("🎬 *Studio BetForge*\n\n"
        "*Video con la tua voce*\n"
        "1. Manda il *testo* del copione.\n"
        "2. Manda il *vocale* con lo stesso testo (prima o dopo, entro 10 minuti).\n"
        "Dopo qualche minuto ricevi il video in 9:16, 16:9 e 1:1.\n\n"
        "*Senza registrare*: manda solo il testo con `#voce` in fondo.\n"
        "*Non sai cosa dire?* Scrivi `#idea` e l'argomento: ti propongo un copione.\n\n"
        "*Opzioni* (tocca per copiare, poi incolla in fondo al testo):\n"
        "`#verticale` · `#orizzontale` · `#quadrato` · `#voce` · `#nointro`\n\n"
        "Altri comandi: /formati · /voce · /idea · /id\n"
        "Le *foto* con didascalia vanno ancora al generatore IA su GPU.")
FORMATS_HELP = ("📐 *Formati*\n\n"
                "Senza tag ricevi tutti e tre. Con un tag ne ricevi uno solo:\n"
                "• 9:16 per Reels, TikTok, Shorts → `#verticale`\n"
                "• 16:9 per YouTube → `#orizzontale`\n"
                "• 1:1 per i post di Instagram e Facebook → `#quadrato`\n\n"
                "Scrivi il tag in fondo al testo del copione.")
VOICE_HELP = ("🗣️ *Video senza registrare*\n\n"
              "Manda solo il testo con `#voce` in fondo, per esempio:\n"
              "`Tu guardi la partita. Io guardo i numeri. #voce`\n\n"
              "Lo legge la voce sintetica con lo stesso effetto metallico, e il video parte subito.\n"
              "L'intro resta la tua registrazione: aggiungi `#nointro` per toglierla.")
IDEA_HELP = ("✍️ *Copione proposto dal bot*\n\n"
             "Scrivi un'idea con `#idea`, per esempio:\n"
             "`#idea parla dei gol attesi`\n\n"
             "Ti rispondo con un copione di circa 25 secondi da copiare con un tocco. Poi lo registri "
             "(testo + vocale) oppure lo rimandi con `#voce`. Non ti piace? Rimanda `#idea`.")
# Menu dei comandi (pulsante "Menu" accanto alla tastiera): lo registra setup/bot_menu.py
COMMANDS = [("help", "Come si usa"), ("formati", "Formati e tag"),
            ("voce", "Video senza registrare (voce sintetica)"), ("idea", "Fatti proporre un copione"),
            ("id", "Il tuo ID Telegram")]
COMMAND_TEXTS = {"start": HELP, "help": HELP, "aiuto": HELP, "formati": FORMATS_HELP, "voce": VOICE_HELP,
                 "idea": IDEA_HELP}


# ---------------------------------------------------------------------------
# Pianificazione della coda (logica pura, testata)
# ---------------------------------------------------------------------------

def kind(msg: dict) -> str:
    if msg.get("voice") or msg.get("audio"):
        return "voice"
    if msg.get("photo") or msg.get("document", {}).get("mime_type", "").startswith("image/"):
        return "photo"
    text = (msg.get("text") or "").strip()
    if text.startswith("/"):
        return "command"
    if re.search(r"(?i)#idea\b", text):
        return "idea"  # richiesta di un copione: si risponde con il testo, niente video
    if re.search(r"(?i)#voce\b", text):
        return "tts"  # testo da leggere con la voce sintetica: niente vocale da aspettare
    return "text" if text else "other"


def plan_queue(updates: list, allowed: set, now: float) -> list:
    """Divide la coda in lavori da fare, in ordine. Ogni lavoro ha gli update_id che "consuma".

    Lavori: {"tipo": "video", "voce": msg, "copione": msg|None, "ids": [...]},
            {"tipo": "risposta", "msg": msg, "testo": str, "ids": [...]}, {"tipo": "ignora", "ids": [...]}.
    Si ferma (senza consumarli) a: prima foto di un utente autorizzato (e' per Kaggle), vocale troppo
    recente senza copione, copione che aspetta ancora il suo vocale.
    """
    msgs = [(u["update_id"], u.get("message") or {}) for u in updates]
    # 1) abbina ogni vocale al testo dello stesso utente piu' vicino nel tempo
    texts = [(uid, m) for uid, m in msgs if kind(m) == "text" and m.get("from", {}).get("id") in allowed]
    pair, used = {}, set()
    for uid, m in msgs:
        if kind(m) == "voice" and m.get("from", {}).get("id") in allowed:
            cands = [(abs(t["date"] - m["date"]), tid, t) for tid, t in texts
                     if tid not in used and t["from"]["id"] == m["from"]["id"]
                     and abs(t["date"] - m["date"]) <= PAIR_WINDOW]
            if cands:
                _, tid, t = min(cands, key=lambda c: c[0])
                pair[uid] = (tid, t)
                used.add(tid)
    # 2) lavori in ordine di coda
    jobs = []
    for uid, m in msgs:
        user = m.get("from", {}).get("id")
        k = kind(m)
        if uid in used:
            continue  # copione: viaggia con il suo vocale
        if user not in allowed:
            reply = "⛔ Bot privato." if k != "other" else None
            jobs.append({"tipo": "risposta", "msg": m, "testo": reply, "ids": [uid]} if reply
                        else {"tipo": "ignora", "ids": [uid]})
            continue
        if k == "photo":
            break  # resta in coda per il worker GPU
        if k == "voice":
            if uid not in pair and now - m.get("date", now) < WAIT_VOICE:
                break  # il copione potrebbe arrivare tra poco
            tid, t = pair.get(uid, (None, None))
            jobs.append({"tipo": "video", "voce": m, "copione": t, "ids": [uid] + ([tid] if tid else [])})
        elif k == "tts":
            jobs.append({"tipo": "video", "voce": None, "copione": m, "ids": [uid]})
        elif k == "idea":
            jobs.append({"tipo": "copione", "msg": m, "ids": [uid]})
        elif k == "text":
            if now - m.get("date", now) < WAIT_TEXT:
                break  # copione in attesa del suo vocale
            jobs.append({"tipo": "risposta", "msg": m, "ids": [uid],
                         "testo": "📝 Ho ricevuto un testo ma nessun vocale entro 15 minuti. "
                                  "Rimanda il testo insieme al vocale, oppure aggiungi #voce "
                                  "e lo leggo con la voce sintetica."})
        elif k == "command":
            name = m["text"].split()[0][1:].split("@")[0].lower()
            reply = f"Il tuo ID Telegram è: {user}" if name == "id" else COMMAND_TEXTS.get(name, HELP)
            jobs.append({"tipo": "risposta", "msg": m, "testo": reply, "ids": [uid], "markdown": name != "id"})
        else:
            jobs.append({"tipo": "ignora", "ids": [uid]})
    return jobs


def confirm_offset(all_ids: list, handled: set):
    """Telegram conferma tutto cio' che sta prima dell'offset: si conferma solo il tratto iniziale
    della coda gia' gestito per intero. None se non c'e' niente da confermare."""
    offset = None
    for uid in sorted(all_ids):
        if uid not in handled:
            break
        offset = uid + 1
    return offset


def parse_tags(text: str) -> tuple:
    tags = {t.lower() for t in re.findall(r"#(\w+)", text or "")}
    clean = re.sub(r"#\w+", "", text or "").strip()
    formats = ["9x16", "16x9", "1x1"]
    for tag, fmt in (("verticale", "9x16"), ("orizzontale", "16x9"), ("quadrato", "1x1")):
        if tag in tags:
            formats = [fmt]
            break
    return clean, formats, "nointro" in tags


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"

    def call(self, method: str, http_timeout: float = 60, files=None, **params):
        r = requests.post(f"{self.base}/{method}", data=params if files else None,
                          json=None if files else params, files=files, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    def send(self, chat_id, text, reply_to=None, markdown=False):
        params = {"chat_id": chat_id, "text": text}
        if reply_to:
            params.update(reply_to_message_id=reply_to, allow_sending_without_reply=True)
        if markdown:
            params["parse_mode"] = "Markdown"
        return self.call("sendMessage", **params)["message_id"]

    def edit(self, chat_id, message_id, text):
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
        except RuntimeError as e:
            if "not modified" not in str(e):
                print(f"[studio] edit fallita: {e}")

    def download(self, file_id, dest):
        path = self.call("getFile", file_id=file_id)["file_path"]
        r = requests.get(f"{self.file_base}/{path}", timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        return dest

    def send_video(self, chat_id, path, caption, size, reply_to=None):
        with open(path, "rb") as f:
            params = {"chat_id": chat_id, "caption": caption[:1024], "supports_streaming": "true",
                      "width": size[0], "height": size[1]}
            if reply_to:
                params.update(reply_to_message_id=reply_to, allow_sending_without_reply="true")
            return self.call("sendVideo", http_timeout=300, files={"video": ("video.mp4", f, "video/mp4")},
                             **params)


# ---------------------------------------------------------------------------
# Lavorazione
# ---------------------------------------------------------------------------

def script_reply(text: str, risky: list) -> str:
    """Messaggio con il copione proposto: in un blocco di codice, cosi' si copia con un tocco."""
    secs = round(len(text.split()) / 2.6)
    msg = (f"✍️ *Copione proposto* (circa {secs} s):\n\n```\n{text}\n```\n"
           "Tocca il testo per copiarlo, poi:\n"
           "• registralo e mandami testo + vocale, oppure\n"
           "• mandalo con `#voce` e lo legge la voce sintetica.\n"
           "Non ti convince? Rimanda `#idea` per un'altra proposta.")
    if risky:
        msg += f"\n\n⚠️ Contiene parole a rischio per il divieto di pubblicità del gioco: {', '.join(risky)}. Meglio cambiarle."
    return msg


def make_script(tg: Telegram, job: dict):
    m = job["msg"]
    chat, mid = m["chat"]["id"], m["message_id"]
    idea = re.sub(r"(?i)#idea\b", "", m.get("text") or "").strip()
    if not idea:
        tg.send(chat, IDEA_HELP, reply_to=mid, markdown=True)
        return
    status = tg.send(chat, "✍️ Scrivo il copione…", reply_to=mid)
    try:
        from studio import copione  # import pigro: llama-cpp serve solo qui
        text, risky = copione.generate(idea)
        tg.edit(chat, status, "✍️ Copione pronto:")
        tg.send(chat, script_reply(text, risky), reply_to=mid, markdown=True)
    except Exception as e:  # noqa: BLE001 - qualunque errore va riportato in chat
        print(f"[studio] errore copione: {e!r}")
        tg.edit(chat, status, f"❌ Errore nel copione: {str(e)[:300]}")


def make_video(tg: Telegram, job: dict, assets: str, whisper_model: str):
    from studio import audio, regia  # import pigro: numpy/Pillow/whisper servono solo qui

    voice = job["voce"]  # None = voce sintetica dal copione
    src = voice or job["copione"]
    chat, mid = src["chat"]["id"], src["message_id"]
    script, formats, no_intro = parse_tags((job["copione"] or {}).get("text", ""))
    if voice:
        intro_msg = "🎙️ Vocale ricevuto" + (" con il testo" if script else "")
    else:
        intro_msg = "🗣️ Testo ricevuto: lo leggo con la voce sintetica"
    status = tg.send(chat, intro_msg + ": preparo il video (circa 5–10 minuti)…", reply_to=mid)
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="studio_")
    try:
        character = regia.load_character(assets)
        content = os.path.join(work, "contenuto.wav")
        if voice:
            tg.edit(chat, status, "🔊 Pulisco l'audio e applico la voce…")
            v = voice.get("voice") or voice.get("audio")
            raw = tg.download(v["file_id"], os.path.join(work, "vocale.ogg"))
            audio.clean(raw, content)
        else:
            if not script:
                raise RuntimeError("il messaggio con #voce non contiene testo da leggere")
            tg.edit(chat, status, "🗣️ Genero la voce sintetica…")
            from studio import tts  # import pigro: kokoro/torch servono solo qui
            tts.synthesize(script, content)
        parts = [content]
        intro = os.path.join(assets, character.get("intro", ""))
        if not no_intro and character.get("intro") and os.path.exists(intro):
            parts.insert(0, intro)  # l'intro e' gia' ripulita (una volta sola!)
        dry = os.path.join(work, "voce_pulita.wav")
        if len(parts) > 1:
            audio.concat(parts, dry)
        else:
            shutil.copy(content, dry)
        wet = os.path.join(work, "voce.wav")
        audio.voice3(dry, wet)

        tg.edit(chat, status, "📝 Trascrivo per i sottotitoli…")
        words = audio.transcribe(dry, whisper_model)
        if script:
            full = script
            if len(parts) > 1 and character.get("testo_intro"):
                full = character["testo_intro"] + " " + script
            words = audio.align_script(full, words)
        phrases = audio.split_phrases(words)
        if not phrases:
            raise RuntimeError("non ho riconosciuto parole nel vocale")
        emphasis = {w.lower() for w in character.get("enfasi", [])}

        for fmt in (f for f in formats if f in character["inquadrature"]):
            tg.edit(chat, status, f"🎬 Monto il video {fmt.replace('x', ':')}…")
            out = os.path.join(work, f"video_{fmt}.mp4")
            r = regia.Renderer(assets, character, fmt)
            r.render(wet, phrases, out, emphasis)
            tg.send_video(chat, out, f"🎬 {fmt.replace('x', ':')}", r.size, reply_to=mid)
        tg.edit(chat, status, f"✅ Fatto in {(time.time() - t0) / 60:.1f} min")
    except Exception as e:  # noqa: BLE001 - qualunque errore va riportato in chat
        print(f"[studio] errore: {e!r}")
        tg.edit(chat, status, f"❌ Errore: {str(e)[:300]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    token = os.environ["TELEGRAM_TOKEN"]
    allowed = {int(v) for v in re.findall(r"-?\d+", os.environ.get("ALLOWED_USER_IDS", ""))}
    if not allowed:
        sys.exit("ALLOWED_USER_IDS vuoto: il bot resta privato, non parto")
    assets = os.path.join(os.environ.get("ASSETS_DIR", "assets"), os.environ.get("CHARACTER", "betforge"))
    whisper_model = os.environ.get("WHISPER_MODEL", "small")
    tg = Telegram(token)

    updates = tg.call("getUpdates", timeout=0, allowed_updates=["message"])
    all_ids = [u["update_id"] for u in updates]
    jobs = plan_queue(updates, allowed, time.time())
    print(f"[studio] {len(updates)} update in coda, {len(jobs)} lavori")
    handled = set()
    for job in jobs:
        handled.update(job["ids"])
        # at-most-once: si conferma PRIMA di lavorare, cosi' un crash non crea un loop
        offset = confirm_offset(all_ids, handled)
        if offset:
            tg.call("getUpdates", offset=offset, timeout=0, limit=1)
        if job["tipo"] == "risposta":
            m = job["msg"]
            tg.send(m["chat"]["id"], job["testo"], reply_to=m.get("message_id"), markdown=job.get("markdown", False))
        elif job["tipo"] == "video":
            make_video(tg, job, assets, whisper_model)
        elif job["tipo"] == "copione":
            make_script(tg, job)


if __name__ == "__main__":
    main()
