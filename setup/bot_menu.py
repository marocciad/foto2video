"""
Registra su Telegram il menu dei comandi e la descrizione del bot (al posto di BotFather).

Uso, dalla radice del progetto:
    python setup/bot_menu.py

Legge TELEGRAM_TOKEN da secrets.json. Si rilancia ogni volta che cambiano comandi o testi.
"""

import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio.__main__ import COMMANDS  # noqa: E402 - una sola fonte per i comandi

SHORT = "Studio BetForge: mandami testo e vocale, ti restituisco il video promozionale pronto per i social."
DESCRIPTION = ("🎬 Studio BetForge\n\n"
               "Mandami il testo del copione e un vocale con lo stesso testo: in pochi minuti ricevi il video "
               "in 9:16, 16:9 e 1:1, con voce camuffata e sottotitoli animati.\n\n"
               "Non puoi registrare? Manda solo il testo con #voce.\n\n"
               "Usa il pulsante Menu o scrivi /help per tutte le opzioni.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with open("secrets.json", encoding="utf-8") as f:
        token = json.load(f)["TELEGRAM_TOKEN"]
    base = f"https://api.telegram.org/bot{token}"
    calls = [
        ("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]}),
        ("setMyShortDescription", {"short_description": SHORT}),
        ("setMyDescription", {"description": DESCRIPTION}),
        ("setChatMenuButton", {"menu_button": {"type": "commands"}}),
    ]
    for method, params in calls:
        r = requests.post(f"{base}/{method}", json=params, timeout=30).json()
        print(f"{method}: {'ok' if r.get('ok') else r.get('description')}")


if __name__ == "__main__":
    main()
