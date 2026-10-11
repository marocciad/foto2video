"""Audio dello studio: pulizia del vocale, "voce 3", volume per i social, tempi delle parole.

Lezioni dal collaudo (2026-10-11), da non perdere:
- per abbassare il tono niente `atempo` (parlato a scatti): rubberband cambia il tono e non il ritmo;
- niente flanger: sulle code delle parole fa un fruscio;
- niente compressore rapido: "afferra" le plosive (la B di Benvenuti). Va bene solo con attack lento;
- l'audio si ripulisce UNA volta: la doppia afftdn lascia artefatti;
- whisper: niente initial_prompt con il testo (lo salta), meglio hotwords.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess

import numpy as np

SR = 48000

CLEAN = ("highpass=f=80,afftdn=nf=-25,agate=threshold=0.008:ratio=3:attack=5:release=180,"
         "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
         "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.25,areverse")
VOICE3 = ("rubberband=pitch=0.80:formant=shifted,aecho=0.85:0.6:6:0.25,highpass=f=140,lowpass=f=6500,"
          "acompressor=threshold=-30dB:ratio=3:attack=25:release=200:makeup=3")
LIMITER = "alimiter=limit=0.84:attack=5:release=80:level=false"


def run(cmd: list):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} fallito: {p.stderr[-600:]}")
    return p


def clean(src: str, dst: str):
    """Mono 48 kHz, riduzione rumore, gate tra le parole, silenzi tagliati ai bordi."""
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", str(SR), "-af", CLEAN, dst])


def concat(paths: list, dst: str, gap: float = 0.8):
    """Unisce le clip con `gap` secondi di silenzio tra l'una e l'altra."""
    inputs, parts = [], []
    for i, p in enumerate(paths):
        inputs += ["-i", p]
        parts.append(f"[{i}:a]aformat=sample_rates={SR}:channel_layouts=mono[a{i}]")
    chain, labels = [], []
    for i in range(len(paths)):
        labels.append(f"[a{i}]")
        if i < len(paths) - 1:
            chain.append(f"aevalsrc=0:d={gap}:s={SR}[g{i}]")
            labels.append(f"[g{i}]")
    graph = ";".join(parts + chain) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]", dst])


def measure_lufs(path: str) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float([line for line in err.splitlines() if line.strip().startswith("I:")][-1].split()[1])


def voice3(src: str, dst: str, lufs: float = -13.0):
    """Voce camuffata "metallica" + volume da social.

    loudnorm si ferma prima del target per rispettare i picchi: il guadagno che manca lo da'
    un limitatore con anticipo (pochi dB sui soli picchi, trasparente).
    """
    tmp = dst + ".tmp.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", f"{VOICE3},loudnorm=I={lufs}:TP=-1.5:LRA=9",
         "-ar", str(SR), tmp])
    gain = lufs - measure_lufs(tmp)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-af", f"volume={gain:.2f}dB,{LIMITER}",
         "-ar", str(SR), dst])
    os.remove(tmp)


def load_pcm(path: str, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


# ---------------------------------------------------------------------------
# Parole e tempi
# ---------------------------------------------------------------------------

def transcribe(path: str, model_name: str = "small") -> list:
    """[{"w": parola, "a": inizio, "b": fine}] con faster-whisper (import pigro: i test non lo richiedono)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    # audio decodificato da ffmpeg: la decodifica interna di faster-whisper (PyAV) cambia API spesso
    segs, _ = model.transcribe(load_pcm(path, 16000), language="it", word_timestamps=True,
                               vad_filter=False, hotwords="BetForge")
    return fix_brand([{"w": w.word.strip(), "a": round(w.start, 3), "b": round(w.end, 3)}
                      for s in segs for w in s.words])


def fix_brand(words: list) -> list:
    """"Bet Forge", "betforg", "Bet" + "Forge" -> "BetForge"."""
    out = []
    for w in words:
        text = re.sub(r"(?i)\bbet\s*-?\s*f[oò]r(ge|g|dge|ch)\b", "BetForge", w["w"])
        if out and out[-1]["w"].lower() == "bet" and text.lower().startswith("forg"):
            out[-1] = {"w": "BetForge" + re.sub(r"(?i)^forg\w*", "", text), "a": out[-1]["a"], "b": w["b"]}
        else:
            out.append({**w, "w": text})
    return out


def _norm(t: str) -> str:
    return re.sub(r"[^\w]", "", t.lower())


def align_script(script: str, words: list) -> list:
    """Il testo viene dal copione, i tempi da whisper.

    Allineamento parola per parola con difflib. Le sostituzioni ("matematico" per "matematica")
    prendono i tempi delle parole riconosciute al loro posto; le parole senza corrispondenza
    prendono tempi interpolati tra le vicine.
    """
    script_words = script.split()
    if not script_words:
        return words
    sm = difflib.SequenceMatcher(a=[_norm(w) for w in script_words], b=[_norm(w["w"]) for w in words],
                                 autojunk=False)
    times = [None] * len(script_words)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (words[j1 + k]["a"], words[j1 + k]["b"])
        elif op == "replace":
            a0, b0 = words[j1]["a"], words[j2 - 1]["b"]
            n = i2 - i1
            for k in range(n):
                times[i1 + k] = (a0 + (b0 - a0) * k / n, a0 + (b0 - a0) * (k + 1) / n)
    known = [i for i, t in enumerate(times) if t]
    for i, t in enumerate(times):
        if t is None:
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            a0 = times[prev][1] if prev is not None else 0.0
            b0 = times[nxt][0] if nxt is not None else a0 + 0.4
            times[i] = (a0, max(b0, a0 + 0.15))
    return [{"w": w, "a": round(t[0], 3), "b": round(t[1], 3)} for w, t in zip(script_words, times)]


def split_phrases(words: list, pause: float = 0.35, max_words: int = 9) -> list:
    """Frasi = un'inquadratura ciascuna. Si spezza su pausa, punto, virgola (dopo 3 parole) o lunghezza."""
    phrases, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end = w["w"][-1:]
        if (nxt is None or nxt["a"] - w["b"] > pause or end in ".?!…"
                or (end in ",;:" and len(cur) >= 3) or len(cur) >= max_words):
            phrases.append({"testo": " ".join(x["w"] for x in cur), "inizio": cur[0]["a"],
                            "fine": cur[-1]["b"], "parole": cur})
            cur = []
    return phrases


def save_json(obj, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
