"""Vocali Telegram -> voce metallica + tempi delle parole (per regia_v3.py).

uso: python vocale.py <secrets.json> <cartella_lavoro>
Legge i vocali in coda SENZA confermarli. Il primo vocale = intro, gli altri = contenuto.
Scrive: <cartella>/voce.wav (con effetto) e <cartella>/tempi.json (frasi con parole e tempi).
"""
import json
import os
import re
import subprocess
import sys

import numpy as np
import requests
from faster_whisper import WhisperModel

secrets_path, work = sys.argv[1], sys.argv[2]
os.makedirs(work, exist_ok=True)
cfg = json.load(open(secrets_path, encoding="utf-8"))
base = f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}"
allowed = set(cfg["ALLOWED_USER_IDS"])
SR = 48000

LOCAL = sys.argv[3:]  # file audio locali al posto dei vocali Telegram (per collaudo)
ups = [] if LOCAL else requests.get(f"{base}/getUpdates", params={"timeout": 0}, timeout=30).json()["result"]
voices = [u["message"] for u in ups
          if u.get("message", {}).get("from", {}).get("id") in allowed
          and (u["message"].get("voice") or u["message"].get("audio"))]
print(f"vocali in coda: {len(voices)}")
if LOCAL:
    voices = [{"local": f} for f in LOCAL]
INTRO = os.environ.get("INTRO")  # intro gia' registrata: si usa solo l'ultimo vocale come contenuto
if INTRO:
    voices = [{"local": INTRO, "gia_pulita": True}] + voices[-1:]
if not voices:
    sys.exit("nessun vocale")


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


clips = []
for i, m in enumerate(voices):
    v = m.get("voice") or m.get("audio") or {}
    raw = m.get("local")
    if not raw:
        path = requests.get(f"{base}/getFile", params={"file_id": v["file_id"]}, timeout=30).json()["result"]["file_path"]
        raw = os.path.join(work, f"clip{i}.ogg")
        open(raw, "wb").write(requests.get(f"https://api.telegram.org/file/bot{cfg['TELEGRAM_TOKEN']}/{path}", timeout=120).content)
    clean = os.path.join(work, f"clip{i}.wav")
    if m.get("gia_pulita"):
        clips.append(raw)
        continue
    # mono 48 kHz, riduzione rumore, taglio dei silenzi a inizio e fine
    run(["ffmpeg", "-y", "-i", raw, "-ac", "1", "-ar", str(SR), "-af",
         "highpass=f=80,afftdn=nf=-25,agate=threshold=0.008:ratio=3:attack=5:release=180,"
         "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
         "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.25,areverse",
         clean])
    clips.append(clean)
    print(f"clip {i}: {v.get('duration')} s")

# intro + 0.8 s + contenuto
gap = os.path.join(work, "gap.wav")
run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", "0.8", gap])
lst = os.path.join(work, "lista.txt")
seq = [clips[0]] + sum(([gap, c] for c in clips[1:]), [])
open(lst, "w", encoding="utf-8").write("".join(f"file '{os.path.abspath(p)}'\n" for p in seq))
dry = os.path.join(work, "voce_pulita.wav")
run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dry])

# Voce 3: tono piu' grave senza cambiare ritmo (rubberband) + eco cortissima "metallica".
# Niente flanger: sulle code delle parole faceva un fruscio ("whoosh") udibile.
# Compressore LENTO (attack 25 ms): lascia passare le plosive (la B di Benvenuti) senza afferrarle.
# loudnorm in modalita' dinamica, con limitatore dei picchi integrato.
VOICE3 = ("rubberband=pitch=0.80:formant=shifted,aecho=0.85:0.6:6:0.25,highpass=f=140,lowpass=f=6500,"
          "acompressor=threshold=-30dB:ratio=3:attack=25:release=200:makeup=3")


def measure_lufs(path):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float([l for l in err.splitlines() if l.strip().startswith("I:")][-1].split()[1])


def voice3(src, dst, lufs=-13.0):
    tmp = dst + ".tmp.wav"
    run(["ffmpeg", "-y", "-i", src, "-af", f"{VOICE3},loudnorm=I={lufs}:TP=-1.5:LRA=9", "-ar", str(SR), tmp])
    # loudnorm si ferma prima del target per rispettare i picchi: il guadagno mancante lo da' un
    # limitatore con anticipo (qualche dB sui soli picchi, trasparente)
    gain = lufs - measure_lufs(tmp)
    run(["ffmpeg", "-y", "-i", tmp, "-af", f"volume={gain:.2f}dB,alimiter=limit=0.84:attack=5:release=80:level=false",
         "-ar", str(SR), dst])
    os.remove(tmp)


voice3(dry, os.path.join(work, "voce.wav"))

# trascrizione con tempi per parola (sulla voce pulita: riconosce meglio)
words_path = os.path.join(work, "parole.json")
if os.environ.get("RIUSA_TRASCRIZIONE") and os.path.exists(words_path):
    words = json.load(open(words_path, encoding="utf-8"))
else:
    model = WhisperModel(os.environ.get("WHISPER_MODEL", "medium"), device="cpu", compute_type="int8")
    # audio decodificato con ffmpeg (16 kHz mono float): evita la decodifica interna di faster-whisper
    pcm16 = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", dry, "-ac", "1", "-ar", "16000",
                            "-f", "f32le", "-"], capture_output=True, check=True).stdout
    segs, _ = model.transcribe(np.frombuffer(pcm16, np.float32), language="it", word_timestamps=True,
                               vad_filter=False, hotwords="BetForge")
    words = [{"w": w.word.strip(), "a": round(w.start, 3), "b": round(w.end, 3)} for sg in segs for w in sg.words]
    json.dump(words, open(words_path, "w", encoding="utf-8"), ensure_ascii=False)
fix = lambda t: re.sub(r"(?i)\bbet\s*-?\s*f[oò]r(ge|g|dge|ch)\b", "BetForge", t)  # noqa: E731
for w in words:
    w["w"] = fix(w["w"])
# "Bet" + "Forge" separati -> una parola
merged = []
for w in words:
    if merged and merged[-1]["w"].lower() == "bet" and w["w"].lower().startswith("forg"):
        merged[-1] = {"w": "BetForge" + re.sub(r"(?i)^forg\w*", "", w["w"]), "a": merged[-1]["a"], "b": w["b"]}
    else:
        merged.append(w)
words = merged

# frasi: si spezza su pausa > 0.45 s o punteggiatura forte
phrases, cur = [], []
for i, w in enumerate(words):
    cur.append(w)
    nxt = words[i + 1] if i + 1 < len(words) else None
    end = w["w"][-1:]
    if (nxt is None or nxt["a"] - w["b"] > 0.35 or end in ".?!…"
            or (end in ",;:" and len(cur) >= 3) or len(cur) >= 9):
        phrases.append({"testo": " ".join(x["w"] for x in cur), "inizio": cur[0]["a"], "fine": cur[-1]["b"],
                        "parole": cur})
        cur = []
json.dump(phrases, open(os.path.join(work, "tempi.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for p in phrases:
    print(f"{p['inizio']:6.2f}-{p['fine']:6.2f}  {p['testo']}")
