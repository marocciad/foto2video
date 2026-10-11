"""Test senza rete: Telegram finto, backend Ken Burns reale (ffmpeg)."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "worker"), os.path.join(ROOT, "dispatcher")]

import dispatch  # noqa: E402
import worker as w  # noqa: E402

ME, STRANGER = 111, 999


def photo_update(uid, user=ME, caption="slow dolly in #lungo"):
    return {"update_id": uid, "message": {
        "message_id": uid * 10, "chat": {"id": user}, "from": {"id": user},
        "caption": caption,
        "photo": [{"file_id": "small", "width": 90, "height": 160, "file_size": 1000},
                  {"file_id": "big", "width": 720, "height": 1280, "file_size": 90000}]}}


def text_update(uid, text, user=ME):
    return {"update_id": uid, "message": {
        "message_id": uid * 10, "chat": {"id": user}, "from": {"id": user}, "text": text}}


class FakeTelegram:
    def __init__(self, updates, image_path):
        self.pending = list(updates)
        self.image_path = image_path
        self.log = []
        self.videos = []
        self.confirmed = 0

    def get_updates(self, offset, timeout=30, limit=100):
        return [u for u in self.pending if offset is None or u["update_id"] >= offset]

    def confirm(self, offset):
        self.confirmed = offset
        self.pending = [u for u in self.pending if u["update_id"] >= offset]

    def send(self, chat_id, text, reply_to=None, markdown=False):
        self.log.append(("send", chat_id, text))
        return len(self.log)

    def edit(self, chat_id, mid, text):
        self.log.append(("edit", chat_id, text))

    def action(self, chat_id, action="upload_video"):
        pass

    def download(self, file_id, dest):
        assert file_id == "big", "deve scaricare la foto alla risoluzione massima"
        import shutil
        shutil.copy(self.image_path, dest)
        return dest

    def send_video(self, chat_id, path, caption, reply_to=None):
        size = os.path.getsize(path)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "format=duration:stream=codec_name,width,height,pix_fmt",
                                "-of", "default=nw=1", path], capture_output=True, text=True).stdout
        self.videos.append((chat_id, size, probe, caption))


def make_image(path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=720x1280:duration=1", "-frames:v", "1", path], check=True)


def cfg(**kw):
    c = dict(w.DEFAULTS)
    c.update(TELEGRAM_TOKEN="x", ALLOWED_USER_IDS={ME}, BACKEND="kenburns")
    c.update(kw)
    return c


def test_parse():
    j = w.parse_update(photo_update(1))
    assert isinstance(j, w.Job) and j.file_id == "big" and j.long and not j.fast
    assert j.prompt == "slow dolly in"
    c = w.parse_update(text_update(2, "/help@Foto2VideoBot"))
    assert isinstance(c, w.Command) and c.name == "help"
    assert w.parse_ids("123, -45") == {123, -45}
    assert w.parse_ids([1, "2"]) == {1, 2}


def test_worker_end_to_end(tmp_path):
    tmp = str(tmp_path)
    img = os.path.join(tmp, "in.png")
    make_image(img)
    updates = [text_update(1, "/start"), photo_update(2), photo_update(3, user=STRANGER),
               text_update(4, "/id", user=STRANGER)]
    tg = FakeTelegram(updates, img)
    clock = [1000.0]
    wk = w.Worker(cfg=cfg(), tg=tg, backend=w.KenBurnsBackend(cfg()), clock=lambda: clock[0])
    wk.started = wk.last_activity = clock[0]
    n = wk.poll_once(timeout=0)
    assert n == 4
    assert tg.confirmed == 5 and tg.pending == []
    assert len(tg.videos) == 1, tg.log
    chat, size, probe, caption = tg.videos[0]
    assert chat == ME and size < 48e6
    assert "codec_name=h264" in probe and "pix_fmt=yuv420p" in probe
    assert "width=404" in probe or "width=" in probe
    dur = float([l for l in probe.splitlines() if l.startswith("duration=")][0].split("=")[1])
    assert 4.5 <= dur <= 5.5, dur  # #lungo -> ~5 s
    texts = [t for _, _, t in tg.log]
    assert any("Foto2Video" in t for t in texts), "manca l'aiuto per /start"
    assert any("Bot privato" in t for t in texts), "lo sconosciuto non va servito"
    assert any(f"ID Telegram è: {STRANGER}" in t for t in texts), "/id deve rispondere a tutti"
    # spegnimento per inattività
    assert wk.should_stop() is None
    clock[0] += 11 * 60
    assert "minuti" in wk.should_stop()


def test_error_reported(tmp_path):
    tmp = str(tmp_path)
    class Boom:
        name = "boom"

        def generate(self, *a):
            raise RuntimeError("CUDA out of memory. Tried to allocate")
    img = os.path.join(tmp, "in.png")
    make_image(img)
    tg = FakeTelegram([photo_update(7)], img)
    wk = w.Worker(cfg=cfg(), tg=tg, backend=Boom())
    wk.started = wk.last_activity = 0
    wk.poll_once(timeout=0)
    assert any("memoria GPU esaurita" in t for _, _, t in tg.log)
    assert tg.confirmed == 8, "il lavoro fallito va comunque confermato (niente loop)"


def test_dispatch_decide():
    allowed = {ME}
    assert dispatch.decide([photo_update(1)], allowed, "running") == "skip"
    assert dispatch.decide([photo_update(1)], allowed, "queued") == "skip"
    assert dispatch.decide([], allowed, "complete") == "idle"
    assert dispatch.decide([photo_update(1)], allowed, "complete") == "start"
    assert dispatch.decide([photo_update(1)], allowed, "missing") == "start"
    assert dispatch.decide([photo_update(1, user=STRANGER)], allowed, "complete") == "reply"
    assert dispatch.decide([text_update(1, "/help")], allowed, "error") == "reply"
    voice = {"update_id": 2, "message": {"message_id": 20, "chat": {"id": ME}, "from": {"id": ME},
                                         "voice": {"file_id": "v", "duration": 5}}}
    assert dispatch.decide([text_update(1, "/help"), voice], allowed, "complete") == "idle"


def test_fallback_kenburns_on_failure(tmp_path):
    class Boom:
        name = "wan"

        def generate(self, *a):
            raise RuntimeError("CUDA out of memory")
    img = os.path.join(str(tmp_path), "in.png")
    make_image(img)
    tg = FakeTelegram([photo_update(9, caption="test")], img)
    wk = w.Worker(cfg=cfg(FALLBACK_KENBURNS=True), tg=tg, backend=Boom())
    wk.started = wk.last_activity = 0
    wk.poll_once(timeout=0)
    assert len(tg.videos) == 1 and "Ken Burns" in tg.videos[0][3]
    assert wk.current_job is None


def test_fallback_when_model_load_fails(tmp_path):
    """Errore nel caricamento del modello (es. import di diffusers): errore in chat + Ken Burns."""
    class BadLoad:
        name = "wan"

        def load(self, notify):
            raise RuntimeError("Failed to import diffusers.models.autoencoders.autoencoder_kl_wan")

        def generate(self, *a):
            raise AssertionError("non deve arrivare qui")
    img = os.path.join(str(tmp_path), "in.png")
    make_image(img)
    tg = FakeTelegram([photo_update(9, caption="test")], img)
    wk = w.Worker(cfg=cfg(FALLBACK_KENBURNS=True), tg=tg, backend=BadLoad())
    wk.started = wk.last_activity = 0
    wk.poll_once(timeout=0)
    assert len(tg.videos) == 1 and "Ken Burns" in tg.videos[0][3]
    assert any(e[0] == "edit" and "Failed to import" in e[2] for e in tg.log)
    assert wk.current_job is None


def test_abort_startup_consumes_queue(tmp_path):
    img = os.path.join(str(tmp_path), "in.png")
    make_image(img)
    tg = FakeTelegram([photo_update(20), photo_update(21)], img)
    w.abort_startup(tg, cfg(), RuntimeError("pip install fallito"))
    assert tg.confirmed == 22 and tg.pending == [], "le foto vanno consumate, niente loop"
    assert any("non è partito" in t for _, _, t in tg.log)


def test_find_local_model(tmp_path):
    d = tmp_path / "wan22-ti2v-5b-fp16" / "wan_fp16"
    d.mkdir(parents=True)
    (d / "model_index.json").write_text('{"_class_name": "WanPipeline"}')
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "model_index.json").write_text('{"_class_name": "FluxPipeline"}')
    assert w.find_local_model(str(tmp_path)) == str(d)
    assert w.find_local_model(str(tmp_path / "other")) is None


def test_dispatch_unknown_status_is_safe():
    assert dispatch.decide([photo_update(1)], {ME}, "unknown:boh") == "skip"


def test_dispatch_classify_status():
    c = dispatch.classify_status
    assert c('u/k has status "KernelWorkerStatus.RUNNING"') == "running"
    assert c("Cannot access kernel 'u/k' (Permission 'kernels.get' was denied).") == "missing"
    assert c("404 Client Error: Not Found") == "missing"
    assert c("401 Client Error: Unauthorized").startswith("unknown")
    assert c("risposta strana").startswith("unknown")


def test_telegram_client_params():
    """Il client vero (non il finto): 'timeout' di Telegram non va confuso con quello HTTP."""
    class Resp:
        def json(self):
            return {"ok": True, "result": []}

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, url, data=None, json=None, files=None, timeout=None):
            self.calls.append((url.rsplit("/", 1)[1], json, timeout))
            return Resp()

    s = Session()
    tg = w.Telegram("123:abc", session=s)
    tg.get_updates(7, timeout=30)
    tg.confirm(8)
    assert s.calls[0] == ("getUpdates", {"timeout": 30, "limit": 100,
                                          "allowed_updates": ["message"], "offset": 7}, 45)
    method, params, http_timeout = s.calls[1]
    assert params["timeout"] == 0 and params["offset"] == 8 and http_timeout > 0


def test_build_prompts():
    p, n = w.build_prompts("he raises a hand.", {})
    assert p == "he raises a hand." and n == w.NEGATIVE_PROMPT
    p, n = w.build_prompts("he raises a hand.", {"CHARACTER_PROMPT": "rigid white mask",
                                                  "NEGATIVE_EXTRA": "smiling mask"})
    assert p == "he raises a hand. rigid white mask"
    assert n.startswith(w.NEGATIVE_PROMPT) and n.endswith(", smiling mask")
    assert w.build_prompts("", {})[0]  # senza didascalia c'e' comunque un prompt


if __name__ == "__main__":
    import tempfile
    test_parse()
    test_dispatch_decide()
    with tempfile.TemporaryDirectory() as d:
        test_worker_end_to_end(d)
        test_error_reported(d)
    print("TUTTI I TEST OK")
