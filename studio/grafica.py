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
    """Grafiche da regole a parole chiave (personaggio.json, "grafiche"): una per regola, alla prima
    parola chiave trovata. Logica pura (testata)."""
    hits = []
    for rule in rules:
        times = [t for t in (find_phrase(words, p) for p in rule.get("parole", [])) if t is not None]
        if times:
            ev = {k: v for k, v in rule.items() if k != "parole"}
            ev["t"] = round(max(min(times) - 0.1, 0.0), 3)
            hits.append(ev)
    return schedule(hits, speech_end, logo)


# --- grafiche scritte nel copione ------------------------------------------------------------
# {numero: 12.480 | partite analizzate}   {barre: Inter 1,8 - Milan 0,9 | gol attesi}
# {forma: VVNPV | Inter}                   {rating: 87 | rating Inter}
# {app} / {app: calendario, analisi | etichetta}   (schermate in personaggio.json, "app")
# {mappa: Italia, Inghilterra, Spagna | campionati analizzati}
MARKUP = re.compile(r"\{\s*(numero|barre|forma|rating|app|mappa)\s*(?::\s*([^|}]*?))?\s*(?:\|\s*([^}]*?)\s*)?\}",
                    re.IGNORECASE)
DUR_BY_TYPE = {"app": 4.5, "mappa": 3.6}  # da guardare con calma: restano piu' a lungo

# Nomi italiani -> codice ISO 3166-1 numerico dei confini world-atlas (Natural Earth).
# Inghilterra, Scozia e Galles non sono Stati: si illumina il Regno Unito.
COUNTRIES = {
    "italia": "380", "inghilterra": "826", "scozia": "826", "galles": "826", "regno unito": "826",
    "spagna": "724", "germania": "276", "francia": "250", "portogallo": "620", "olanda": "528",
    "paesi bassi": "528", "belgio": "056", "turchia": "792", "grecia": "300", "danimarca": "208",
    "svezia": "752", "norvegia": "578", "svizzera": "756", "austria": "040", "polonia": "616",
    "croazia": "191", "serbia": "688", "repubblica ceca": "203", "cechia": "203", "ucraina": "804",
    "romania": "642", "irlanda": "372", "finlandia": "246", "russia": "643", "brasile": "076",
    "argentina": "032", "stati uniti": "840", "usa": "840", "messico": "484", "giappone": "392",
    "arabia saudita": "682", "cina": "156", "australia": "036", "corea del sud": "410",
    "egitto": "818", "marocco": "504",
}
WORLD = os.path.join(HERE, "grafica", "world-50m.json")
MAP_LIBS = ('<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js"></script>')


def _number(text: str) -> tuple:
    """"12.480" -> (12480, 0, ""), "1,8" -> (1.8, 1, ""), "87%" -> (87, 0, "%"), "12.480+" -> (12480, 0, "+")."""
    m = re.match(r"\s*([\d.]+(?:,\d+)?)\s*(\S*)", text)
    if not m:
        raise ValueError(f"numero non valido: {text!r}")
    raw, suffix = m.group(1), m.group(2)
    dec = len(raw.split(",")[1]) if "," in raw else 0
    return float(raw.replace(".", "").replace(",", ".")), dec, suffix


def _markup_event(kind: str, body: str, label: str) -> dict:
    kind, body, label = kind.lower(), (body or "").strip(), (label or "").strip()
    if kind == "app":  # i nomi diventano file in make_video (vuoto = tutte le schermate)
        return {"tipo": "app", "schermate": [n.strip().lower() for n in body.split(",") if n.strip()],
                "kicker": label.upper()}
    if kind == "mappa":
        names = [n.strip() for n in body.split(",") if n.strip()]
        unknown = [n for n in names if n.lower() not in COUNTRIES]
        if not names or unknown:
            raise ValueError(f"paesi non riconosciuti: {', '.join(unknown) or 'nessuno'}")
        return {"tipo": "mappa", "kicker": label.upper(),
                "paesi": [{"nome": n.upper(), "id": COUNTRIES[n.lower()]} for n in names[:8]]}
    if kind == "numero":
        val, dec, suffix = _number(body)
        return {"tipo": "contatore", "valore": val, "decimali": dec, "suffisso": suffix,
                "etichetta": label.upper()}
    if kind == "barre":
        voci = []
        for part in re.split(r"\s+(?:-|vs\.?|–)\s+", body):
            m = re.match(r"(.*\S)\s+([\d.,]+\S*)$", part.strip())
            if not m:
                raise ValueError(f"barra non valida: {part!r} (scrivi: Nome valore)")
            val, dec, _ = _number(m.group(2))
            voci.append({"nome": m.group(1).upper(), "valore": val})
        return {"tipo": "barre", "kicker": (label or "confronto").upper(), "voci": voci,
                "decimali": max(1, dec)}
    if kind == "forma":
        seq = body.upper().replace("W", "V").replace("D", "N").replace("L", "P")
        seq = re.sub(r"[^VNP]", "", seq)
        if not seq:
            raise ValueError("forma vuota: usa V, N, P (es. VVNPV)")
        return {"tipo": "forma", "kicker": ("forma " + label).strip().upper(), "sequenza": seq[:7]}
    val, _, _ = _number(body)
    return {"tipo": "rating", "kicker": (label or "rating").upper(), "valore": min(val, 100), "etichetta": "SU 100"}


def parse_markup(script: str) -> tuple:
    """Toglie le grafiche dal copione: (testo_pulito, [(indice_parola, evento)], [errori]).
    La grafica si aggancia all'inizio della frase che precede il segno (alla prima parola, se il
    segno apre il testo): parte quando cominci a dire la frase. Un segno scritto male si salta e
    finisce negli errori: il video si fa comunque. Logica pura (testata)."""
    items, errors, clean, pos = [], [], [], 0
    for m in MARKUP.finditer(script):
        clean.append(script[pos:m.start()])
        before = " ".join(clean).split()
        idx = len(before) - 1
        while idx > 0 and before[idx - 1][-1:] not in ".!?…":  # risale all'inizio della frase
            idx -= 1
        try:
            items.append((max(idx, 0), _markup_event(m.group(1), m.group(2), m.group(3))))
        except ValueError as e:
            errors.append(f"{m.group(0)} → {e}")
        pos = m.end()
    clean.append(script[pos:])
    text = re.sub(r"\s+", " ", " ".join(clean)).strip()
    text = re.sub(r"\s+([.,;:!?…])", r"\1", text)  # niente spazi prima della punteggiatura
    return text, items, errors


def markup_hits(items: list, words: list, offset: int = 0) -> list:
    """Istanti delle grafiche del copione: alla parola d'aggancio (`offset` = parole dell'intro)."""
    hits = []
    for idx, ev in items:
        i = min(idx + offset, len(words) - 1)
        if i >= 0:
            hits.append({**ev, "t": round(max(words[i]["a"] - 0.1, 0.0), 3)})
    return hits


def schedule(hits: list, speech_end: float, logo: str = None) -> list:
    """In ordine di tempo, senza sovrapposizioni, ognuna al massimo MAX_DUR; poi la chiusura."""
    hits = sorted(hits, key=lambda e: e["t"])
    events = []
    for i, ev in enumerate(hits):
        if events and ev["t"] < events[-1]["t"] + events[-1]["durata"] - 0.01:  # tolleranza arrotondamenti
            continue  # la precedente e' ancora a schermo
        nxt = hits[i + 1]["t"] if i + 1 < len(hits) else speech_end
        limit = DUR_BY_TYPE.get(ev.get("tipo"), MAX_DUR)
        ev["durata"] = round(min(limit, max(nxt, ev["t"]) - ev["t"], speech_end - ev["t"]), 3)
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


def overlay_config(phrases: list, size: tuple, emphasis: set, cuts: list, intro: bool) -> dict:
    """Tutto cio' che l'overlay disegna oltre alle grafiche: sottotitoli, transizioni ai cambi di
    inquadratura (`cuts`, in secondi) e, se c'e' l'intro, l'apertura animata. Logica pura (testata)."""
    cfg = build_captions(phrases, size, emphasis)
    cfg["tagli"] = [round(t, 3) for t in cuts if t > 0.3]
    if intro and len(phrases) >= 2:
        cfg["apertura"] = {"fine_benvenuti": phrases[0]["fine"], "inizio_brand": phrases[1]["inizio"],
                           "fine_brand": phrases[1]["fine"]}
    return cfg


def available() -> bool:
    """L'overlay si usa solo dove HyperFrames e' installato (GitHub Actions lo segnala con HYPERFRAMES=1)."""
    return os.environ.get("HYPERFRAMES") == "1"


def resolve_app_screens(events: list, screens: dict) -> dict:
    """Nomi delle schermate -> percorsi relativi nel progetto di rendering. Toglie le grafiche "app"
    senza schermate disponibili. Ritorna {percorso_relativo: nome} dei file da copiare."""
    needed = {}
    for ev in list(events):
        if ev.get("tipo") != "app":
            continue
        names = [n for n in (ev["schermate"] or list(screens)) if n in screens]
        if not names:
            events.remove(ev)
            continue
        ev["schermate"] = [f"app/{n}.png" for n in names]
        needed.update({f"app/{n}.png": n for n in names})
    return needed


def render_overlay(events: list, size: tuple, duration: float, out_webm: str, logo_rgba=None,
                   captions: dict = None, files: dict = None):
    """Rende il modello con sfondo trasparente (HyperFrames -> WebM VP9 con alfa).
    `files`: {percorso_relativo: file_sorgente} da copiare accanto al modello (schermate dell'app)."""
    W, H = size
    work = tempfile.mkdtemp(prefix="grafica_")
    try:
        os.makedirs(os.path.join(work, "fonts"))
        for f in FONTS:
            shutil.copy(os.path.join(HERE, "fonts", f), os.path.join(work, "fonts", f))
        if logo_rgba is not None:
            logo_rgba.save(os.path.join(work, "logo.png"))
        for rel, src in (files or {}).items():
            os.makedirs(os.path.dirname(os.path.join(work, rel)), exist_ok=True)
            shutil.copy(src, os.path.join(work, rel))
        html = open(TEMPLATE, encoding="utf-8").read()
        # mappa: librerie e confini inseriti nella pagina solo se servono (niente rete in rendering)
        map_libs = ""
        if any(e.get("tipo") == "mappa" for e in events):
            with open(WORLD, encoding="utf-8") as f:
                map_libs = MAP_LIBS + "\n<script>window.__WORLD = " + f.read() + ";</script>"
        html = html.replace("<!--__MAPLIBS__-->", map_libs)
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


# Look "cinema" (personaggio.json: "look": "cinema"): applicato al video della regia SOTTO le grafiche,
# cosi' scritte e sottotitoli restano nitidi. Contrasto, ombre fredde e luci dorate, vignettatura, grana fine.
# Grana FISSA (allf=u): quella che cambia a ogni fotogramma (allf=t) portava il video da 11 a 74 MB,
# oltre il limite di 50 MB di Telegram.
LOOKS = {
    "cinema": ("eq=contrast=1.07:saturation=1.06:gamma=0.97,"
               "colorbalance=rs=-0.03:gs=0.00:bs=0.04:rh=0.05:gh=0.02:bh=-0.04,"
               "vignette=angle=PI/4.5,noise=alls=4:allf=u"),
}


def composite(video: str, overlay_webm: str, out: str, look: str = None):
    """Sovrappone la grafica trasparente al video (audio copiato cosi' com'e'), con il look opzionale."""
    grade = (LOOKS[look] + ",") if look in LOOKS else ""
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-c:v", "libvpx-vp9", "-i", overlay_webm,
                        "-filter_complex", f"[0:v]{grade}null[b];[b][1:v]overlay=0:0:eof_action=pass[v]",
                        "-map", "[v]", "-map", "0:a?",
                        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-c:a", "copy", "-movflags", "+faststart", out], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg (grafica) fallito: {p.stderr[-600:]}")
