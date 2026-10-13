"""Regia virtuale: inquadrature dalle immagini del personaggio, bagliore guidato dalla voce,
sottotitoli cinetici e chiusura con il logo. Tutto CPU (numpy + Pillow + ffmpeg/libass).

Il personaggio e' descritto da `personaggio.json` (nel dataset privato degli asset):
    {
      "logo": "logo.png",
      "intro": "intro/intro_pulita.wav",
      "enfasi": ["betforge", "numeri", ...],
      "inquadrature": {
        "9x16": [[immagine, cx, cy, altezza, zoom0, zoom1, deriva_x, "crop"|"fit"], ...],
        "16x9": [...],
        "1x1": [...]
      }
    }
Le prime due inquadrature sono dell'intro, l'ultima e' la chiusura, quelle in mezzo ruotano.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess

import numpy as np
from PIL import Image, ImageFilter

from studio.audio import load_pcm

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
GREEN = np.array([34, 197, 94], np.float32)      # #22c55e, verde BetForge
BG_DARK = np.array([8, 11, 16], np.float32)      # #080b10, sfondo dell'app
GREEN_ASS = "&H5EC522&"                          # stessi colori in BGR per ASS
GOLD_ASS = "&H2EB8F5&"                           # oro della corona, per "BetForge"
FONTS = [("Anton", 1.0), ("Bebas Neue", 1.18), ("Archivo Black", 0.82), ("Russo One", 0.85),
         ("Black Ops One", 0.85)]
SIZES = {"9x16": (720, 1280), "16x9": (1280, 720), "1x1": (1080, 1080)}
LOGO_WIDTH = {"9x16": 0.78, "16x9": 0.42, "1x1": 0.60}  # frazione della larghezza del video
END = 2.8  # secondi di chiusura con il logo


def load_character(assets_dir: str) -> dict:
    with open(os.path.join(assets_dir, "personaggio.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Logica pura (testata)
# ---------------------------------------------------------------------------

def assign_shots(n: int, plan: list) -> list:
    """Un'inquadratura per frase: intro (prime 2), rotazione delle centrali, chiusura (ultima)."""
    if n <= 2 or len(plan) < 3:
        return [plan[i % len(plan)] for i in range(n)]
    intro, middle, close = plan[:2], plan[2:-1] or plan[:1], plan[-1]
    return intro + [middle[i % len(middle)] for i in range(n - 3)] + [close]


def cut_times(phrases: list, duration: float) -> list:
    """Lo stacco cade poco prima dell'inizio di ogni frase."""
    return [0.0] + [max(p["inizio"] - 0.12, 0.0) for p in phrases[1:]] + [duration]


def _ass_time(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def build_ass(phrases: list, size: tuple, vertical: bool, emphasis: set, seed: int = 7) -> str:
    """Sottotitoli cinetici: gruppi di max 3 parole che entrano con movimento, font diverso a ogni
    gruppo, parole importanti piu' grandi e corte piu' piccole, parola detta verde con un "pop"."""
    rnd = random.Random(seed)
    W, H = size
    fs = round(66 * W / 720) if vertical else round(60 * H / 720)
    X, Y = W // 2, int(H * (0.70 if vertical else 0.80))
    head = [
        "[Script Info]", "ScriptType: v4.00+", "WrapStyle: 0", f"PlayResX: {W}", f"PlayResY: {H}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: S,Anton,{fs},&H00FFFFFF,&H00FFFFFF,&H00100B08,&H96000000,0,0,0,0,100,100,1,0,1,"
        f"{max(2, round(fs / 13))},3,5,{W // 14},{W // 14},0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def word_size(w, k_font):
        key = w.lower().strip(".,…!?;:")
        k = 1.38 if key in emphasis or len(key) >= 8 else (0.72 if len(key) <= 3 else 1.0)
        return int(fs * k * k_font)

    events, last_font = [], None
    for p in phrases:
        groups = [[]]
        for w in p["parole"]:
            groups[-1].append(w)
            if len(groups[-1]) == 3 or w["w"][-1:] in ".,…!?":
                groups.append([])
        for g in (g for g in groups if g):
            font, k_font = rnd.choice([f for f in FONTS if f[0] != last_font])
            last_font = font
            rot = rnd.uniform(-4, 4)
            dy = rnd.choice([30, -30, 0])            # entra dal basso, dall'alto o di lato
            dx = rnd.choice([-40, 40]) if dy == 0 else 0
            for j, w in enumerate(g):
                end = g[j + 1]["a"] if j + 1 < len(g) else w["b"] + 0.15
                parts = []
                for k, w2 in enumerate(g):
                    txt = w2["w"].upper().rstrip(",;:")   # stile social: niente virgole
                    size_px = word_size(w2["w"], k_font)
                    if k == j:  # parola detta: verde, "pop" di scala
                        parts.append(r"{\fs%d\c%s\fscx118\fscy118\t(0,110,\fscx100\fscy100)}%s{\fscx100\fscy100}"
                                     % (size_px, GREEN_ASS, txt))
                    else:
                        col = GOLD_ASS if "betforge" in w2["w"].lower() else "&HFFFFFF&"
                        parts.append(r"{\fs%d\c%s}%s" % (size_px, col, txt))
                tags = r"{\an5\fn%s\frz%.1f" % (font, rot)
                if j == 0:  # ingresso del gruppo
                    tags += r"\move(%d,%d,%d,%d,0,160)\fad(70,0)" % (X + dx, Y + dy, X, Y)
                else:
                    tags += r"\pos(%d,%d)" % (X, Y)
                events.append([w["a"], end, tags + "}" + " ".join(parts)])
    for e, nxt in zip(events, events[1:]):  # nessuna sovrapposizione
        e[1] = min(e[1], nxt[0])
    body = [f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},S,,0,0,0,,{t}" for a, b, t in events if b > a]
    return "\n".join(head + body) + "\n"


# ---------------------------------------------------------------------------
# Immagini
# ---------------------------------------------------------------------------

def _prepare(path: str) -> dict:
    """Immagine + mappa delle parti verdi luminose (linea sulla maschera, circuiti) per il bagliore."""
    src = Image.open(path).convert("RGB")
    a = np.asarray(src).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = Image.fromarray((((g > 1.35 * r) & (g > 1.35 * b) & (g > 80)) * 255).astype(np.uint8))
    glow = np.asarray(mask.filter(ImageFilter.GaussianBlur(6))).astype(np.float32)[..., None] / 255
    core = np.asarray(mask.filter(ImageFilter.GaussianBlur(1.5))).astype(np.float32)[..., None] / 255
    return {"src": src, "a": a, "light": (glow * 0.6 + core * 0.4) * GREEN, "size": src.size}


def _load_logo(path: str):
    """Logo su sfondo nero -> RGBA (alfa dalla luminosita'), ritagliato sul contenuto."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    alpha = np.clip((a.max(axis=2) - 18) / 70, 0, 1)
    im = Image.fromarray(np.dstack([a, alpha * 255]).astype(np.uint8), "RGBA")
    return im.crop(im.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox())


def _ease(x: float) -> float:
    return x * x * (3 - 2 * x)


class Renderer:
    def __init__(self, assets_dir: str, character: dict, fmt: str, size: tuple = None, glow: float = 1.0):
        self.fmt, self.glow = fmt, glow
        self.size = size or SIZES[fmt]
        self.ar = self.size[0] / self.size[1]
        self.plan = character["inquadrature"][fmt]
        # le coordinate in personaggio.json sono in pixel delle immagini originali
        self.imgs = {name: _prepare(os.path.join(assets_dir, name)) for name in {s[0] for s in self.plan}}
        self.bg_cache = {}
        logo_path = os.path.join(assets_dir, character.get("logo", "logo.png"))
        self.logo = self.logo_glow = None
        if os.path.exists(logo_path):
            lg = _load_logo(logo_path)
            lw = int(self.size[0] * LOGO_WIDTH[fmt])
            self.logo = lg.resize((lw, int(lw * lg.height / lg.width)), Image.LANCZOS)
            g = np.asarray(self.logo.getchannel("A").filter(ImageFilter.GaussianBlur(22))).astype(np.float32)
            self.logo_glow = (g / 255)[..., None] * GREEN

    def shot_frame(self, shot, p: float, t: float, k: float) -> Image.Image:
        name, cx0, cy, hh, z0, z1, dx, mode = shot
        im = self.imgs[name]
        SW, SH = im["size"]
        W, H = self.size
        lit = Image.fromarray(np.clip(im["a"] + im["light"] * k, 0, 255).astype(np.uint8))
        z = (z0 + (z1 - z0) * p) * (1 + 0.006 * math.sin(2 * math.pi * t / 4.0))  # micro-respiro
        if mode == "fit":  # immagine larga dentro il verticale, su una copia sfocata di se'
            if name not in self.bg_cache:
                w = SH * self.ar
                bg = im["src"].crop(((SW - w) / 2, 0, (SW + w) / 2, SH)).resize(self.size, Image.BICUBIC)
                self.bg_cache[name] = Image.eval(bg.filter(ImageFilter.GaussianBlur(28)), lambda v: int(v * 0.45))
            canvas = self.bg_cache[name].copy()
            fw = int(W * z)
            fh = int(fw * SH / SW)
            canvas.paste(lit.resize((fw, fh), Image.BICUBIC), ((W - fw) // 2, int(H * 0.40 - fh / 2)))
            return canvas
        h = min(hh / z, SH)
        w = h * self.ar
        if w > SW:
            w, h = SW, SW / self.ar
        cx = cx0 + dx * (p - 0.5)
        x0 = min(max(cx - w / 2, 0), SW - w)
        y0 = min(max(cy - h / 2, 0), SH - h)
        return lit.transform(self.size, Image.EXTENT, (x0, y0, x0 + w, y0 + h), Image.BICUBIC)

    def end_card(self, te: float, last: Image.Image) -> Image.Image:
        """Sfondo scuro BetForge, logo che cresce e appare, alone verde che "respira"."""
        W, H = self.size
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / W) ** 2 + ((yy - H * 0.45) / H) ** 2)
        base = BG_DARK + (1 - np.clip(r * 2.2, 0, 1))[..., None] * np.array([10, 30, 16], np.float32)
        q = min(te / 0.7, 1.0)
        sc = 0.82 + 0.18 * (1 - (1 - q) ** 3)
        fade = min(te / 0.45, 1.0)
        lg = self.logo.resize((int(self.logo.width * sc), int(self.logo.height * sc)), Image.LANCZOS)
        cx, cy = (W - lg.width) // 2, int(H * 0.45 - lg.height / 2)
        gl = np.asarray(Image.fromarray(np.clip(self.logo_glow, 0, 255).astype(np.uint8)).resize(lg.size))
        gl = gl.astype(np.float32) * (0.35 + 0.25 * math.sin(2 * math.pi * te / 1.6)) * fade
        y0, x0 = max(cy, 0), max(cx, 0)
        base[y0:y0 + gl.shape[0], x0:x0 + gl.shape[1]] += gl[:H - y0, :W - x0]
        img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
        img.paste(lg.convert("RGB"), (cx, cy), lg.getchannel("A").point(lambda v: int(v * fade)))
        if te < 0.35 and last is not None:  # dissolvenza dall'ultima inquadratura
            img = Image.blend(last, img, te / 0.35)
        return img

    def render(self, voice_wav: str, phrases: list, out_path: str, emphasis: set, fps: int = 30,
               subtitles: bool = True):
        """subtitles=False: niente sottotitoli ASS (li disegna l'overlay HyperFrames, vedi grafica.py)."""
        wav = load_pcm(voice_wav)
        dur = len(wav) / 48000
        hop = 48000 // fps
        n_env = max(int(np.ceil(len(wav) / hop)), 1)  # nessuna fetta vuota (darebbe NaN)
        env = np.array([np.sqrt(np.mean(wav[i * hop:(i + 1) * hop] ** 2) + 1e-9) for i in range(n_env)])
        env = np.convolve(env / (np.percentile(env, 95) + 1e-9), np.ones(3) / 3, "same").clip(0, 1.2)

        shots = assign_shots(len(phrases), self.plan)
        cuts = cut_times(phrases, dur)
        ass_path = out_path + ".ass"
        vf = []
        if subtitles:
            with open(ass_path, "w", encoding="utf-8") as f:
                # il quadrato impagina i sottotitoli come il verticale (in proporzione alla larghezza)
                f.write(build_ass(phrases, self.size, self.fmt != "16x9", emphasis))
            vf = ["-vf", ass_filter(ass_path)]
        total = dur + (END if self.logo is not None else 0)
        W, H = self.size
        ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
             "-i", voice_wav,
             *vf,
             "-af", "apad", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        last = None
        try:
            for f in range(int(total * fps)):
                t = f / fps
                if t >= dur:
                    frame = self.end_card(t - dur, last)
                else:
                    s = max(i for i in range(len(shots)) if cuts[i] <= t) if shots else 0
                    p = _ease((t - cuts[s]) / max(cuts[s + 1] - cuts[s], 1e-3))
                    k = self.glow * (0.12 + 0.05 * math.sin(2 * math.pi * 0.25 * t)
                                     + 0.5 * env[min(f, len(env) - 1)])
                    frame = last = self.shot_frame(shots[s], p, t, k)
                ff.stdin.write(frame.tobytes())
            ff.stdin.close()
        except BrokenPipeError:
            pass
        err = ff.stderr.read().decode(errors="replace")
        if ff.wait() != 0:
            raise RuntimeError(f"ffmpeg (regia) fallito: {err[-600:]}")
        if subtitles:
            os.remove(ass_path)
        return total

    def burn_subtitles(self, video: str, phrases: list, out_path: str, emphasis: set):
        """Riserva: imprime i sottotitoli ASS su un video gia' montato senza sottotitoli."""
        ass_path = out_path + ".ass"
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(build_ass(phrases, self.size, self.fmt != "16x9", emphasis))
        p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vf", ass_filter(ass_path),
                            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                            "-c:a", "copy", "-movflags", "+faststart", out_path], capture_output=True, text=True)
        os.remove(ass_path)
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg (sottotitoli) fallito: {p.stderr[-600:]}")


def ass_filter(ass_path: str) -> str:
    esc = lambda p: p.replace("\\", "/").replace(":", r"\:")  # noqa: E731 - filtri ffmpeg su Windows
    return f"ass='{esc(ass_path)}':fontsdir='{esc(FONTS_DIR)}'"
