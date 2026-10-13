"""Esperimento "mani che si muovono": Wan anima un ritaglio attorno alle mani del personaggio.

Gira su Kaggle (GPU T4), una volta sola per immagine: i loop delle mani non dipendono da cosa
dice il personaggio, quindi poi lo studio li incolla nei video su CPU, senza GPU.

    kaggle kernels push -p prototipi/mani      (poi: kaggle kernels output ... -p out/)

Usa il WanBackend del worker, scaricato dal repo pubblico (stesso codice gia' collaudato).
"""

import glob
import os
import subprocess
import sys
import time
import urllib.request
import zipfile

W = "/kaggle/working"
REPO_RAW = "https://raw.githubusercontent.com/marocciad/foto2video/main/worker/worker.py"

# Ritaglio 16:9 attorno a mani e busto in 02_frontale.png (1678x937): le mani stanno circa in
# x 600-1080, y 580-900. Lavorare sul ritaglio da' a Wan piu' pixel sulle mani.
CROP = (359, 397, 1319, 937)

NEGATIVE = ("camera movement, zoom, pan, head movement, mask changing, facial expression, smiling, "
            "mouth moving, armor changing, body moving, standing up, extra fingers, deformed hands")
PROMPTS = [
    "A masked hooded figure in black armor sits perfectly still at a desk, hands clasped in front of "
    "the chest. Only the fingers move: they slowly and gently flex and tap against each other. Head, "
    "mask, shoulders and body stay completely still. Static locked-off camera, cinematic lighting.",
    "A masked hooded figure in black armor sits perfectly still at a desk. While speaking calmly, the "
    "clasped hands slowly open a little in a subtle explaining gesture, then come back together. Head, "
    "mask and body stay completely still. Static locked-off camera, cinematic lighting.",
]


def find_image() -> str:
    hits = glob.glob("/kaggle/input/**/02_frontale.png", recursive=True)
    if hits:
        return hits[0]
    for z in glob.glob("/kaggle/input/**/assets.zip", recursive=True):
        zipfile.ZipFile(z).extractall(f"{W}/assets")
        return glob.glob(f"{W}/assets/**/02_frontale.png", recursive=True)[0]
    sys.exit("02_frontale.png non trovata negli asset")


def main():
    urllib.request.urlretrieve(REPO_RAW, f"{W}/worker_src.py")
    sys.path.insert(0, W)
    import worker_src as w

    w.ensure_deps("wan")
    from PIL import Image

    crop_path = f"{W}/mani_input.png"
    Image.open(find_image()).convert("RGB").crop(CROP).save(crop_path)

    # export ad alta qualita' (il compositing ritaglia e ingrandisce: niente compressione forte)
    def finalize_hq(src, dst, max_mb=48.0):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-c:v", "libx264", "-crf", "12",
                        "-pix_fmt", "yuv420p", dst], check=True)
    w.finalize_mp4 = finalize_hq

    cfg = dict(w.DEFAULTS)
    # 121 frame vanno in memoria esaurita sulla T4 (16 GB); 73 e' il valore collaudato
    cfg.update(WAN_FRAMES=73, WAN_STEPS=30, NEGATIVE_EXTRA=NEGATIVE, CHARACTER_PROMPT="")
    backend = w.WanBackend(cfg)
    t = time.time()
    backend.load(print)
    print(f"[mani] modello caricato in {(time.time() - t) / 60:.1f} min")
    for i, prompt in enumerate(PROMPTS):
        t = time.time()
        job = w.Job(update_id=0, chat_id=0, user_id=0, message_id=0, file_id="", prompt=prompt)
        last = {"p": -1}

        def progress(frac):
            p = int(frac * 10)
            if p != last["p"]:
                last["p"] = p
                print(f"[mani {i}] {p * 10}%")
        backend.generate(crop_path, job, f"{W}/mani_{i}.mp4", progress)
        print(f"[mani {i}] fatto in {(time.time() - t) / 60:.1f} min")


if __name__ == "__main__":
    main()
