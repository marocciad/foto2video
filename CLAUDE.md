# Foto2Video — contesto per Claude Code

Bot Telegram **personale** che trasforma una foto in un breve video IA, usando solo GPU gratuite.
Vincolo principale: **costo zero**. Proprietario: Daniele (sviluppatore indipendente, stack abituale Flutter/Firebase; qui è tutto Python).
Lingua di lavoro: **italiano** (messaggi del bot, commenti, README). Identificatori nel codice in inglese.

## Architettura

```
Telegram (coda: tiene i messaggi 24 h)
   ▲ getUpdates / sendVideo
   │
worker/worker.py ── gira su Kaggle (T4), long polling, genera, si spegne dopo IDLE_MINUTES
   ▲ kaggle kernels push (ogni push = nuova esecuzione)
   │
dispatcher/dispatch.py ── GitHub Actions cron */10: se kernel non attivo e ci sono foto → push
```

- Nessun database: la coda è Telegram stesso. Firebase scartato perché Storage/Functions richiedono il piano Blaze.
- `worker.py` deve restare **un unico file**, perché Kaggle `kernel_type: script` esegue un solo `code_file`. Il workflow lo copia in `kaggle/` prima del push.
- Le dipendenze pesanti (torch, diffusers) si importano **solo dentro** `WanBackend`, così i test girano senza GPU.

## Decisioni prese (e perché)

| Decisione | Motivo |
|---|---|
| Modello Wan 2.2 TI2V-5B (diffusers, `WanImageToVideoPipeline`, `expand_timesteps=True`) | Apache 2.0: uso commerciale ok. LTX ha una licenza più restrittiva |
| `machine_shape: NvidiaTeslaT4` | il default di Kaggle è la P100 (CUDA 6.0), non supportata dal PyTorch recente |
| fp16 su T4, VAE in fp32 | la T4 (sm 7.5) non ha bf16 nativo |
| Con 2 GPU: text encoder UMT5 su `cuda:1`, il resto su `cuda:0`; con 1 GPU: `enable_model_cpu_offload` | il modello non entra intero in 16 GB |
| Secrets in un **dataset Kaggle privato** (`secrets.json`) | i Kaggle Secrets vengono rimossi quando il kernel è avviato via API |
| Update Telegram confermato **prima** di generare | at-most-once: un crash non deve creare un loop che brucia quota |
| Default ~480p, 73 frame (~3 s), 30 step | compromesso tempo/qualità su T4 |
| Backend `kenburns` (ffmpeg, CPU) | serve a testare tutta la catena senza GPU; è anche il fallback automatico se Wan fallisce |
| FP8 **solo storage** (`enable_layerwise_casting`) con 1 GPU | la T4 non ha FP8 hardware: si memorizza in FP8 e si calcola in fp16. Dimezza la VRAM del transformer |
| Niente `vae.enable_slicing()` | in AutoencoderKLWan agisce solo con batch > 1: inutile qui. Basta `enable_tiling()` |
| Niente bf16 su T4 | è emulato, quindi lento. Resta come opzione (`WAN_DTYPE=bf16`) solo se fp16 produce NaN |
| TI2V-5B **non ha image encoder** CLIP | l'immagine entra tramite il VAE (`expand_timesteps`). Non va confuso con Wan 2.1 I2V-14B |
| Dispatcher: **prima** `kaggle kernels status`, **poi** Telegram | una getUpdates mentre il worker fa long polling causa 409 Conflict e gli chiude la connessione |
| Dispatcher: stato Kaggle sconosciuto = non avviare | meglio perdere un giro che avviare due worker |
| Errore fatale all'avvio del worker: avvisa e consuma la coda | evita il loop "dispatcher riavvia, worker crasha" |
| Cache modello come dataset Kaggle (`model_cache/`), rilevata in automatico da `find_local_model()` | evita circa 30 GB di download a sessione |

## Comandi

```bash
pip install -r requirements-dev.txt     # serve anche ffmpeg nel PATH
pytest -q                               # test senza rete (Telegram finto, ffmpeg reale)
python setup/push_secrets.py            # crea/aggiorna il dataset privato dei secrets
cp worker/worker.py kaggle/ && kaggle kernels push -p kaggle   # avvio manuale del worker
kaggle kernels status <user>/foto2video-worker
kaggle kernels output <user>/foto2video-worker -p out/          # log dell'ultima esecuzione
```

## Stato

- ✅ Logica bot, dispatcher, backend Ken Burns: testati.
- ⚠️ **Mai eseguito su GPU reale.** Il backend Wan è scritto sul sorgente di diffusers (main, ottobre 2026) ma va collaudato su Kaggle.
  Primo collaudo: `BACKEND=kenburns` → catena ok → `BACKEND=wan`. Poi leggi i log con `kaggle kernels output`.
- Rischi da verificare al collaudo: qualità in FP8 storage (disattivabile con `WAN_FP8=false`), UMT5 in fp16 (possibili NaN → `WAN_DTYPE=bf16`), output nero in fp16 (→ `WAN_DTYPE=bf16`), OOM (→ `WAN_MAX_AREA` più basso), `from_pretrained(text_encoder=None, tokenizer=None)` sul ramo a 2 GPU, valore di `machine_shape` accettato dalla CLI, tempo reale per clip.

## Roadmap (in ordine di valore)

1. **Collaudo su Kaggle** e taratura dei default (risoluzione, frame, step) sui tempi reali della T4.
2. **Cache del modello**: implementata (`model_cache/build_cache.py`) ma **non collaudata**. Da verificare: che `--dir-mode zip` venga estratto in cartelle su `/kaggle/input`, lo spazio disco in `/tmp` durante la build (circa 45 GB di picco) e i tempi reali di avvio.
3. **Post-produzione "cinematografica"** in ffmpeg: interpolazione a 48/60 fps (`minterpolate` o RIFE), bande letterbox, color grade (LUT), musica opzionale.
4. **Formato di output**: tag `#verticale`/`#orizzontale`/`#quadrato` con crop guidato.
5. **Backend LTX-2.3** come alternativa veloce, con audio nativo (attenzione alla licenza).
6. **Personaggio ricorrente**: LoRA di volto per Wan (Daniele ha già esperienza con LoRA SDXL).
7. **Montaggio multi-clip**: più foto → più clip → un video unico con transizioni.

## Regole

- Restare sulle GPU gratuite e per **uso personale**: niente esposizione pubblica del bot (`ALLOWED_USER_IDS` sempre impostato).
- Non mettere mai token in repo: `secrets.json` è in `.gitignore`.
- Limiti di Telegram: il bot scarica file fino a 20 MB e invia video fino a 50 MB (`finalize_mp4` ricomprime sotto 48 MB).
- Ogni modifica a `worker.py` deve far passare `pytest`. Le parti GPU vanno tenute dietro import lazy.
