# Prototipi della regia virtuale (girano sul PC, non ancora nel bot)

Collaudati il 2026-10-11 con le immagini in `personaggi/betforge/` (non nel repo).

| Script | Cosa fa |
|---|---|
| `vocale.py` | scarica i vocali dal bot **senza confermarli**, pulisce l'audio, applica la voce 3 (rubberband 0.80 + eco corta), porta a ~-14.7 LUFS e trascrive con faster-whisper (`medium`) per avere i tempi delle parole → `voce.wav` + `tempi.json`. Con `INTRO=<wav>` usa l'intro salvata e solo l'ultimo vocale come contenuto. `RIUSA_TRASCRIZIONE=1` salta whisper |
| `regia.py` | monta il video: un'inquadratura per frase (intro fissa, rotazione, chiusura sul campo lungo), bagliore verde guidato dalla voce, sottotitoli ASS parola per parola (font Anton, parola detta in `#22c55e`) |
| `tts_frasi.py` | alternativa senza registrazione: voce sintetica Kokoro (`im_nicola`), frase per frase |

Ambiente: Python 3.12 con `torch` (CPU), `kokoro`, `soundfile`, `faster-whisper`, `numpy`, `pillow`, `requests`; ffmpeg con libass e rubberband. Font Anton (OFL) in `fonts/` accanto a `regia.py`.

```bash
INTRO=personaggi/betforge/intro/intro_pulita.wav python prototipi/vocale.py secrets.json lavoro
python prototipi/regia.py personaggi/betforge lavoro/voce.wav lavoro/tempi.json video_9x16.mp4 9x16
```

Lezioni dal collaudo:
- niente `atempo` per abbassare il tono (parlato a scatti) e niente flanger (fruscio sulle code delle parole);
- niente compressore rapido: "afferra" le plosive (la B di Benvenuti). Va bene solo con attack lento (25 ms);
- l'audio va ripulito **una volta sola**: la doppia `afftdn` lascia artefatti;
- whisper: niente `initial_prompt` con il testo dell'intro (la salta), meglio `hotwords="BetForge"`.
