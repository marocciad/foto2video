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

- **Studio** (`studio/`, il formato principale): stesso workflow del dispatcher. Se la coda non inizia con una foto, `dispatch.py` scrive `action=studio` e il workflow installa ffmpeg e whisper, scarica gli asset privati (dataset Kaggle `foto2video-assets`) e lancia `python -m studio`. Lo studio monta vocale (+ copione) → video in 9:16, 16:9 e 1:1 su CPU, e si ferma alla prima foto, che resta per Kaggle.
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
| Token Kaggle nuovi `KGAT_…` → passati alla CLI come `KAGGLE_API_TOKEN` (`kaggle_env()`) | accettano solo auth Bearer: servono la CLI kaggle ≥ 1.8 (oggi 2.x) e Python ≥ 3.11. Il secret GitHub resta `KAGGLE_KEY` |
| CLI 2.x: "Cannot access kernel" = kernel mancante | per un kernel mai creato risponde così invece di 404. Senza questa regola il primo avvio non partirebbe mai |
| Studio su GitHub Actions, non su Kaggle | la regia virtuale è solo CPU: niente quota GPU e niente avvio del notebook. Repo pubblico = minuti illimitati |
| Asset del personaggio in un dataset Kaggle privato (`setup/push_assets.py`) | il repo è pubblico. Il workflow li scarica solo quando serve |
| Coda studio: conferma solo il **prefisso contiguo** gestito (`confirm_offset`) | Telegram conferma tutto ciò che sta prima dell'offset: vocale e copione possono non essere adiacenti |
| Vocale senza copione: attesa di 90 s; copione senza vocale: attesa di 15 min | il copione può arrivare dopo il vocale, e registrare richiede tempo |
| Sottotitoli: testo dal copione, tempi da whisper (`align_script`) | whisper sbaglia qualche parola ("matematico", "gol a test"); il copione è esatto |
| `#voce`: testo letto da Kokoro su Actions (CPU), intro sempre registrata | per quando Daniele non può registrare. "BetForge" sintetizzato con `am_onyx` e incollato nella frase (`tts.segments`). Kokoro/torch-cpu installati solo se `tts=1` |
| Whisper `small` su Actions | con il copione bastano i tempi, e `small` è circa 10 volte più veloce di `medium` |
| Cache modello come dataset Kaggle (`model_cache/`), rilevata in automatico da `find_local_model()` | evita circa 30 GB di download a sessione |

## Comandi

```bash
pip install -r requirements-dev.txt     # serve anche ffmpeg nel PATH
pytest -q                               # test senza rete (Telegram finto, ffmpeg reale; lo studio richiede ffmpeg con rubberband e libass)
py -3.12 setup/push_assets.py           # carica personaggi/ nel dataset privato foto2video-assets
py -3.12 setup/push_secrets.py          # crea/aggiorna il dataset privato dei secrets (serve Python >= 3.11)
cp worker/worker.py kaggle/ && kaggle kernels push -p kaggle   # avvio manuale del worker
kaggle kernels status <user>/foto2video-worker
kaggle kernels output <user>/foto2video-worker -p out/          # log dell'ultima esecuzione
```

## Stato

- ✅ Logica bot, dispatcher, backend Ken Burns: testati e collaudati su Kaggle (2026-10-11).
- ✅ **Studio collaudato su GitHub Actions** (2026-10-12): vocale di 19 s + copione → 9:16 e 16:9 in circa 2 min 45 s dall'avvio del workflow (installazione 30 s, montaggio 126 s). Wan è stato collaudato su 2×T4 (circa 16 min a sessione senza cache), ma per questo formato non serve.
- ✅ **Avvio ogni 5 minuti con cron-job.org** (2026-10-12): POST a `.../actions/workflows/dispatch.yml/dispatches` con token fine-grained (solo `foto2video`, Actions read/write). Un body vuoto dà 422: serve `{"ref":"main"}`. **Il token scade il 2027-10-10**: va rigenerato e aggiornato su cron-job.org. L'API version 2022-11-28 è in dismissione (sunset 2028-03-10).
- ✅ **Formato 1:1** e **modalità `#voce`** (2026-10-13): `#voce` collaudata su Actions, con installazione di kokoro in 36 s e studio completo in circa 1 min 45 s per un solo formato.
- ⏳ Da fare: logo ad alta risoluzione.
- Wan su Kaggle: funziona (2 GPU, fp16), ma fa sorridere la maschera e deforma l'armatura. Corretti `torchao` vecchio e il fallback in caso di errore di caricamento.
- Rischi da verificare al collaudo: qualità in FP8 storage (disattivabile con `WAN_FP8=false`), UMT5 in fp16 (possibili NaN → `WAN_DTYPE=bf16`), output nero in fp16 (→ `WAN_DTYPE=bf16`), OOM (→ `WAN_MAX_AREA` più basso), `from_pretrained(text_encoder=None, tokenizer=None)` sul ramo a 2 GPU, valore di `machine_shape` accettato dalla CLI, tempo reale per clip.

## Obiettivo d'uso (risposte di Daniele, 2026-10-11)

- **Scopo**: video promozionali per **sponsorizzare la sua app** su tutti i social e su YouTube, quindi uso **commerciale**. Wan (Apache 2.0) va bene; per ogni componente nuovo (modelli, TTS, font, musica) va verificata la licenza commerciale.
- **Volume**: circa 3 video al giorno, un solo utente. Nessuna fretta e nessuna fascia oraria preferita.
- **Soggetto tipico**: un'immagine generata con un **personaggio inventato con una maschera**, seduto in uno studio. Il soggetto **agisce** (non solo movimenti di camera). La maschera rende superfluo il lip-sync.
- **Output desiderato**: 720p, **10–30 s**, più formati in base al social (9:16, 16:9, 1:1), **audio** e **sottotitoli animati in stile social** (parola per parola, evidenziati).
- **Interazione**: prompt in italiano con traduzione automatica in inglese (disattivabile). Sia didascalia libera sia bottoni/preset. Messaggi di conferma e di avanzamento. Foto inviate anche come **file**, a qualità piena.
- **Regia (requisito chiave, 2026-10-11)**: il personaggio deve restare **completamente fermo**: maschera e armatura **non devono cambiare**. Al massimo si muovono un po' le mani mentre "parla". Ciò che conta sono i **cambi di inquadratura** durante il parlato (campo largo, mezzo busto, primo piano). Al primo test Wan ha fatto sorridere la maschera e ha deformato un po' l'armatura: la generazione IA libera non basta.
- **Intro fissa in ogni video**: *"Benvenuti…"*, poi 1–2 s di pausa sospesa, poi *"questo è BetForge…"*, poi parte il contenuto. Va preparata una volta sola e riusata, per coerenza e per risparmiare tempo.
- **Immagini del personaggio**: in `personaggi/betforge/` (gitignored): `01_campo_lungo`, `02_frontale` (16:9), `03_profilo_verso_sx` e `04_profilo_verso_dx` (9:16). Il prototipo di regia multi-angolo è stato approvato come direzione. Bagliore verde: tenerlo **tenue** (Daniele l'ha trovato troppo forte alla prima prova).
- **Testo parlato e sottotitoli**: due modalità. (a) Daniele scrive il testo; (b) il bot propone un testo promozionale partendo da una descrizione. Per (b) serve un LLM piccolo e gratuito su Kaggle (candidato Qwen, Apache 2.0), caricato e poi scaricato prima di Wan.
- **Voce**: **robotica/camuffata**, come una voce alterata per non farsi riconoscere. Si ottiene con TTS + effetti ffmpeg (pitch, ring-mod/vocoder, flanger).
- **Voce scelta (2026-10-11)**: variante **3 "metallica"**: tono abbassato con `asetrate` 0.80 **senza `atempo`** (il time-stretch la rendeva a scatti), generando la TTS più veloce di 1/0.80 per compensare. Poi eco corta, flanger, passa-banda e loudnorm.
- **Pronuncia di "BetForge"**: **inglese**, sintetizzata con una voce inglese di Kokoro (`am_onyx`) e montata nella frase italiana. La voce italiana aggiunge sempre una vocale finale ("forgi").
- **Più carattere nel parlato**: Kokoro è piatto. Opzioni: (a) Daniele registra la voce (memo vocale su Telegram) → trascrizione per i tempi delle parole → effetto metallico; (b) TTS espressiva (candidato Chatterbox Multilingual, MIT, con controllo dell'enfasi) su GPU Kaggle.
- **Musica**: niente nel video, Daniele la aggiunge dopo nell'app del social.
- **Stile grafico**: verde BetForge `#22c55e` per la parola evidenziata (lo sfondo dell'app è `#080b10`).
- **Logo / call-to-action finale**: non ora, ma va previsto come **opzione** disattivata di default.
- **App da promuovere**: BetForge, analisi e pronostici calcistici. ⚠️ In Italia il Decreto Dignità vieta la pubblicità del gioco con vincite in denaro, e i social hanno policy restrittive su scommesse e pronostici: segnalato a Daniele, che deve verificare.
- **Vincoli**: tutto gratis per ora. Telegram ora, app Flutter in futuro. Nessun archivio oltre alla chat.

## Regia virtuale (approvata da Daniele il 2026-10-11: "il video è bellissimo")

Implementata in `studio/` (prototipi originali in `prototipi/`). Il formato approvato:
- intro fissa registrata da Daniele (`personaggi/betforge/intro/`), poi il suo vocale con il contenuto;
- voce 3 (rubberband 0.80 + eco corta, ~-14.7 LUFS), testo dei sottotitoli dal **copione** allineato ai tempi di whisper;
- uno stacco di inquadratura per frase tra le 4 immagini del personaggio, bagliore verde tenue guidato dalla voce;
- sottotitoli cinetici: 5 font OFL a rotazione, parole chiave più grandi, "BetForge" in oro, parola detta in verde con un "pop";
- chiusura di circa 2,8 s con il logo (da `Betforge/public/icons/icon-512x512.png`, a bassa risoluzione: chiedere a Daniele un SVG o un PNG grande).
- Wan non serve per questo formato: tutto CPU.

## Roadmap (in ordine di valore)

1. **Collaudo su Kaggle** (kenburns → wan) e misura del tempo reale per clip sulla T4: tutto il resto si dimensiona su questo dato.
2. **Cache del modello**: implementata (`model_cache/build_cache.py`) ma **non collaudata**. Da verificare: che `--dir-mode zip` venga estratto in cartelle su `/kaggle/input`, lo spazio disco in `/tmp` durante la build (circa 45 GB di picco) e i tempi reali di avvio.
3. **Formati**: tag `#verticale`/`#orizzontale`/`#quadrato` e bottoni. Wan genera direttamente nell'aspect ratio scelto, senza crop.
4. **Video lunghi (10–30 s)**: catena di clip di circa 5 s, ognuna partendo dall'ultimo fotogramma della precedente, poi concatenate. Per la quota conviene generare a 480p e fare upscale a 720p dopo.
5. **Voce + sottotitoli animati** (nota: oggi `finalize_mp4` usa `-an` e toglie l'audio, va cambiato): testo della voce fuori campo → TTS italiano (candidato Kokoro-82M, Apache 2.0) → timestamp per parola (faster-whisper) → sottotitoli ASS in stile social, impressi nel video con ffmpeg/libass. Font OFL (es. Montserrat ExtraBold).
6. **Prompt IT→EN** automatico (candidato opus-mt-it-en su CPU), con eventuale "arricchimento" del prompt.
7. **Post-produzione**: interpolazione a 48/60 fps (RIFE), upscale, musica opzionale (royalty-free, o aggiunta direttamente nell'app del social).
8. **Personaggio ricorrente**: per ora basta la stessa immagine di partenza. LoRA solo se la coerenza tra clip non basta.
9. **App Flutter** come seconda interfaccia (oltre a Telegram).
10. **Backend LTX-2.3** come alternativa veloce con audio nativo: verificare la licenza per uso commerciale.

## Regole

- Restare sulle GPU gratuite e per **uso personale**: niente esposizione pubblica del bot (`ALLOWED_USER_IDS` sempre impostato).
- Non mettere mai token in repo: `secrets.json` è in `.gitignore`.
- Limiti di Telegram: il bot scarica file fino a 20 MB e invia video fino a 50 MB (`finalize_mp4` ricomprime sotto 48 MB).
- Ogni modifica a `worker.py` deve far passare `pytest`. Le parti GPU vanno tenute dietro import lazy.
