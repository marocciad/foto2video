# Foto2Video

Bot Telegram personale che trasforma una foto in un breve video con l'IA, usando GPU gratuite di Kaggle. Costo: zero.

```
Tu (Telegram) ──foto + didascalia──▶ Bot
                                      │  i messaggi restano in coda fino a 24 h
GitHub Actions (ogni 10 min) ─────────┤  "ci sono foto e la GPU è spenta?"
                                      ▼
                     Kaggle: kernels push → notebook su T4
                     worker.py: Wan 2.2 TI2V-5B → MP4 → te lo manda in chat
                     dopo 10 min senza richieste si spegne da solo
```

## Cosa aspettarsi

- **Modello**: Wan 2.2 TI2V-5B, licenza Apache 2.0, quindi i video sono usabili anche commercialmente.
- **Qualità di default**: circa 480p, circa 3 secondi a 24 fps. Con `#lungo` si arriva a circa 5 secondi.
- **Tempi**: tra l'invio della foto e l'avvio passano fino a 10 minuti (il cron di GitHub) più l'avvio del notebook e il download del modello, circa 5–10 minuti la prima volta. I video successivi della stessa sessione partono subito.
- **Quota**: Kaggle dà circa 30 ore GPU a settimana. Il worker si spegne da solo quando non lavora.

> ⚠️ **Da verificare al primo avvio:** la parte su GPU non l'ho potuta eseguire, perché non ho una GPU a disposizione. La logica del bot è testata. Se il primo video esce nero o va in errore, guarda la sezione "Problemi noti".

## Setup (una volta sola, ~20 minuti)

### 1. Bot Telegram
1. Su Telegram apri **@BotFather**, scrivi `/newbot` e copia il **token**.
2. Il tuo ID Telegram lo scopri più avanti: scrivi `/id` al bot appena il dispatcher gira. In alternativa usa **@userinfobot**.

### 2. Kaggle
1. Crea un account su kaggle.com e **verifica il telefono** (Settings → Phone verification). Senza verifica non hai né GPU né internet nei notebook.
2. Settings → API → **Create New Token**: scarica `kaggle.json`, che contiene username e key.
3. Crea un **dataset privato** chiamato `foto2video-secrets` con dentro un file `secrets.json`, preparato partendo da `secrets.example.json`:
   ```json
   {
     "TELEGRAM_TOKEN": "il-token-di-BotFather",
     "ALLOWED_USER_IDS": [il_tuo_id],
     "BACKEND": "wan"
   }
   ```
   Il token sta qui e non nei Secrets di Kaggle perché quelli **si perdono quando il notebook viene avviato via API**.
4. In `kaggle/kernel-metadata.json` e `model_cache/kernel-metadata.json` sostituisci `TUO_USERNAME_KAGGLE` con il tuo username.

**Scorciatoia per i punti 3 e 4**: con la CLI kaggle installata e `secrets.json` compilato, lancia `python setup/push_secrets.py`. Lo script crea il dataset privato e aggiorna da solo `kernel-metadata.json`.

### 3. GitHub
1. Crea un repository e carica questa cartella.
   - **Pubblico** (consigliato): i minuti di Actions sono illimitati. Il codice non contiene segreti.
   - **Privato**: hai 2000 minuti al mese, quindi nel workflow porta il cron a `*/30` invece di `*/10`.
2. Settings → Secrets and variables → Actions, aggiungi 4 secret:
   `TELEGRAM_TOKEN`, `ALLOWED_USER_IDS` (es. `123456789`), `KAGGLE_USERNAME`, `KAGGLE_KEY`.
3. Tab **Actions** → abilita i workflow → "foto2video-dispatch" → **Run workflow** per provarlo subito.

### 4. Cache del modello (consigliata, una volta sola)
Senza cache ogni sessione riscarica circa 30 GB da Hugging Face. Con la cache il modello viene letto dal disco di Kaggle.
1. Aggiungi `KAGGLE_USERNAME` e `KAGGLE_KEY` a `secrets.json` e rilancia `python setup/push_secrets.py`.
2. `kaggle kernels push -p model_cache`: gira su CPU per circa 30–60 minuti, converte il transformer in fp16 e crea il dataset privato `wan22-ti2v-5b-fp16`.
3. `python setup/push_secrets.py --use-cache` collega il dataset al worker. Fai commit e push.

Il worker trova il modello da solo in `/kaggle/input`. Se il dataset manca, ripiega su Hugging Face.

### 5. Primo test (consigliato)
Metti `"BACKEND": "kenburns"` nel `secrets.json` (aggiorna il dataset con una nuova versione) e manda una foto al bot. Riceverai uno zoom lento fatto con ffmpeg: se arriva, tutta la catena Telegram → GitHub → Kaggle funziona. Poi torna a `"wan"`.

## Studio: video con la tua voce (regia virtuale)

Il formato principale per i video promozionali. **Non usa la GPU**: gira su GitHub Actions in circa 5–10 minuti.

1. Manda al bot un **vocale** con il testo da dire.
2. Per sottotitoli perfetti manda anche il **testo** del vocale come messaggio, prima o dopo, entro 10 minuti.
   - Se manca il testo, il bot aspetta 90 s e poi usa la trascrizione automatica, che può sbagliare qualche parola.
3. Ricevi il video in **9:16**, **16:9** e **1:1**: intro registrata, stacchi di inquadratura su ogni frase, voce camuffata, sottotitoli animati e logo finale.

Tag nel testo: `#verticale`, `#orizzontale` o `#quadrato` (un solo formato), `#nointro`.

**Senza registrare:** manda solo il testo con `#voce`. Lo legge una voce sintetica (Kokoro-82M, Apache 2.0) con lo stesso effetto metallico, e "BetForge" è pronunciato da una voce inglese. Il lavoro parte subito e l'intro resta la tua registrazione.

**Non sai cosa dire?** Scrivi `#idea` e l'argomento, per esempio `#idea parla dei gol attesi`. Il bot propone un copione di circa 25 s scritto da Qwen3-4B (Apache 2.0, su CPU), con le regole del personaggio e niente riferimenti al gioco con vincite. Lo copi e lo registri, oppure lo rimandi con `#voce`.

**Grafiche e sottotitoli animati (HyperFrames).** Su GitHub i video si completano con un overlay HyperFrames (licenza Apache 2.0):
- sottotitoli in cui ogni parola entra quando viene detta, con le parole chiave in una pillola verde;
- transizioni (lampo o glitch) a ogni cambio di inquadratura;
- apertura animata sull'intro;
- chiusura con il logo;
- grafiche scritte nel copione: `{numero: 12.480 | partite analizzate}`, `{barre: Inter 1,8 - Milan 0,9 | gol attesi}`, `{forma: VVNPV | Inter}`, `{rating: 87 | rating Inter}`. Vedi `/grafiche` nel bot.

Se HyperFrames non è disponibile o fallisce, il video esce con i sottotitoli classici.

### Menu del bot

```bash
python setup/bot_menu.py
```

Registra il pulsante **Menu** (`/help`, `/formati`, `/voce`, `/id`) e la descrizione del bot. Si rilancia quando cambiano i comandi o i testi in `studio/__main__.py`.

### Il personaggio (asset privati)

Immagini, logo, intro e inquadrature stanno in `personaggi/<nome>/`, cartella **esclusa da git**. Si caricano in un dataset Kaggle privato:

```bash
py -3.12 setup/push_assets.py
```

Nel dataset finiscono:
- `personaggio.json`: inquadrature per formato, testo dell'intro, parole da evidenziare. Esempio in `studio/personaggio.example.json`;
- le immagini citate in `personaggio.json`;
- `logo.png`: logo su sfondo nero;
- `intro/intro_pulita.wav`: l'intro registrata, già ripulita.

Rilancia lo script ogni volta che cambi qualcosa.

### Avvio puntuale con cron-job.org

Il cron di GitHub può tardare anche ore. Per un bot reattivo, un servizio gratuito come [cron-job.org](https://cron-job.org) chiama GitHub ogni 5 minuti.

1. Su GitHub crea un **fine-grained token**: Settings → Developer settings → Personal access tokens → Fine-grained.
   - Accesso al **solo** repository `foto2video`.
   - Permesso **Actions: Read and write**. Nessun altro.
2. Su cron-job.org crea un job:
   - **URL**: `https://api.github.com/repos/<utente>/foto2video/actions/workflows/dispatch.yml/dispatches`
   - **Metodo**: POST, ogni 5 minuti
   - **Header**: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`
   - **Corpo**: `{"ref":"main"}`

## Uso (foto → video IA su GPU)

Manda una **foto con didascalia** che descrive il movimento, meglio in inglese:

> *slow dolly-in, golden hour light, hair moving in the wind, cinematic*

| Tag | Effetto |
|---|---|
| `#lungo` | circa 5 s invece di circa 3 |
| `#veloce` | 20 passaggi invece di 30: più rapido, meno rifinito |

Comandi: `/help`, `/id`.

## Configurazione (in `secrets.json`)

| Chiave | Default | Note |
|---|---|---|
| `BACKEND` | `wan` | `kenburns` per test senza IA |
| `IDLE_MINUTES` | `10` | spegnimento per inattività. Con `0` si spegne circa 30 s dopo l'ultimo video: zero spreco, ma ogni foto successiva riparte da zero |
| `WAN_MAX_AREA` | `399360` (480×832) | più alto = più dettaglio ma più lento e più memoria |
| `WAN_FRAMES` / `WAN_FRAMES_LONG` | `73` / `121` | devono essere 4k+1 |
| `WAN_STEPS` | `30` | |
| `WAN_DTYPE` | `auto` | su T4 usa fp16; forza `bf16` se l'output esce nero |
| `WAN_FP8` | `auto` | pesi del transformer in FP8 (solo memoria) quando c'è una sola GPU: ~5 GB invece di ~10 |
| `FALLBACK_KENBURNS` | `true` | se l'IA fallisce, manda comunque uno zoom Ken Burns |
| `CHARACTER_PROMPT` | `""` | descrizione fissa del personaggio, aggiunta a ogni didascalia (es. *"his face is covered by a rigid, motionless white mask"*) |
| `NEGATIVE_EXTRA` | `""` | cose da evitare, aggiunte al prompt negativo (es. *"smiling, mouth moving, mask changing shape"*) |

## Problemi noti

- **Kaggle assegna una P100**: il metadato `machine_shape: NvidiaTeslaT4` serve a evitarlo, perché la P100 non è supportata dal PyTorch recente. Se la tua versione della CLI non lo riconosce, prova `NvidiaTeslaT4Highmem`.
- **Video nero o con NaN**: è un problema di fp16 su T4. Metti `WAN_DTYPE: "bf16"`, che è più lento perché emulato ma stabile.
- **Out of memory**: abbassa `WAN_MAX_AREA` (es. `320*576`) o evita `#lungo`.
- **Lento all'avvio**: senza la cache del modello (punto 4 del setup) ogni sessione riscarica circa 30 GB.
- **Errore all'avvio del worker**: il bot ti avvisa e scarta le foto in coda. Senza questo, il dispatcher riavvierebbe il notebook ogni 10 minuti.
- **Il cron non parte**: GitHub ritarda i cron nelle ore di punta e li **disattiva dopo 60 giorni** senza commit nei repo pubblici. Basta un commit qualsiasi per riattivarli.
- **Uso**: tienilo personale. Le GPU gratuite di Kaggle non vanno usate come backend di un servizio pubblico.

## Struttura

```
studio/                   regia virtuale: vocale -> video (gira su GitHub Actions, CPU)
  audio.py                pulizia, voce camuffata, volume, trascrizione, allineamento al copione
  tts.py                  voce sintetica per #voce (Kokoro, "BetForge" con voce inglese)
  copione.py              copione proposto per #idea (Qwen3-4B GGUF con llama-cpp, controllo parole a rischio)
  grafica.py + grafica/   overlay HyperFrames: sottotitoli, grafiche dal copione, transizioni, apertura, chiusura
  regia.py                inquadrature, bagliore, sottotitoli cinetici, logo finale
  __main__.py             legge la coda Telegram (vocali + copioni) e monta i video
  fonts/                  5 font OFL (licenze incluse)
setup/push_assets.py      carica personaggi/ in un dataset Kaggle privato
worker/worker.py          bot + generazione (gira su Kaggle)
dispatcher/dispatch.py    accende Kaggle quando servono foto (gira su GitHub)
kaggle/kernel-metadata.json
.github/workflows/dispatch.yml
tests/                    test senza rete: pytest -q
```
