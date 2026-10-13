"""Incolla nell'immagine originale SOLO la zona delle mani animata da Wan (resto fermo al pixel).

    python prototipi/mani/componi.py <immagine.png> <wan.mp4> <uscita.mp4> [cx cy rx ry]

L'ellisse (cx, cy, rx, ry) e' la zona delle mani in pixel dell'immagine originale.
Il loop e' "avanti e indietro", cosi' si ripete senza scatti.
"""

import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CROP = (359, 397, 1319, 937)  # deve coincidere con genera_mani.py


def read_frames(path):
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h = map(int, probe.stdout.strip().split(","))
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3), (w, h)


def main():
    img_path, wan_path, out_path = sys.argv[1:4]
    cx, cy, rx, ry = (int(v) for v in sys.argv[4:8]) if len(sys.argv) > 7 else (835, 725, 270, 185)
    base = Image.open(img_path).convert("RGB")
    frames, (fw, fh) = read_frames(wan_path)

    # Wan ha adattato il ritaglio (ImageOps.fit, centrato) a fw x fh: zona corrispondente nell'originale
    x0, y0, x1, y1 = CROP
    cw, ch = x1 - x0, y1 - y0
    scale = min(cw / fw, ch / fh)
    vis_w, vis_h = fw * scale, fh * scale
    rx0, ry0 = x0 + (cw - vis_w) / 2, y0 + (ch - vis_h) / 2
    box = (int(round(rx0)), int(round(ry0)), int(round(rx0 + vis_w)), int(round(ry0 + vis_h)))
    size = (box[2] - box[0], box[3] - box[1])

    # maschera morbida delle mani, nelle coordinate della zona
    mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(28)).crop(box)
    m = np.asarray(mask).astype(np.float32)[..., None] / 255

    orig = np.asarray(base.crop(box)).astype(np.float32)
    first = np.asarray(Image.fromarray(frames[0]).resize(size, Image.LANCZOS)).astype(np.float32)
    # correzione colore (media/deviazione per canale) calcolata sul fotogramma 0, uguale per tutti
    w8 = m[..., 0] > 0.05
    gain = orig[w8].std(0) / (first[w8].std(0) + 1e-6)
    off = orig[w8].mean(0) - first[w8].mean(0) * gain

    seq = list(range(len(frames))) + list(range(len(frames) - 2, 0, -1))  # avanti e indietro
    out_w, out_h = 1280, int(1280 * base.height / base.width) // 2 * 2
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{out_w}x{out_h}", "-r", "24", "-i", "-", "-c:v", "libx264", "-crf", "16",
                           "-pix_fmt", "yuv420p", out_path], stdin=subprocess.PIPE)
    full = np.asarray(base).astype(np.float32)
    for i in seq:
        f = np.asarray(Image.fromarray(frames[i]).resize(size, Image.LANCZOS)).astype(np.float32) * gain + off
        region = orig * (1 - m) + f * m
        frame = full.copy()
        frame[box[1]:box[3], box[0]:box[2]] = region
        im = Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8)).resize((out_w, out_h), Image.LANCZOS)
        ff.stdin.write(im.tobytes())
    ff.stdin.close()
    ff.wait()
    print(out_path, f"{len(seq) / 24:.1f}s", "zona", box, "colore gain", np.round(gain, 2))


if __name__ == "__main__":
    main()
