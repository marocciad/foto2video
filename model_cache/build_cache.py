"""
Foto2Video - costruisce UNA VOLTA la cache del modello come dataset Kaggle privato.

Gira su Kaggle (CPU basta, serve internet). Scarica Wan 2.2 TI2V-5B da
Hugging Face, converte il transformer da fp32 (~20 GB) a fp16 (~10 GB) e
carica tutto come dataset privato. Il worker lo trova da solo in
/kaggle/input quando il dataset e' allegato: niente download a ogni avvio.

Requisiti in secrets.json (dataset privato allegato):
  KAGGLE_USERNAME, KAGGLE_KEY  -> servono per creare il dataset da dentro Kaggle
Avvio:  kaggle kernels push -p model_cache
"""

import glob
import json
import os
import shutil
import subprocess
import sys

MODEL = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
SLUG = "wan22-ti2v-5b-fp16"
SRC = "/tmp/wan_src"
OUT = "/tmp/wan_fp16"


def secrets() -> dict:
    for path in glob.glob("/kaggle/input/**/secrets.json", recursive=True):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def disk(label: str):
    total, used, free = shutil.disk_usage("/tmp")
    print(f"[disk] {label}: liberi {free / 1e9:.1f} GB")


def main():
    cfg = secrets()
    for key in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
        if cfg.get(key):
            os.environ[key] = str(cfg[key])
    user = os.environ.get("KAGGLE_USERNAME")
    if not (user and os.environ.get("KAGGLE_KEY")):
        sys.exit("Servono KAGGLE_USERNAME e KAGGLE_KEY in secrets.json")

    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U",
                    "git+https://github.com/huggingface/diffusers",
                    "transformers", "accelerate", "huggingface_hub", "kaggle"], check=True)
    import torch
    from diffusers import WanTransformer3DModel
    from huggingface_hub import snapshot_download

    disk("inizio")
    snapshot_download(MODEL, local_dir=SRC, token=cfg.get("HF_TOKEN") or None)
    disk("dopo il download")

    os.makedirs(OUT, exist_ok=True)
    # 1) transformer: fp32 -> fp16 (dimezza dimensione e tempo di caricamento)
    tr = WanTransformer3DModel.from_pretrained(SRC, subfolder="transformer", torch_dtype=torch.float16)
    tr.save_pretrained(os.path.join(OUT, "transformer"), max_shard_size="4GB")
    del tr
    shutil.rmtree(os.path.join(SRC, "transformer"))
    disk("dopo la conversione")

    # 2) tutto il resto (text encoder, vae, tokenizer, scheduler, model_index.json) cosi' com'e'
    for name in os.listdir(SRC):
        if name.startswith("."):
            continue
        shutil.move(os.path.join(SRC, name), os.path.join(OUT, name))

    with open(os.path.join(OUT, "dataset-metadata.json"), "w", encoding="utf-8") as f:
        json.dump({"title": SLUG, "id": f"{user}/{SLUG}", "licenses": [{"name": "apache-2.0"}]}, f)

    # --dir-mode zip: le sottocartelle vengono caricate (altrimenti sarebbero ignorate)
    exists = subprocess.run(["kaggle", "datasets", "status", f"{user}/{SLUG}"],
                            capture_output=True).returncode == 0
    cmd = (["kaggle", "datasets", "version", "-p", OUT, "-m", "rebuild", "--dir-mode", "zip"]
           if exists else ["kaggle", "datasets", "create", "-p", OUT, "--dir-mode", "zip"])
    subprocess.run(cmd, check=True)
    print(f"✅ Dataset {user}/{SLUG} caricato. Aggiungilo a dataset_sources del worker.")


if __name__ == "__main__":
    main()
