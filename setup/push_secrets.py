"""
Crea (o aggiorna) il dataset Kaggle privato con secrets.json.

Uso, dalla radice del progetto:
    cp secrets.example.json secrets.json      # e compilalo
    python setup/push_secrets.py              # crea/aggiorna il dataset
    python setup/push_secrets.py --use-cache  # in piu': collega il worker alla cache del modello

Richiede la CLI kaggle autenticata (KAGGLE_USERNAME/KAGGLE_KEY o ~/.kaggle/kaggle.json).
Il dataset resta PRIVATO: contiene il token del bot.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

SLUG = "foto2video-secrets"
CACHE_SLUG = "wan22-ti2v-5b-fp16"
REQUIRED = ("TELEGRAM_TOKEN", "ALLOWED_USER_IDS")


def kaggle_username() -> str:
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    path = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["username"]
    sys.exit("Credenziali Kaggle non trovate (KAGGLE_USERNAME o ~/.kaggle/kaggle.json)")


def main():
    if not os.path.exists("secrets.json"):
        sys.exit("Manca secrets.json: copialo da secrets.example.json e compilalo")
    with open("secrets.json", encoding="utf-8") as f:
        secrets = json.load(f)
    missing = [k for k in REQUIRED if not secrets.get(k)]
    if missing or "il-token" in str(secrets.get("TELEGRAM_TOKEN")):
        sys.exit(f"secrets.json incompleto: {missing or ['TELEGRAM_TOKEN']}")

    user = kaggle_username()
    with tempfile.TemporaryDirectory() as d:
        shutil.copy("secrets.json", os.path.join(d, "secrets.json"))
        with open(os.path.join(d, "dataset-metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"title": SLUG, "id": f"{user}/{SLUG}",
                       "licenses": [{"name": "other"}]}, f)
        exists = subprocess.run(["kaggle", "datasets", "status", f"{user}/{SLUG}"],
                                capture_output=True, text=True).returncode == 0
        cmd = (["kaggle", "datasets", "version", "-p", d, "-m", "aggiornamento secrets"]
               if exists else ["kaggle", "datasets", "create", "-p", d])  # create = privato di default
        print(" ".join(cmd[:3]), "→", f"{user}/{SLUG}")
        subprocess.run(cmd, check=True)

    for meta_path in (os.path.join("kaggle", "kernel-metadata.json"),
                      os.path.join("model_cache", "kernel-metadata.json")):
        with open(meta_path, encoding="utf-8") as f:
            text = f.read()
        if "TUO_USERNAME_KAGGLE" in text:
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(text.replace("TUO_USERNAME_KAGGLE", user))
            print(f"Aggiornato {meta_path} con l'username '{user}'")
    if "--use-cache" in sys.argv:
        use_cache(user)
    print("Fatto.")


def use_cache(user: str):
    """Allega al worker il dataset con i pesi (creato da model_cache/build_cache.py)."""
    path = os.path.join("kaggle", "kernel-metadata.json")
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    ref = f"{user}/{CACHE_SLUG}"
    if ref not in meta["dataset_sources"]:
        meta["dataset_sources"].append(ref)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
        print(f"Worker collegato alla cache {ref}")


if __name__ == "__main__":
    main()
