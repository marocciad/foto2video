"""Estrae gli asset scaricati da Kaggle in una cartella pulita (usato dal workflow).

`kaggle datasets download --unzip` con un dataset di un solo file a volte lascia `assets.zip`,
a volte lo estrae gia'. Questo script gestisce entrambi i casi.

    python setup/unpack_assets.py <cartella_download> <cartella_asset>
"""

import glob
import os
import shutil
import sys
import zipfile


def main(src: str, dst: str):
    os.makedirs(dst, exist_ok=True)
    zips = glob.glob(os.path.join(src, "**", "assets.zip"), recursive=True)
    if zips:
        zipfile.ZipFile(zips[0]).extractall(dst)
    else:  # gia' estratto: le cartelle dei personaggi sono quelle con personaggio.json
        for cfg in glob.glob(os.path.join(src, "**", "personaggio.json"), recursive=True):
            char_dir = os.path.dirname(cfg)
            shutil.copytree(char_dir, os.path.join(dst, os.path.basename(char_dir)), dirs_exist_ok=True)
    found = sorted(os.path.basename(os.path.dirname(p))
                   for p in glob.glob(os.path.join(dst, "*", "personaggio.json")))
    if not found:
        sys.exit(f"Nessun personaggio trovato in {src}")
    print("Personaggi:", ", ".join(found))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
