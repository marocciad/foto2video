"""Voce sintetica per lo studio: testo -> wav, con Kokoro-82M (Apache 2.0) su CPU.

La voce italiana non sa chiudere "BetForge" su una consonante (dice "betforgi"): il marchio
lo pronuncia una voce inglese e si incolla dentro la frase italiana. Dopo, l'effetto "voce 3"
(rubberband + eco) rende le due voci indistinguibili.
"""

from __future__ import annotations

import re

import numpy as np

SR = 24000            # Kokoro lavora a 24 kHz
VOICE_IT = "im_nicola"
VOICE_EN = "am_onyx"
SPEED = 0.92
GAP_WORD = 0.04       # s tra un pezzo italiano e "BetForge"
PAUSE = 0.40          # s dopo una frase
PAUSE_SUSPENDED = 1.0  # s dopo i puntini di sospensione
BRAND = re.compile(r"(betforge)", re.IGNORECASE)


def segments(text: str) -> list:
    """Divide il testo in pezzi da sintetizzare: [(lingua, testo, pausa_dopo), ...]. Logica pura."""
    out = []
    for sentence in re.split(r"(?<=[.!?…])\s+", text.replace("...", "…").strip()):
        if not sentence:
            continue
        parts = [p.strip() for p in BRAND.split(sentence) if re.search(r"\w", p)]  # niente pezzi di sola punteggiatura
        for i, part in enumerate(parts):
            lang = "en" if BRAND.fullmatch(part.strip(".,!?…")) else "it"
            last = i == len(parts) - 1
            pause = (PAUSE_SUSPENDED if sentence.endswith("…") else PAUSE) if last else GAP_WORD
            out.append((lang, "BetForge" + part[len("BetForge"):] if lang == "en" else part, pause))
    return out


def _trim(a: np.ndarray, thr: float = 0.01) -> np.ndarray:
    idx = np.where(np.abs(a) > thr)[0]
    return a[max(idx[0] - 240, 0): idx[-1] + 480] if len(idx) else a


def synthesize(text: str, out_wav: str):
    """Sintetizza `text` in `out_wav` (24 kHz mono). Import pigro: kokoro/torch servono solo qui."""
    import soundfile as sf
    from kokoro import KPipeline

    pipes = {"it": KPipeline(lang_code="i", repo_id="hexgrad/Kokoro-82M"),
             "en": KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")}
    voices = {"it": VOICE_IT, "en": VOICE_EN}
    chunks = []
    for lang, part, pause in segments(text):
        audio = np.concatenate([np.asarray(r.audio, np.float32)
                                for r in pipes[lang](part, voice=voices[lang], speed=SPEED)])
        chunks += [_trim(audio), np.zeros(int(pause * SR), np.float32)]
    if not chunks:
        raise RuntimeError("testo vuoto: niente da leggere")
    sf.write(out_wav, np.concatenate(chunks), SR)
