"""
Foto2Video - worker GPU per Kaggle.

Legge le foto inviate al bot Telegram (long polling), genera un video
con il backend scelto e lo rimanda in chat. Si spegne da solo dopo
IDLE_MINUTES senza lavoro, per non consumare la quota GPU settimanale.

Configurazione: file secrets.json in un dataset Kaggle privato
(vedi README) oppure variabili d'ambiente con gli stessi nomi.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

DEFAULTS = {
    "BACKEND": "wan",            # "wan" (IA, GPU) oppure "kenburns" (zoom, CPU, per test)
    "IDLE_MINUTES": 10,          # si spegne dopo N minuti senza lavoro
    "MAX_HOURS": 11,             # limite di sicurezza (sessione Kaggle max 12h)
    "WAN_MODEL": "Wan-AI/Wan2.2-TI2V-5B-Diffusers",
    "WAN_MAX_AREA": 480 * 832,   # ~480p: compromesso giusto per T4
    "WAN_FRAMES": 73,            # 73 frame = ~3 s a 24 fps (deve essere 4k+1)
    "WAN_FRAMES_LONG": 121,      # #lungo = ~5 s
    "WAN_STEPS": 30,
    "WAN_STEPS_FAST": 20,        # #veloce
    "WAN_GUIDANCE": 5.0,
    "WAN_DTYPE": "auto",         # auto | fp16 | bf16
    "WAN_FP8": "auto",           # pesi del transformer memorizzati in FP8 (auto = solo con 1 GPU)
    "FALLBACK_KENBURNS": True,   # se Wan fallisce, manda comunque uno zoom Ken Burns
    "CHARACTER_PROMPT": "",      # descrizione fissa del personaggio, aggiunta a ogni didascalia
    "NEGATIVE_EXTRA": "",        # cose da evitare, aggiunte al prompt negativo (es. maschera che sorride)
}

NEGATIVE_PROMPT = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，"
    "最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，"
    "画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，"
    "杂乱的背景，三条腿，背景人很多，倒着走"
)

HELP_TEXT = (
    "🎬 *Foto2Video*\n\n"
    "Mandami una *foto* con una *didascalia* che descrive il movimento, "
    "per esempio:\n"
    "_lenta carrellata in avanti, luce del tramonto, capelli mossi dal vento_\n\n"
    "Opzioni da aggiungere alla didascalia:\n"
    "• `#lungo` → ~5 secondi invece di ~3\n"
    "• `#veloce` → meno passaggi, più rapido ma meno rifinito\n\n"
    "Le didascalie in inglese funzionano meglio.\n"
    "Comandi: /help, /id"
)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    # 1) secrets.json da un dataset Kaggle allegato (i Secrets di Kaggle
    #    non sopravvivono all'avvio via API, il dataset privato sì)
    for path in sorted(glob.glob("/kaggle/input/**/secrets.json", recursive=True)):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
        print(f"[config] caricato {path}")
        break
    # 2) variabili d'ambiente (hanno la precedenza)
    for key in list(DEFAULTS) + ["TELEGRAM_TOKEN", "ALLOWED_USER_IDS", "HF_TOKEN"]:
        if os.environ.get(key):
            cfg[key] = os.environ[key]

    if not cfg.get("TELEGRAM_TOKEN"):
        sys.exit("TELEGRAM_TOKEN mancante: controlla secrets.json o le variabili d'ambiente")
    cfg["ALLOWED_USER_IDS"] = parse_ids(cfg.get("ALLOWED_USER_IDS", ""))
    for key in ("IDLE_MINUTES", "WAN_MAX_AREA", "WAN_FRAMES", "WAN_FRAMES_LONG",
                "WAN_STEPS", "WAN_STEPS_FAST"):
        cfg[key] = int(cfg[key])
    for key in ("MAX_HOURS", "WAN_GUIDANCE"):
        cfg[key] = float(cfg[key])
    return cfg


def find_local_model(root: str = "/kaggle/input") -> Optional[str]:
    """Cartella di un modello Wan allegato come dataset Kaggle (evita il download)."""
    for path in sorted(glob.glob(os.path.join(root, "**", "model_index.json"), recursive=True)):
        try:
            with open(path, encoding="utf-8") as f:
                if "Wan" in json.load(f).get("_class_name", ""):
                    return os.path.dirname(path)
        except (OSError, ValueError):
            continue
    return None


def parse_ids(value) -> set:
    if isinstance(value, (list, tuple, set)):
        return {int(v) for v in value}
    return {int(v) for v in re.findall(r"-?\d+", str(value))}


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, token: str, session: Optional[requests.Session] = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.s = session or requests.Session()

    def call(self, method: str, http_timeout: float = 60, files=None, **params):
        # http_timeout e' il timeout della richiesta HTTP; "timeout" e' un parametro
        # di Telegram (long polling) e va in params insieme agli altri.
        r = self.s.post(f"{self.base}/{method}", data=params if files else None,
                        json=None if files else params, files=files, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    def get_updates(self, offset: Optional[int], timeout: int = 30, limit: int = 100):
        params = {"timeout": timeout, "limit": limit,
                  "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", http_timeout=timeout + 15, **params)

    def confirm(self, offset: int):
        """Conferma a Telegram tutti gli update con id < offset."""
        self.call("getUpdates", offset=offset, timeout=0, limit=1)

    def send(self, chat_id: int, text: str, reply_to: Optional[int] = None,
             markdown: bool = False) -> int:
        params = {"chat_id": chat_id, "text": text}
        if reply_to:
            params["reply_to_message_id"] = reply_to
            params["allow_sending_without_reply"] = True
        if markdown:
            params["parse_mode"] = "Markdown"
        return self.call("sendMessage", **params)["message_id"]

    def edit(self, chat_id: int, message_id: int, text: str):
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
        except RuntimeError as e:
            if "not modified" not in str(e):
                print(f"[telegram] edit fallita: {e}")

    def action(self, chat_id: int, action: str = "upload_video"):
        try:
            self.call("sendChatAction", http_timeout=10, chat_id=chat_id, action=action)
        except Exception:
            pass

    def download(self, file_id: str, dest: str) -> str:
        info = self.call("getFile", file_id=file_id)
        r = self.s.get(f"{self.file_base}/{info['file_path']}", timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        return dest

    def send_video(self, chat_id: int, path: str, caption: str, reply_to: Optional[int] = None):
        with open(path, "rb") as f:
            params = {"chat_id": chat_id, "caption": caption[:1024],
                      "supports_streaming": "true"}
            if reply_to:
                params["reply_to_message_id"] = reply_to
                params["allow_sending_without_reply"] = "true"
            return self.call("sendVideo", http_timeout=300,
                             files={"video": ("video.mp4", f, "video/mp4")}, **params)


# ---------------------------------------------------------------------------
# Lavori
# ---------------------------------------------------------------------------

@dataclass
class Job:
    update_id: int
    chat_id: int
    user_id: int
    message_id: int
    file_id: str
    prompt: str
    long: bool = False
    fast: bool = False


@dataclass
class Command:
    update_id: int
    chat_id: int
    user_id: int
    message_id: int
    name: str


def parse_update(update: dict):
    """Trasforma un update Telegram in Job, Command o None."""
    msg = update.get("message")
    if not msg or "from" not in msg:
        return None
    base = dict(update_id=update["update_id"], chat_id=msg["chat"]["id"],
                user_id=msg["from"]["id"], message_id=msg["message_id"])

    file_id = None
    if msg.get("photo"):
        file_id = max(msg["photo"], key=lambda p: p.get("file_size", 0) or p["width"] * p["height"])["file_id"]
    elif msg.get("document", {}).get("mime_type", "").startswith("image/"):
        file_id = msg["document"]["file_id"]

    if file_id:
        caption = msg.get("caption", "") or ""
        tags = {t.lower() for t in re.findall(r"#(\w+)", caption)}
        prompt = re.sub(r"#\w+", "", caption).strip()
        return Job(file_id=file_id, prompt=prompt, long="lungo" in tags,
                   fast="veloce" in tags, **base)

    text = (msg.get("text") or "").strip()
    if text.startswith("/"):
        name = text.split()[0][1:].split("@")[0].lower()
        return Command(name=name, **base)
    if text:
        return Command(name="testo", **base)
    return None


def build_prompts(job_prompt: str, cfg: dict) -> tuple:
    """Prompt positivo e negativo per Wan: didascalia + personaggio fisso + negativi extra."""
    prompt = job_prompt or "cinematic shot, subtle natural motion, gentle camera movement"
    character = (cfg.get("CHARACTER_PROMPT") or "").strip()
    if character:
        prompt = f"{prompt.rstrip(' .,')}. {character}"
    negative = NEGATIVE_PROMPT
    extra = (cfg.get("NEGATIVE_EXTRA") or "").strip()
    if extra:
        negative = f"{negative}, {extra}"
    return prompt, negative


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

ProgressCb = Callable[[float], None]


def run(cmd: list):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} fallito: {p.stderr[-800:]}")
    return p


def finalize_mp4(src: str, dst: str, max_mb: float = 48.0):
    """Ricodifica in H.264 compatibile con Telegram, sotto il limite di 50 MB."""
    crf = 20
    while True:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-an", dst])
        if os.path.getsize(dst) / 1e6 <= max_mb or crf >= 36:
            return dst
        crf += 4


class KenBurnsBackend:
    """Zoom lento sulla foto con ffmpeg. Gira su CPU: serve a testare il bot."""
    name = "kenburns"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def generate(self, image_path: str, job: Job, out_path: str, progress: ProgressCb):
        seconds = 5 if job.long else 3
        fps = 24
        frames = seconds * fps
        probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height", "-of", "csv=p=0", image_path])
        w, h = (int(x) for x in probe.stdout.strip().split(",")[:2])
        scale = 720 / max(w, h)
        ow, oh = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
        progress(0.1)
        tmp = out_path + ".raw.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", image_path,
             "-vf", (f"scale={ow * 4}:{oh * 4},"
                     f"zoompan=z='min(zoom+0.0015,1.25)':d={frames}"
                     f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={ow}x{oh}:fps={fps}"),
             "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", tmp])
        progress(0.9)
        finalize_mp4(tmp, out_path)
        os.remove(tmp)
        return out_path


class WanBackend:
    """Wan 2.2 TI2V-5B (Apache 2.0) via diffusers, pensato per T4 o T4 x2."""
    name = "wan"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pipe = None
        self.te = None
        self.tok = None

    # -- caricamento -------------------------------------------------------
    def load(self, notify: Callable[[str], None]):
        if self.pipe is not None:
            return
        import torch
        from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
        from transformers import AutoTokenizer, UMT5EncoderModel

        if not torch.cuda.is_available():
            raise RuntimeError("Nessuna GPU trovata: controlla che il notebook usi la T4")

        want = self.cfg["WAN_DTYPE"]
        native_bf16 = torch.cuda.get_device_capability(0)[0] >= 8  # Ampere+; T4 e' 7.5
        if want == "bf16" or (want == "auto" and native_bf16):
            self.dtype = torch.bfloat16
        else:
            self.dtype = torch.float16  # T4 non ha bf16 nativo
        n_gpu = torch.cuda.device_count()
        local = find_local_model()
        mid = local or self.cfg["WAN_MODEL"]
        tok_kw = {"token": self.cfg.get("HF_TOKEN")} if self.cfg.get("HF_TOKEN") and not local else {}
        source = "dal dataset Kaggle" if local else "da Hugging Face (lento: considera la cache)"
        print(f"[wan] modello {mid} ({source}), {n_gpu} GPU, dtype {self.dtype}")
        notify(f"🔧 Carico il modello {source} su {n_gpu} GPU "
               f"({torch.cuda.get_device_name(0)})…")

        vae = AutoencoderKLWan.from_pretrained(mid, subfolder="vae", torch_dtype=torch.float32, **tok_kw)
        if n_gpu >= 2:
            # Text encoder (UMT5-XXL, ~11 GB) sulla seconda T4, il resto sulla prima.
            self.tok = AutoTokenizer.from_pretrained(mid, subfolder="tokenizer", **tok_kw)
            self.te = UMT5EncoderModel.from_pretrained(
                mid, subfolder="text_encoder", torch_dtype=self.dtype, **tok_kw).to("cuda:1").eval()
            pipe = WanImageToVideoPipeline.from_pretrained(
                mid, vae=vae, text_encoder=None, tokenizer=None,
                expand_timesteps=True, torch_dtype=self.dtype, **tok_kw)
            pipe.to("cuda:0")
        else:
            # Una sola GPU: i componenti salgono sulla GPU a turno.
            pipe = WanImageToVideoPipeline.from_pretrained(
                mid, vae=vae, expand_timesteps=True, torch_dtype=self.dtype, **tok_kw)
            pipe.enable_model_cpu_offload()
        fp8 = self.cfg["WAN_FP8"]
        if fp8 is True or str(fp8).lower() in ("1", "true", "on") or (fp8 == "auto" and n_gpu < 2):
            # FP8 solo come formato di memoria: il calcolo resta in fp16.
            # La T4 non ha FP8 hardware, ma dimezza la VRAM del transformer (~10 -> ~5 GB).
            pipe.transformer.enable_layerwise_casting(
                storage_dtype=torch.float8_e4m3fn, compute_dtype=self.dtype)
            print("[wan] transformer in FP8 (storage)")
        # Nota: vae.enable_slicing() qui non serve, agisce solo con batch > 1.
        try:
            pipe.vae.enable_tiling()
        except Exception:
            pass
        self.pipe = pipe

    # -- prompt su seconda GPU (stessa logica di WanPipeline._get_t5_prompt_embeds)
    def _embed(self, text: str, max_len: int = 512):
        import torch
        try:  # stessa pulizia del testo che fa la pipeline ufficiale
            from diffusers.pipelines.wan.pipeline_wan_i2v import prompt_clean
            text = prompt_clean(text)
        except Exception:
            text = " ".join(text.split())
        inp = self.tok([text], padding="max_length", max_length=max_len, truncation=True,
                       add_special_tokens=True, return_attention_mask=True, return_tensors="pt")
        ids, mask = inp.input_ids.to("cuda:1"), inp.attention_mask.to("cuda:1")
        seq_len = int(mask.gt(0).sum())
        with torch.no_grad():
            emb = self.te(ids, mask).last_hidden_state[0, :seq_len]
        emb = torch.cat([emb, emb.new_zeros(max_len - seq_len, emb.size(1))])
        return emb.unsqueeze(0).to("cuda:0", dtype=self.dtype)

    # -- generazione -------------------------------------------------------
    def generate(self, image_path: str, job: Job, out_path: str, progress: ProgressCb):
        import math
        from diffusers.utils import export_to_video
        from PIL import Image, ImageOps

        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        # Dimensioni multiple di 32 che rispettano le proporzioni della foto
        mod = 32
        ratio = image.height / image.width
        h = round(math.sqrt(self.cfg["WAN_MAX_AREA"] * ratio)) // mod * mod
        w = round(math.sqrt(self.cfg["WAN_MAX_AREA"] / ratio)) // mod * mod
        image = ImageOps.fit(image, (w, h), Image.LANCZOS)

        frames = self.cfg["WAN_FRAMES_LONG"] if job.long else self.cfg["WAN_FRAMES"]
        steps = self.cfg["WAN_STEPS_FAST"] if job.fast else self.cfg["WAN_STEPS"]
        prompt, negative = build_prompts(job.prompt, self.cfg)
        print(f"[wan] prompt: {prompt}")

        def on_step(pipe, i, t, kw):
            progress((i + 1) / steps)
            return kw

        kwargs = dict(image=image, height=h, width=w, num_frames=frames,
                      num_inference_steps=steps, guidance_scale=self.cfg["WAN_GUIDANCE"],
                      callback_on_step_end=on_step)
        if self.te is not None:
            kwargs.update(prompt_embeds=self._embed(prompt),
                          negative_prompt_embeds=self._embed(negative))
        else:
            kwargs.update(prompt=prompt, negative_prompt=negative)

        result = self.pipe(**kwargs).frames[0]
        raw = out_path + ".raw.mp4"
        export_to_video(result, raw, fps=24)
        finalize_mp4(raw, out_path)
        os.remove(raw)
        return out_path


BACKENDS = {"wan": WanBackend, "kenburns": KenBurnsBackend}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

@dataclass
class Worker:
    cfg: dict
    tg: Telegram
    backend: object
    clock: Callable[[], float] = time.time
    offset: Optional[int] = None
    started: float = field(default=0.0)
    last_activity: float = field(default=0.0)
    done: int = 0
    current_job: Optional[Job] = None

    def allowed(self, user_id: int) -> bool:
        return not self.cfg["ALLOWED_USER_IDS"] or user_id in self.cfg["ALLOWED_USER_IDS"]

    def handle_command(self, c: Command):
        if c.name == "id":
            self.tg.send(c.chat_id, f"Il tuo ID Telegram è: {c.user_id}", reply_to=c.message_id)
        elif not self.allowed(c.user_id):
            self.tg.send(c.chat_id, "⛔ Bot privato.", reply_to=c.message_id)
        elif c.name in ("start", "help", "aiuto", "testo"):
            self.tg.send(c.chat_id, HELP_TEXT, markdown=True)
        else:
            self.tg.send(c.chat_id, "Comando sconosciuto. Scrivi /help", reply_to=c.message_id)

    def handle_job(self, job: Job, position: int = 0):
        if not self.allowed(job.user_id):
            self.tg.send(job.chat_id, "⛔ Bot privato.", reply_to=job.message_id)
            return
        status_id = self.tg.send(
            job.chat_id,
            "⏳ In coda" + (f" (posizione {position + 1})" if position else "") + "…",
            reply_to=job.message_id)

        stop = threading.Event()

        def keep_typing():
            while not stop.wait(4.5):
                self.tg.action(job.chat_id)

        last = {"pct": -1, "t": 0.0}

        def progress(frac: float):
            pct = int(frac * 100)
            now = self.clock()
            if pct >= last["pct"] + 10 and now - last["t"] > 3:
                last.update(pct=pct, t=now)
                self.tg.edit(job.chat_id, status_id, f"🎬 Genero il video… {pct}%")

        tmpdir = tempfile.mkdtemp(prefix="f2v_")
        self.current_job = job
        t0 = self.clock()
        th = threading.Thread(target=keep_typing, daemon=True)
        th.start()
        try:
            # Il caricamento sta dentro il try: se fallisce (dipendenze, download)
            # l'utente riceve l'errore e il fallback, invece di perdere la foto.
            if hasattr(self.backend, "load"):
                first = getattr(self.backend, "pipe", None) is None
                t_load = self.clock()
                self.backend.load(lambda t: self.tg.edit(job.chat_id, status_id, t))
                if first:
                    print(f"[tempi] modello caricato in {(self.clock() - t_load) / 60:.1f} min")
            t_gen = self.clock()
            self.tg.edit(job.chat_id, status_id, "🎬 Genero il video… 0%")
            img = self.tg.download(job.file_id, os.path.join(tmpdir, "input.jpg"))
            out = self.backend.generate(img, job, os.path.join(tmpdir, "output.mp4"), progress)
            stop.set()
            mins = (self.clock() - t0) / 60
            print(f"[tempi] video generato in {(self.clock() - t_gen) / 60:.1f} min "
                  f"(job totale {mins:.1f} min)")
            caption = f"✅ Fatto in {mins:.1f} min" + (f"\n📝 {job.prompt}" if job.prompt else "")
            self.tg.send_video(job.chat_id, out, caption, reply_to=job.message_id)
            self.tg.edit(job.chat_id, status_id, "✅ Video pronto")
            self.done += 1
        except Exception as e:  # noqa: BLE001 - qualunque errore va riportato in chat
            stop.set()
            msg = str(e)
            oom = "out of memory" in msg.lower()
            if oom:
                msg = "memoria GPU esaurita. Riprova senza #lungo."
            print(f"[job {job.update_id}] errore: {e!r}")
            self.tg.edit(job.chat_id, status_id, f"❌ Errore: {msg[:300]}")
            if self.cfg.get("FALLBACK_KENBURNS") and not isinstance(self.backend, KenBurnsBackend):
                self._fallback(job, tmpdir)
        finally:
            self.current_job = None
            shutil.rmtree(tmpdir, ignore_errors=True)
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

    def _fallback(self, job: Job, tmpdir: str):
        """Anteprima Ken Burns quando il modello IA fallisce."""
        try:
            img = os.path.join(tmpdir, "input.jpg")
            if not os.path.exists(img):
                self.tg.download(job.file_id, img)
            out = KenBurnsBackend(self.cfg).generate(
                img, job, os.path.join(tmpdir, "fallback.mp4"), lambda _: None)
            self.tg.send_video(job.chat_id, out,
                               "⚠️ La generazione IA non è riuscita: ecco un'anteprima Ken Burns.",
                               reply_to=job.message_id)
        except Exception as e:  # noqa: BLE001
            print(f"[job {job.update_id}] anche il fallback è fallito: {e!r}")

    def on_terminate(self, *_):
        """SIGTERM/SIGINT: Kaggle chiude la sessione. Avvisa chi stava aspettando."""
        job = self.current_job
        if job:
            try:
                self.tg.send(job.chat_id, "⛔ La sessione GPU è stata chiusa durante il video. "
                             "Rimanda la foto e riparto.", reply_to=job.message_id)
            except Exception:
                pass
        print("[worker] terminato dal sistema")
        sys.exit(0)

    def should_stop(self) -> Optional[str]:
        now = self.clock()
        if now - self.last_activity > self.cfg["IDLE_MINUTES"] * 60:
            return f"nessuna richiesta da {self.cfg['IDLE_MINUTES']} minuti"
        if now - self.started > self.cfg["MAX_HOURS"] * 3600:
            return "limite di sessione raggiunto"
        return None

    def poll_once(self, timeout: int = 30) -> int:
        """Una tornata di polling. Ritorna il numero di update gestiti."""
        updates = self.tg.get_updates(self.offset, timeout=timeout)
        items = [(u["update_id"], parse_update(u)) for u in updates]
        jobs = [it for _, it in items if isinstance(it, Job)]
        for update_id, item in items:
            # Conferma PRIMA di elaborare: se il notebook muore durante un
            # video, al riavvio non rifà all'infinito lo stesso lavoro.
            self.offset = update_id + 1
            self.tg.confirm(self.offset)
            self.last_activity = self.clock()
            if isinstance(item, Command):
                self.handle_command(item)
            elif isinstance(item, Job):
                self.handle_job(item, position=jobs.index(item))
            self.last_activity = self.clock()
        return len(updates)

    def run(self):
        self.started = self.last_activity = self.clock()
        print(f"[worker] avviato con backend '{self.backend.name}'")
        while True:
            try:
                self.poll_once()
            except requests.RequestException as e:
                print(f"[worker] rete: {e}; riprovo tra 5 s")
                time.sleep(5)
            except RuntimeError as e:
                print(f"[worker] {e}; riprovo tra 10 s")
                time.sleep(10)
            reason = self.should_stop()
            if reason:
                print(f"[worker] mi spengo: {reason}. Video generati: {self.done}")
                return


def ensure_deps(backend: str):
    """Su Kaggle installa le librerie giuste (Wan 2.2 richiede diffusers recente)."""
    if backend != "wan" or not os.path.isdir("/kaggle"):
        return
    print("[setup] installo diffusers/transformers aggiornati…")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U",
                    "git+https://github.com/huggingface/diffusers",
                    "transformers", "accelerate", "ftfy", "imageio", "imageio-ffmpeg"],
                   check=True)
    # Il torchao preinstallato su Kaggle e' piu' vecchio di quello che diffusers main
    # si aspetta (ImportError su FqnToConfig). Non lo usiamo: senza, diffusers lo salta.
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torchao"], check=False)


def main():
    cfg = load_config()
    if cfg["BACKEND"] not in BACKENDS:
        sys.exit(f"BACKEND sconosciuto: {cfg['BACKEND']} (scegli tra {', '.join(BACKENDS)})")
    os.environ.setdefault("HF_HOME", "/tmp/hf")  # disco temporaneo, più capiente di /kaggle/working
    tg = Telegram(cfg["TELEGRAM_TOKEN"])
    try:
        ensure_deps(cfg["BACKEND"])
        worker = Worker(cfg=cfg, tg=tg, backend=BACKENDS[cfg["BACKEND"]](cfg))
        signal.signal(signal.SIGTERM, worker.on_terminate)
        signal.signal(signal.SIGINT, worker.on_terminate)
        worker.run()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        abort_startup(tg, cfg, e)
        raise


def abort_startup(tg: Telegram, cfg: dict, error: Exception):
    """Errore fatale prima/fuori dal ciclo: avvisa e consuma le foto in attesa.

    Senza questo, il dispatcher vedrebbe ancora le foto in coda e riavvierebbe
    il notebook ogni 10 minuti, fallendo ogni volta.
    """
    print(f"[worker] errore fatale: {error!r}")
    try:
        updates = tg.get_updates(None, timeout=0)
        chats = {u["message"]["chat"]["id"] for u in updates if "message" in u}
        if updates:
            tg.confirm(updates[-1]["update_id"] + 1)
        for chat_id in chats or cfg["ALLOWED_USER_IDS"]:
            tg.send(chat_id, f"❌ Il worker non è partito: {str(error)[:300]}\n"
                             "Controlla i log su Kaggle, poi rimanda la foto.")
    except Exception as e:  # noqa: BLE001
        print(f"[worker] impossibile avvisare su Telegram: {e!r}")


if __name__ == "__main__":
    main()
