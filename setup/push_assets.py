"""
Crea (o aggiorna) il dataset Kaggle PRIVATO con gli asset dello studio: immagini del
personaggio, logo, intro registrata, personaggio.json. Il repo e' pubblico: gli asset no.

Uso, dalla radice del progetto:
    py -3.12 setup/push_assets.py            # carica tutti i personaggi in personaggi/

Struttura attesa: personaggi/<nome>/{personaggio.json, immagini, logo.png, intro/intro_pulita.wav}.
GitHub Actions lo scarica con `kaggle datasets download` prima di montare i video.
"""

import json
import os
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from push_secrets import KAGGLE, kaggle_env, kaggle_username  # noqa: E402

SLUG = "foto2video-assets"
ROOT = "personaggi"


def needed_files(char_dir: str) -> list:
    """Solo cio' che serve al montaggio (niente prove, niente versioni gia' elaborate)."""
    with open(os.path.join(char_dir, "personaggio.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    files = {"personaggio.json", cfg.get("logo", "logo.png")}
    if cfg.get("intro"):
        files.add(cfg["intro"])
    for shots in cfg["inquadrature"].values():
        files.update(s[0] for s in shots)
    missing = [f for f in files if not os.path.exists(os.path.join(char_dir, f))]
    if missing:
        sys.exit(f"{char_dir}: mancano {missing}")
    return sorted(files)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if os.path.exists("secrets.json"):  # credenziali Kaggle da secrets.json, come push_secrets.py
        with open("secrets.json", encoding="utf-8") as f:
            secrets = json.load(f)
        for key in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
            if secrets.get(key) and not os.environ.get(key):
                os.environ[key] = str(secrets[key])
    kaggle_env()
    user = kaggle_username()
    chars = [c for c in sorted(os.listdir(ROOT)) if os.path.exists(os.path.join(ROOT, c, "personaggio.json"))]
    if not chars:
        sys.exit(f"Nessun personaggio in {ROOT}/ (serve personaggi/<nome>/personaggio.json)")
    with tempfile.TemporaryDirectory() as d:
        # un solo zip: le sottocartelle nei dataset Kaggle si scaricano in modo poco prevedibile
        with zipfile.ZipFile(os.path.join(d, "assets.zip"), "w", zipfile.ZIP_DEFLATED) as z:
            for c in chars:
                for rel in needed_files(os.path.join(ROOT, c)):
                    z.write(os.path.join(ROOT, c, rel), f"{c}/{rel}")
                    print(f"  + {c}/{rel}")
        with open(os.path.join(d, "dataset-metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"title": SLUG, "id": f"{user}/{SLUG}", "licenses": [{"name": "other"}]}, f)
        exists = subprocess.run(KAGGLE + ["datasets", "status", f"{user}/{SLUG}"],
                                capture_output=True, text=True).returncode == 0
        cmd = (KAGGLE + ["datasets", "version", "-p", d, "-m", "aggiornamento asset"]
               if exists else KAGGLE + ["datasets", "create", "-p", d])  # create = privato di default
        print(" ".join(cmd[len(KAGGLE) - 1:len(KAGGLE) + 2]), "→", f"{user}/{SLUG}")
        subprocess.run(cmd, check=True)
    print("Fatto.")


if __name__ == "__main__":
    main()
