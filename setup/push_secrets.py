"""
Crea (o aggiorna) il dataset Kaggle privato con secrets.json.

Uso, dalla radice del progetto:
    cp secrets.example.json secrets.json      # e compilalo
    python setup/push_secrets.py              # crea/aggiorna il dataset
    python setup/push_secrets.py --use-cache  # in piu': collega il worker alla cache del modello

Richiede la CLI kaggle installata nello stesso Python (pip install -U kaggle).
Credenziali: KAGGLE_USERNAME/KAGGLE_KEY in secrets.json, nell'ambiente o in ~/.kaggle/kaggle.json.
Su Windows con piu' Python: py -3.12 setup/push_secrets.py
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
# CLI dello stesso Python che esegue lo script (i token KGAT_ richiedono kaggle >= 1.8, Python >= 3.11)
KAGGLE = [sys.executable, "-m", "kaggle"]


def kaggle_env():
    """I token nuovi (KGAT_...) vanno passati alla CLI come KAGGLE_API_TOKEN (auth Bearer)."""
    key = os.environ.get("KAGGLE_KEY", "")
    if key.startswith("KGAT_") and not os.environ.get("KAGGLE_API_TOKEN"):
        os.environ["KAGGLE_API_TOKEN"] = os.environ.pop("KAGGLE_KEY")


def kaggle_username() -> str:
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    path = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["username"]
    sys.exit("Credenziali Kaggle non trovate (KAGGLE_USERNAME o ~/.kaggle/kaggle.json)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # la console Windows (cp1252) non stampa "→"
    if not os.path.exists("secrets.json"):
        sys.exit("Manca secrets.json: copialo da secrets.example.json e compilalo")
    with open("secrets.json", encoding="utf-8") as f:
        secrets = json.load(f)
    missing = [k for k in REQUIRED if not secrets.get(k)]
    if missing or "il-token" in str(secrets.get("TELEGRAM_TOKEN")):
        sys.exit(f"secrets.json incompleto: {missing or ['TELEGRAM_TOKEN']}")

    # Se le credenziali Kaggle non sono nell'ambiente, le prende da secrets.json
    for key in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
        if secrets.get(key) and not os.environ.get(key):
            os.environ[key] = str(secrets[key])
    kaggle_env()
    user = kaggle_username()
    with tempfile.TemporaryDirectory() as d:
        shutil.copy("secrets.json", os.path.join(d, "secrets.json"))
        with open(os.path.join(d, "dataset-metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"title": SLUG, "id": f"{user}/{SLUG}",
                       "licenses": [{"name": "other"}]}, f)
        exists = subprocess.run(KAGGLE + ["datasets", "status", f"{user}/{SLUG}"],
                                capture_output=True, text=True).returncode == 0
        cmd = (KAGGLE + ["datasets", "version", "-p", d, "-m", "aggiornamento secrets"]
               if exists else KAGGLE + ["datasets", "create", "-p", d])  # create = privato di default
        print(" ".join(cmd[len(KAGGLE) - 1:len(KAGGLE) + 2]), "→", f"{user}/{SLUG}")
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
