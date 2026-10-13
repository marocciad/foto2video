"""Grafiche animate (HyperFrames) sopra il video della regia: contatori, barre, rating, forma, logo.

Le grafiche partono quando nel parlato compaiono le parole chiave definite in personaggio.json:

    "grafiche": [
      {"parole": ["gol attesi", "xg"], "tipo": "barre", "kicker": "GOL ATTESI (xG)",
       "voci": [{"nome": "CASA", "valore": 1.84}, {"nome": "OSPITI", "valore": 0.92}]},
      {"parole": ["migliaia di partite"], "tipo": "contatore", "valore": 12480, "etichetta": "PARTITE"}
    ]

Il modello HTML (grafica/template.html) viene reso con sfondo trasparente (WebM VP9 con alfa)
e sovrapposto al video con ffmpeg. Se qualcosa va storto si tiene il video senza grafiche.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "grafica", "template.html")
FONTS = ["Anton-Regular.ttf", "BebasNeue-Regular.ttf", "ArchivoBlack-Regular.ttf", "RussoOne-Regular.ttf",
         "BlackOpsOne-Regular.ttf"]
HYPERFRAMES = os.environ.get("HYPERFRAMES_CMD", "npx -y hyperframes@0.8.152").split()

MAX_DUR = 2.6   # s: durata massima di una grafica
MIN_DUR = 1.1   # s: piu' corta di cosi' non si legge, e la grafica si salta


def _norm(t: str) -> str:
    return re.sub(r"[^\w]", "", t.lower())


def find_phrase(words: list, phrase: str):
    """Istante d'inizio della prima occorrenza di `phrase` (una o piu' parole) nel parlato, o None."""
    target = [_norm(p) for p in phrase.split() if _norm(p)]
    keys = [_norm(w["w"]) for w in words]
    for i in range(len(keys) - len(target) + 1):
        if keys[i:i + len(target)] == target:
            return words[i]["a"]
    return None


def plan_graphics(words: list, rules: list, speech_end: float, logo: str = None) -> list:
    """Eventi per il modello HTML: una grafica per regola (alla prima parola chiave trovata),
    in ordine di tempo, senza sovrapporsi. Logica pura (testata)."""
    hits = []
    for rule in rules:
        times = [t for t in (find_phrase(words, p) for p in rule.get("parole", [])) if t is not None]
        if times:
            ev = {k: v for k, v in rule.items() if k != "parole"}
            ev["t"] = round(max(min(times) - 0.1, 0.0), 3)
            hits.append(ev)
    hits.sort(key=lambda e: e["t"])
    events = []
    for i, ev in enumerate(hits):
        if events and ev["t"] < events[-1]["t"] + events[-1]["durata"] - 0.01:  # tolleranza arrotondamenti
            continue  # la precedente e' ancora a schermo
        nxt = hits[i + 1]["t"] if i + 1 < len(hits) else speech_end
        ev["durata"] = round(min(MAX_DUR, max(nxt, ev["t"]) - ev["t"], speech_end - ev["t"]), 3)
        if ev["durata"] >= MIN_DUR:
            events.append(ev)
    if logo:
        events.append({"tipo": "chiusura", "t": round(speech_end, 3), "logo": logo})
    return events


def build_captions(phrases: list, size: tuple, emphasis: set, seed: int = 7) -> dict:
    """Sottotitoli per il modello HTML: gruppi di max 3 parole (a capo sulla punteggiatura), font a
    rotazione, parole chiave piu' grandi e in pillola, "BetForge" in oro. Stesse regole di
    regia.build_ass, cosi' il risultato resta quello approvato. Logica pura (testata)."""
    import random

    from studio.regia import FONTS as CAPTION_FONTS

    rnd = random.Random(seed)
    W, H = size
    vertical = H >= W
    groups, last_font = [], None
    for p in phrases:
        current = [[]]
        for w in p["parole"]:
            current[-1].append(w)
            if len(current[-1]) == 3 or w["w"][-1:] in ".,…!?":
                current.append([])
        for g in (g for g in current if g):
            font, k_font = rnd.choice([f for f in CAPTION_FONTS if f[0] != last_font])
            last_font = font
            words = []
            for w in g:
                key = w["w"].lower().strip(".,…!?;:")
                big = key in emphasis or len(key) >= 8
                words.append({"testo": w["w"].upper().rstrip(",;:"), "a": w["a"],
                              "k": 1.38 if big else (0.72 if len(key) <= 3 else 1.0),
                              "brand": "betforge" in key, "enfasi": key in emphasis and "betforge" not in key})
            groups.append({"font": font, "k": k_font, "rot": round(rnd.uniform(-4, 4), 1),
                           "inizio": g[0]["a"], "fine": g[-1]["b"] + 0.15, "parole": words})
    for g, nxt in zip(groups, groups[1:]):  # nessuna sovrapposizione tra gruppi
        g["fine"] = min(g["fine"], nxt["inizio"])
    fs = round(66 * W / 720) if vertical else round(60 * H / 720)
    return {"sottotitoli": groups, "cap_fs": fs, "cap_y": 0.70 if vertical else 0.80}


def render_overlay(events: list, size: tuple, duration: float, out_webm: str, logo_rgba=None,
                   captions: dict = None):
    """Rende il modello con sfondo trasparente (HyperFrames -> WebM VP9 con alfa)."""
    W, H = size
    work = tempfile.mkdtemp(prefix="grafica_")
    try:
        os.makedirs(os.path.join(work, "fonts"))
        for f in FONTS:
            shutil.copy(os.path.join(HERE, "fonts", f), os.path.join(work, "fonts", f))
        if logo_rgba is not None:
            logo_rgba.save(os.path.join(work, "logo.png"))
        html = open(TEMPLATE, encoding="utf-8").read()
        for k, v in {"__W__": str(W), "__H__": str(H), "__DUR__": f"{duration:.3f}",
                     "__CONFIG__": json.dumps({"eventi": events, **(captions or {})}, ensure_ascii=False)}.items():
            html = html.replace(k, v)
        with open(os.path.join(work, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        p = subprocess.run(HYPERFRAMES + ["render", work, "--format", "webm", "-o", out_webm, "--quiet"],
                           capture_output=True, text=True, shell=os.name == "nt")
        if p.returncode != 0 or not os.path.exists(out_webm):
            raise RuntimeError(f"hyperframes render fallito: {(p.stderr or p.stdout)[-600:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def composite(video: str, overlay_webm: str, out: str):
    """Sovrappone la grafica trasparente al video (audio copiato cosi' com'e')."""
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-c:v", "libvpx-vp9", "-i", overlay_webm,
                        "-filter_complex", "[0:v][1:v]overlay=0:0:eof_action=pass[v]", "-map", "[v]", "-map", "0:a?",
                        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-c:a", "copy", "-movflags", "+faststart", out], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg (grafica) fallito: {p.stderr[-600:]}")
