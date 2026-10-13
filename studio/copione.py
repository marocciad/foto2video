"""Copione proposto dal bot: idea -> testo di 20-30 s per il personaggio di BetForge.

Modello: Qwen3-4B-Instruct-2507 (Apache 2.0) quantizzato Q4_K_M, eseguito su CPU con llama-cpp.
Il testo deve presentare BetForge come analisi statistica del calcio: niente vincite, quote,
scommesse o soldi (Decreto Dignita' e policy dei social, vedi CLAUDE.md).
"""

from __future__ import annotations

import os
import re

MODEL_REPO = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
MODEL_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

SYSTEM = """Sei l'autore dei video promozionali di BetForge, un'app di analisi statistica del calcio \
(gol attesi, forma delle squadre, rating, modelli matematici su migliaia di partite).
Nei video parla un personaggio mascherato e incappucciato: misterioso, calmo, sicuro di sé, \
un filo ironico. Parla in prima persona, in italiano, con frasi brevi e d'effetto.

Scrivi SOLO il testo che il personaggio dice, pronto da leggere:
- tra 55 e 75 parole (circa 25 secondi);
- frasi corte, una pausa naturale dopo ogni frase;
- cita "BetForge" una o due volte; l'ultima frase è una chiusura forte e memorabile;
- niente saluti iniziali (c'è già un'intro registrata), niente hashtag, emoji, titoli o virgolette.

Regole obbligatorie: BetForge è analisi e informazione sportiva. NON parlare mai di scommesse, \
puntate, quote, bookmaker, vincite, soldi, guadagni o gioco d'azzardo, nemmeno per negarli. \
Non promettere certezze sui risultati ("sai cosa succederà"): i dati aiutano a capire il calcio, \
non garantiscono il futuro."""

RISKY = ["scommess", "scommett", "punta", "puntat", "quot", "bookmaker", "vinc", "vincit", "soldi",
         "guadagn", "denaro", "euro", "azzard", "giocat", "bet ", "win", "cash", "jackpot", "profitt"]


def risky_terms(text: str) -> list:
    """Parole a rischio rispetto al divieto di pubblicita' del gioco (BetForge escluso). Logica pura."""
    low = re.sub(r"(?i)betforge", "", text).lower() + " "
    found = []
    for term in RISKY:
        if re.search(r"\b" + re.escape(term), low):
            found.append(term.strip())
    return found


def clean(text: str) -> str:
    """Toglie markdown, virgolette, hashtag ed eventuali saluti iniziali dal testo generato."""
    text = re.sub(r"#\w+", "", text)  # hashtag interi, non solo il simbolo
    text = re.sub(r"[*_#`\"“”«»]", "", text)
    text = re.sub(r"(?im)^\s*(copione|testo)\s*:\s*", "", text)
    text = re.sub(r"(?i)^\s*benvenuti[.…!,\s]*", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def model_path() -> str:
    if os.environ.get("LLM_MODEL"):
        return os.environ["LLM_MODEL"]
    from huggingface_hub import hf_hub_download
    return hf_hub_download(MODEL_REPO, MODEL_FILE)


def generate(idea: str, attempts: int = 3) -> tuple:
    """(copione, parole_a_rischio). Rigenera finche' il testo e' pulito, al massimo `attempts` volte."""
    from llama_cpp import Llama  # import pigro: serve solo qui

    llm = Llama(model_path=model_path(), n_ctx=2048, n_threads=os.cpu_count() or 4, verbose=False)
    best, best_score = None, None
    for i in range(attempts):
        out = llm.create_chat_completion(
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"Idea per il video: {idea.strip()}"}],
            temperature=0.8, top_p=0.9, max_tokens=260, seed=1234 + i)
        text = clean(out["choices"][0]["message"]["content"])
        risky = risky_terms(text)
        score = score_script(text, risky)
        if best is None or score < best_score:
            best, best_score = (text, risky), score
        if score == (0, 0):
            break
    return best


def score_script(text: str, risky: list) -> tuple:
    """Piu' basso e' meglio: prima nessuna parola a rischio, poi lunghezza vicina a 50-85 parole."""
    n = len(text.split())
    off = 0 if 50 <= n <= 85 else min(abs(n - 50), abs(n - 85))
    return (len(risky), off)
