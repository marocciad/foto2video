"""Prototipo completo: voce + stacchi sulle frasi + bagliore guidato dalla voce + sottotitoli social.

uso: python regia_v3.py <cartella_immagini> <voce.wav> <tempi.json> <out.mp4> <16x9|9x16> [glow]
"""
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageFilter

folder, voice, timing_path, out_path, fmt = sys.argv[1:6]
GLOW = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 30
OUT = (1280, 720) if fmt == "16x9" else (720, 1280)
AR = OUT[0] / OUT[1]
GREEN = np.array([34, 197, 94], np.float32)  # #22c55e

# --- audio: durata e inviluppo (per il bagliore) ---------------------------------
pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", voice, "-ac", "1", "-ar", "48000",
                      "-f", "f32le", "-"], capture_output=True, check=True).stdout
wav = np.frombuffer(pcm, np.float32)
dur = len(wav) / 48000
hop = 48000 // FPS
env = np.array([np.sqrt(np.mean(wav[i * hop:(i + 1) * hop] ** 2) + 1e-9) for i in range(int(dur * FPS) + 1)])
env = np.convolve(env / (np.percentile(env, 95) + 1e-9), np.ones(3) / 3, "same").clip(0, 1.2)

# --- immagini --------------------------------------------------------------------
def prepare(name):
    src = Image.open(os.path.join(folder, name)).convert("RGB")
    a = np.asarray(src).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    gm = Image.fromarray((((g > 1.35 * r) & (g > 1.35 * b) & (g > 80)) * 255).astype(np.uint8))
    glow = np.asarray(gm.filter(ImageFilter.GaussianBlur(6))).astype(np.float32)[..., None] / 255
    core = np.asarray(gm.filter(ImageFilter.GaussianBlur(1.5))).astype(np.float32)[..., None] / 255
    return dict(src=src, a=a, light=(glow * 0.6 + core * 0.4) * GREEN, size=src.size)


L, F, PS, PD = "01_campo_lungo.png", "02_frontale.png", "03_profilo_verso_sx.png", "04_profilo_verso_dx.png"
imgs = {n: prepare(n) for n in (L, F, PS, PD)}

# Un'inquadratura per frase: (immagine, cx, cy, altezza ritaglio, zoom0, zoom1, deriva x, modo)
# modo "fit" = immagine 16:9 intera dentro il 9:16, con sfondo sfocato (per il logo)
if fmt == "16x9":
    plan = [(L, 836, 470, 941, 1.00, 1.12, 0, "crop"),     # Benvenuti… (pausa sospesa)
            (L, 836, 230, 420, 1.00, 1.06, 0, "crop"),     # questo è BetForge (logo)
            (F, 839, 520, 620, 1.00, 1.06, -20, "crop"),
            (F, 839, 480, 300, 1.00, 1.08, 0, "crop"),     # Io li guardo tutti: primo piano
            (PS, 450, 760, 506, 1.00, 1.06, 0, "crop"),
            (F, 839, 468, 937, 1.00, 1.05, 0, "crop"),
            (PD, 450, 780, 506, 1.04, 1.00, 0, "crop"),
            (L, 836, 470, 941, 1.12, 1.00, 0, "crop")]     # chiusura
else:
    plan = [(L, 836, 470, 941, 1.00, 1.08, 0, "crop"),
            (L, 836, 470, 941, 1.00, 1.05, 0, "fit"),
            (F, 839, 520, 800, 1.00, 1.06, -10, "crop"),
            (F, 839, 480, 420, 1.00, 1.08, 0, "crop"),
            (PS, 450, 800, 1599, 1.00, 1.12, 0, "crop"),
            (F, 839, 468, 937, 1.00, 1.05, 0, "crop"),
            (PD, 450, 800, 1599, 1.10, 1.00, 0, "crop"),
            (L, 836, 470, 941, 1.00, 1.05, 0, "fit")]

phrases = json.load(open(timing_path, encoding="utf-8"))
if len(phrases) != len(plan):
    # intro (campo lungo, logo) + rotazione delle inquadrature centrali + chiusura
    intro, middle, close = plan[:2], plan[2:-1], plan[-1]
    n_mid = max(len(phrases) - 3, 0)
    plan = (intro + [middle[i % len(middle)] for i in range(n_mid)] + [close])[:len(phrases)]
    if len(phrases) < 3:
        plan = intro[:len(phrases)]
# lo stacco cade poco prima che inizi la frase
cuts = [0.0] + [max(p["inizio"] - 0.12, 0) for p in phrases[1:]] + [dur]

# --- sottotitoli ASS: gruppi di parole, parola corrente in verde e piu' grande ----
def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def words_with_times(p):
    if p.get("parole"):  # tempi veri dalla trascrizione
        return [(w["w"], w["a"], w["b"]) for w in p["parole"]]
    words = p["testo"].replace("...", "…").split()
    weights = [max(len(w.strip(".,…!?")), 2) + (6 if w[-1] in ".,…!?" else 0) for w in words]
    total, t, out = sum(weights), p["inizio"], []
    for w, k in zip(words, weights):
        d = (p["fine"] - p["inizio"]) * k / total
        out.append((w, t, t + d))
        t += d
    return out


fs, mv = (64, 300) if fmt == "9x16" else (58, 90)
ass = [
    "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {OUT[0]}", f"PlayResY: {OUT[1]}", "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    f"Style: S,Anton,{fs},&H00FFFFFF,&H00FFFFFF,&H00100B08,&H96000000,0,0,0,0,100,100,1,0,1,5,2,2,40,40,{mv},1",
    "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
]
GREEN_ASS = "&H005EC522&"  # #22c55e in BGR
events = []
for p in phrases:
    ws = words_with_times(p)
    groups = [[]]  # max 3 parole a schermo, e si va a capo dopo la punteggiatura
    for item in ws:
        groups[-1].append(item)
        if len(groups[-1]) == 3 or item[0][-1] in ".,…!?":
            groups.append([])
    groups = [g for g in groups if g]
    for g in groups:
        for j, (w, t0, t1) in enumerate(g):
            end = g[j + 1][1] if j + 1 < len(g) else t1 + 0.15
            parts = []
            for k, (w2, _, _) in enumerate(g):
                txt = w2.upper().rstrip(",;:")  # stile social: niente virgole
                if k == j:  # parola detta: verde, "pop" di scala
                    parts.append(r"{\c" + GREEN_ASS + r"\fscx112\fscy112\t(0,90,\fscx100\fscy100)}" + txt + r"{\c&HFFFFFF&}")
                else:
                    parts.append(txt)
            events.append([t0, end, " ".join(parts)])
# nessuna sovrapposizione: ogni evento finisce quando inizia il successivo
for e, nxt in zip(events, events[1:]):
    e[1] = min(e[1], nxt[0])
ass += [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},S,,0,0,0,,{t}" for a, b, t in events]
ass_path = out_path + ".ass"
open(ass_path, "w", encoding="utf-8").write("\n".join(ass) + "\n")

# --- render -----------------------------------------------------------------------
ease = lambda x: x * x * (3 - 2 * x)  # noqa: E731
bg_cache = {}


def frame_for(shot, p, t, k):
    name, cx0, cy, hh, z0, z1, dx, mode = shot
    im = imgs[name]
    SW, SH = im["size"]
    lit = Image.fromarray(np.clip(im["a"] + im["light"] * k, 0, 255).astype(np.uint8))
    z = (z0 + (z1 - z0) * p) * (1 + 0.006 * math.sin(2 * math.pi * t / 4.0))
    if mode == "fit":
        if name not in bg_cache:  # sfondo: stessa immagine, ingrandita e sfocata, scurita
            h = SH
            w = h * AR
            bg = im["src"].crop(((SW - w) / 2, 0, (SW + w) / 2, h)).resize(OUT, Image.BICUBIC)
            bg_cache[name] = Image.eval(bg.filter(ImageFilter.GaussianBlur(28)), lambda v: int(v * 0.45))
        canvas = bg_cache[name].copy()
        fw = int(OUT[0] * z)
        fh = int(fw * SH / SW)
        fg = lit.resize((fw, fh), Image.BICUBIC)
        canvas.paste(fg, ((OUT[0] - fw) // 2, int(OUT[1] * 0.40 - fh / 2)))
        return canvas
    h = min(hh / z, SH)
    w = h * AR
    if w > SW:
        w, h = SW, SW / AR
    cx = cx0 + dx * (p - 0.5)
    x0 = min(max(cx - w / 2, 0), SW - w)
    y0 = min(max(cy - h / 2, 0), SH - h)
    return lit.transform(OUT, Image.EXTENT, (x0, y0, x0 + w, y0 + h), Image.BICUBIC)


ass_arg = ass_path.replace("\\", "/").replace(":", r"\:")
fonts_arg = os.path.join(HERE, "fonts").replace("\\", "/").replace(":", r"\:")
ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error",
                       "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OUT[0]}x{OUT[1]}", "-r", str(FPS), "-i", "-",
                       "-i", voice,
                       "-vf", f"ass='{ass_arg}':fontsdir='{fonts_arg}'",
                       "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out_path],
                      stdin=subprocess.PIPE)
n = int(dur * FPS)
for f in range(n):
    t = f / FPS
    s = max(i for i in range(len(plan)) if cuts[i] <= t)
    p = ease((t - cuts[s]) / max(cuts[s + 1] - cuts[s], 1e-3))
    k = GLOW * (0.12 + 0.05 * math.sin(2 * math.pi * 0.25 * t) + 0.5 * env[min(f, len(env) - 1)])
    ff.stdin.write(frame_for(plan[s], p, t, k).tobytes())
ff.stdin.close()
ff.wait()
print(out_path, f"{dur:.1f}s", "ok" if ff.returncode == 0 else "ERRORE")
