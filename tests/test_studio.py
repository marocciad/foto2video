"""Test dello studio (regia virtuale): coda Telegram, allineamento del copione, sottotitoli, render reale."""
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from studio import audio, regia  # noqa: E402
from studio import __main__ as bot  # noqa: E402

ME, STRANGER = 111, 999
NOW = 10_000


def msg(uid, date, user=ME, **kw):
    return {"update_id": uid, "message": {"message_id": uid * 10, "date": date, "chat": {"id": user},
                                          "from": {"id": user}, **kw}}


def voice(uid, date, user=ME):
    return msg(uid, date, user, voice={"file_id": f"v{uid}", "duration": 20})


def text(uid, date, t, user=ME):
    return msg(uid, date, user, text=t)


def photo(uid, date, user=ME):
    return msg(uid, date, user, photo=[{"file_id": "p", "width": 10, "height": 10}])


# --- coda ---------------------------------------------------------------------

def test_voice_paired_with_script_sent_after():
    jobs = bot.plan_queue([voice(1, NOW - 30), text(2, NOW - 10, "Ciao a tutti")], {ME}, NOW)
    assert len(jobs) == 1 and jobs[0]["tipo"] == "video"
    assert jobs[0]["copione"]["text"] == "Ciao a tutti" and sorted(jobs[0]["ids"]) == [1, 2]


def test_voice_paired_with_script_sent_before():
    jobs = bot.plan_queue([text(1, NOW - 300, "Copione"), voice(2, NOW - 5)], {ME}, NOW)
    assert [j["tipo"] for j in jobs] == ["video"] and jobs[0]["copione"]["text"] == "Copione"


def test_fresh_voice_without_script_waits():
    assert bot.plan_queue([voice(1, NOW - 20)], {ME}, NOW) == []
    jobs = bot.plan_queue([voice(1, NOW - 200)], {ME}, NOW)  # dopo l'attesa si parte senza copione
    assert jobs[0]["tipo"] == "video" and jobs[0]["copione"] is None


def test_script_waits_for_its_voice_then_gives_up():
    assert bot.plan_queue([text(1, NOW - 60, "Copione")], {ME}, NOW) == []
    jobs = bot.plan_queue([text(1, NOW - 2000, "Copione")], {ME}, NOW)
    assert jobs[0]["tipo"] == "risposta" and "nessun vocale" in jobs[0]["testo"]


def test_stops_at_photo_for_kaggle():
    jobs = bot.plan_queue([text(1, NOW, "/help"), photo(2, NOW), voice(3, NOW - 500)], {ME}, NOW)
    assert [j["tipo"] for j in jobs] == ["risposta"]  # la foto e cio' che segue restano in coda


def test_strangers_and_commands():
    jobs = bot.plan_queue([voice(1, NOW - 500, user=STRANGER), text(2, NOW, "/id")], {ME}, NOW)
    assert jobs[0]["testo"] == "⛔ Bot privato."
    assert jobs[1]["testo"] == f"Il tuo ID Telegram è: {ME}"


def test_menu_commands_have_texts():
    jobs = bot.plan_queue([text(1, NOW, "/formati"), text(2, NOW, "/voce@foto2videobot"), text(3, NOW, "/start")],
                          {ME}, NOW)
    assert [j["testo"] for j in jobs] == [bot.FORMATS_HELP, bot.VOICE_HELP, bot.HELP]
    assert all(j["markdown"] for j in jobs)
    # ogni comando del menu ha una risposta (id a parte, che e' dinamico)
    assert {c for c, _ in bot.COMMANDS} - {"id"} <= set(bot.COMMAND_TEXTS)


def test_confirm_offset_only_contiguous_prefix():
    assert bot.confirm_offset([5, 6, 7], {5, 7}) == 6
    assert bot.confirm_offset([5, 6, 7], {5, 6, 7}) == 8
    assert bot.confirm_offset([5, 6, 7], {6}) is None


def test_parse_tags():
    assert bot.parse_tags("Ciao #verticale") == ("Ciao", ["9x16"], False)
    assert bot.parse_tags("#nointro Ciao")[2] is True
    assert bot.parse_tags("")[1] == ["9x16", "16x9", "1x1"]
    assert bot.parse_tags("Ciao #quadrato")[1] == ["1x1"]


# --- parole e frasi ---------------------------------------------------------------

def W(w, a, b):
    return {"w": w, "a": a, "b": b}


def test_align_script_takes_text_from_script_and_times_from_whisper():
    heard = [W("Solo", 1.0, 1.3), W("matematico.", 1.4, 2.0), W("BetForge", 3.0, 3.5), W("parlano.", 4.2, 4.8)]
    out = audio.align_script("Solo matematica. BetForge. I numeri parlano.", heard)
    assert [w["w"] for w in out] == ["Solo", "matematica.", "BetForge.", "I", "numeri", "parlano."]
    assert out[1]["a"] == 1.4 and out[1]["b"] == 2.0          # sostituzione: tempi della parola sentita
    assert 3.5 <= out[3]["a"] <= out[4]["a"] <= 4.2           # parole mancanti: tra le vicine
    assert all(w["b"] > w["a"] for w in out)


def test_fix_brand():
    out = audio.fix_brand([W("Bet", 0, 0.2), W("Forge,", 0.2, 0.5), W("betforg", 1, 1.4)])
    assert [w["w"] for w in out] == ["BetForge,", "BetForge"] and out[0]["b"] == 0.5


def test_split_phrases():
    ws = [W("Benvenuti…", 0, 1), W("questo", 2.2, 2.5), W("è", 2.5, 2.6), W("BetForge.", 2.6, 3.2),
          W("Gol", 4, 4.2), W("attesi,", 4.2, 4.6), W("forma,", 4.7, 5.0)]
    ph = audio.split_phrases(ws)
    assert [p["testo"] for p in ph] == ["Benvenuti…", "questo è BetForge.", "Gol attesi, forma,"]
    assert ph[1]["inizio"] == 2.2 and ph[1]["fine"] == 3.2


# --- regia ------------------------------------------------------------------------

def test_assign_shots_intro_rotation_and_close():
    plan = ["intro1", "intro2", "a", "b", "close"]
    assert regia.assign_shots(7, plan) == ["intro1", "intro2", "a", "b", "a", "b", "close"]
    assert regia.assign_shots(2, plan) == ["intro1", "intro2"]


def test_build_ass_no_overlaps_and_changing_fonts():
    ws = [W(f"parola{i}", i * 0.5, i * 0.5 + 0.4) for i in range(12)]
    ass = regia.build_ass(audio.split_phrases(ws, max_words=3), (720, 1280), True, {"parola3"})
    events = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert events
    fonts = {line.split(r"\fn")[1].split("\\")[0] for line in events}
    assert len(fonts) >= 3  # il font cambia tra un gruppo e l'altro

    def secs(t):
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    spans = [(secs(e.split(",")[1]), secs(e.split(",")[2])) for e in events]
    assert all(b0 <= a1 for (_, b0), (a1, _) in zip(spans, spans[1:]))


def _fake_assets(d):
    """Personaggio finto: due immagini con dettagli verdi, logo su nero, configurazione."""
    for name, size in (("largo.png", (320, 180)), ("alto.png", (180, 320))):
        im = Image.new("RGB", size, (40, 30, 20))
        ImageDraw.Draw(im).rectangle([60, 60, 80, 120], fill=(30, 220, 60))
        im.save(os.path.join(d, name))
    logo = Image.new("RGB", (200, 200), (0, 0, 0))
    ImageDraw.Draw(logo).rectangle([40, 80, 160, 120], fill=(240, 200, 60))
    logo.save(os.path.join(d, "logo.png"))
    shots = [["largo.png", 160, 90, 180, 1, 1.1, 0, "crop"], ["largo.png", 160, 90, 180, 1, 1, 0, "fit"],
             ["alto.png", 90, 160, 320, 1, 1.1, 0, "crop"], ["largo.png", 160, 90, 180, 1.1, 1, 0, "crop"]]
    cfg = {"logo": "logo.png", "inquadrature": {"9x16": shots, "16x9": shots, "1x1": shots}}
    with open(os.path.join(d, "personaggio.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return cfg


def _duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    return float(out)


def test_render_square(tmp_path):
    d = str(tmp_path)
    _fake_assets(d)
    wav = os.path.join(d, "voce.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=f=220:d=1.0:sample_rate=48000", "-ac", "1", wav], check=True)
    out = os.path.join(d, "q.mp4")
    r = regia.Renderer(d, regia.load_character(d), "1x1", size=(240, 240))
    r.render(wav, audio.split_phrases([W("Ciao", 0.1, 0.5), W("BetForge.", 0.6, 0.9)]), out, set(), fps=10)
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height", "-of", "csv=p=0", out], capture_output=True, text=True).stdout
    assert size.strip() == "240,240"


def test_render_end_to_end(tmp_path):
    d = str(tmp_path)
    cfg = _fake_assets(d)
    wav = os.path.join(d, "voce.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=f=220:d=2.0:sample_rate=48000", "-ac", "1", wav], check=True)
    phrases = audio.split_phrases([W("Benvenuti…", 0.1, 0.6), W("questo", 0.9, 1.1), W("è", 1.1, 1.2),
                                   W("BetForge.", 1.2, 1.8)])
    out = os.path.join(d, "video.mp4")
    r = regia.Renderer(d, regia.load_character(d), "9x16", size=(180, 320))
    total = r.render(wav, phrases, out, {"betforge"}, fps=10)
    assert abs(total - (2.0 + regia.END)) < 0.05
    first = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", out, "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    assert np.frombuffer(first, np.uint8).mean() > 10  # immagine vera, non nera/corrotta
    assert abs(_duration(out) - total) < 0.3
    assert not os.path.exists(out + ".ass")


def test_audio_chain(tmp_path):
    """Pulizia, unione con l'intro e voce 3 su un segnale sintetico (serve ffmpeg con rubberband)."""
    d = str(tmp_path)
    src = os.path.join(d, "src.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=f=300:d=1.5:sample_rate=48000,volume=0.3", "-ac", "1", src], check=True)
    a, b, both, wet = (os.path.join(d, n) for n in ("a.wav", "b.wav", "both.wav", "wet.wav"))
    audio.clean(src, a)
    audio.clean(src, b)
    audio.concat([a, b], both, gap=0.8)
    assert abs(len(audio.load_pcm(both)) / 48000 - (len(audio.load_pcm(a)) * 2 / 48000 + 0.8)) < 0.05
    audio.voice3(both, wet)
    assert abs(audio.measure_lufs(wet) + 13) < 2.0
    assert np.abs(audio.load_pcm(wet)).max() < 0.9


# --- voce sintetica (#voce) -----------------------------------------------------

def test_tts_text_starts_immediately_and_is_not_paired():
    jobs = bot.plan_queue([text(1, NOW - 5, "Ciao a tutti #voce"), voice(2, NOW - 3)], {ME}, NOW)
    assert jobs[0]["tipo"] == "video" and jobs[0]["voce"] is None and jobs[0]["ids"] == [1]
    assert len(jobs) == 1  # il vocale appena arrivato aspetta il suo copione, non usa quello con #voce


def test_tts_segments_brand_in_english():
    from studio import tts
    segs = tts.segments("Benvenuti... questo è BetForge. BetForge trasforma i dati.")
    assert [(lang, t) for lang, t, _ in segs] == [
        ("it", "Benvenuti…"), ("it", "questo è"), ("en", "BetForge"), ("en", "BetForge"),
        ("it", "trasforma i dati.")]
    assert segs[0][2] == tts.PAUSE_SUSPENDED and segs[1][2] == tts.GAP_WORD


def test_tts_synthesis(tmp_path):
    import pytest
    pytest.importorskip("kokoro")
    from studio import tts
    out = os.path.join(str(tmp_path), "tts.wav")
    tts.synthesize("Questo è BetForge.", out)
    assert len(audio.load_pcm(out)) / 48000 > 0.5


# --- copione proposto (#idea) ---------------------------------------------------

def test_idea_becomes_script_job():
    jobs = bot.plan_queue([text(1, NOW - 5, "#idea parla dei gol attesi")], {ME}, NOW)
    assert jobs[0]["tipo"] == "copione" and jobs[0]["ids"] == [1]


def test_risky_terms_ignore_brand():
    from studio import copione
    assert copione.risky_terms("BetForge analizza i dati. I numeri parlano.") == []
    assert set(copione.risky_terms("Punta sulle quote e vinci soldi")) >= {"punta", "quot", "vinc", "soldi"}


def test_clean_and_score():
    from studio import copione
    assert copione.clean('Copione: **"Benvenuti… Io guardo i numeri."** #calcio') == "Io guardo i numeri."
    assert copione.score_script(" ".join(["parola"] * 60), []) == (0, 0)
    assert copione.score_script(" ".join(["parola"] * 30), []) == (0, 20)


def test_script_reply_is_copyable_and_warns():
    msg = bot.script_reply("Io guardo i numeri.", ["vinc"])
    assert "```\nIo guardo i numeri.\n```" in msg and "⚠️" in msg and "vinc" in msg


# --- grafiche HyperFrames (logica pura) -------------------------------------------

def test_parse_markup_clean_text_and_anchor_at_sentence_start():
    from studio import grafica
    text, items, errors = grafica.parse_markup(
        "Io guardo i numeri. Migliaia di partite analizzate. {numero: 12.480+ | partite analizzate} "
        "Gol attesi {barre: Inter 1,8 - Milan 0,9 | gol attesi} ogni giorno.")
    assert text == "Io guardo i numeri. Migliaia di partite analizzate. Gol attesi ogni giorno."
    words = text.split()
    assert [words[i] for i, _ in items] == ["Migliaia", "Gol"]
    num, bars = items[0][1], items[1][1]
    assert num == {"tipo": "contatore", "valore": 12480.0, "decimali": 0, "suffisso": "+",
                   "etichetta": "PARTITE ANALIZZATE"}
    assert bars["voci"] == [{"nome": "INTER", "valore": 1.8}, {"nome": "MILAN", "valore": 0.9}]
    assert bars["kicker"] == "GOL ATTESI" and errors == []


def test_parse_markup_forma_rating_and_errors():
    from studio import grafica
    text, items, errors = grafica.parse_markup("Forma {forma: WWDLW | Inter} e rating {rating: 87} e {barre: Inter - Milan}")
    assert text == "Forma e rating e"
    assert items[0][1]["sequenza"] == "VVNPV" and items[0][1]["kicker"] == "FORMA INTER"
    assert items[1][1]["valore"] == 87 and items[1][1]["kicker"] == "RATING"
    assert len(errors) == 1 and "Inter - Milan" in errors[0]


def test_markup_hits_offset_and_schedule():
    from studio import grafica
    words = [W(f"p{i}", i * 1.0, i * 1.0 + 0.5) for i in range(12)]
    hits = grafica.markup_hits([(0, {"tipo": "rating", "valore": 80}), (1, {"tipo": "forma", "sequenza": "V"})],
                               words, offset=4)
    assert [h["t"] for h in hits] == [3.9, 4.9]
    ev = grafica.schedule(hits, 11.0, logo="logo.png")
    # il rating avrebbe solo 1 s prima della forma (sotto MIN_DUR): si salta, la forma resta 2,6 s
    assert [e["tipo"] for e in ev] == ["forma", "chiusura"]
    assert ev[0]["durata"] == grafica.MAX_DUR and ev[-1]["t"] == 11.0


def test_overlay_config_cuts_opening_and_captions():
    from studio import grafica
    ph = audio.split_phrases([W("Benvenuti…", 0, 1), W("questo", 2.2, 2.5), W("è", 2.5, 2.6), W("BetForge.", 2.6, 3.2),
                              W("Gol", 4, 4.2), W("attesi.", 4.2, 4.6)])
    cfg = grafica.overlay_config(ph, (720, 1280), {"gol"}, [0.0, 2.1, 3.9], intro=True)
    assert cfg["tagli"] == [2.1, 3.9]
    assert cfg["apertura"] == {"fine_benvenuti": 1, "inizio_brand": 2.2, "fine_brand": 3.2}
    groups = cfg["sottotitoli"]
    assert all(a["fine"] <= b["inizio"] for a, b in zip(groups, groups[1:]))
    gol = [w for g in groups for w in g["parole"] if w["testo"] == "GOL"][0]
    assert gol["enfasi"] and gol["k"] == 1.38
    brand = [w for g in groups for w in g["parole"] if "BETFORGE" in w["testo"]][0]
    assert brand["brand"] and not brand["enfasi"]
    assert grafica.overlay_config(ph, (720, 1280), set(), [], intro=False).get("apertura") is None


def test_app_markup_and_screen_resolution():
    from studio import grafica
    text, items, errors = grafica.parse_markup("Guarda l'app. {app: calendario, analisi | l'app} Fine. {app}")
    assert text == "Guarda l'app. Fine." and errors == []
    assert items[0][1] == {"tipo": "app", "schermate": ["calendario", "analisi"], "kicker": "L'APP"}
    assert items[1][1]["schermate"] == []  # vuoto = tutte quelle disponibili
    events = [dict(items[0][1], t=0.0, durata=4.0), dict(items[1][1], t=5.0, durata=3.0),
              {"tipo": "app", "schermate": ["inesistente"], "t": 9.0, "durata": 2.0}]
    needed = grafica.resolve_app_screens(events, {"calendario": "/a/cal.png"})
    assert needed == {"app/calendario.png": "calendario"}
    assert [e["schermate"] for e in events] == [["app/calendario.png"], ["app/calendario.png"]]
    assert grafica.schedule([{"tipo": "app", "schermate": [], "t": 1.0}], 20.0)[0]["durata"] == 4.5


def test_map_markup():
    from studio import grafica
    text, items, errors = grafica.parse_markup(
        "Campionati. {mappa: Italia, Inghilterra, Scozia | campionati analizzati} E poi {mappa: Atlantide}")
    assert text == "Campionati. E poi" and len(items) == 1
    ev = items[0][1]
    assert ev["kicker"] == "CAMPIONATI ANALIZZATI"
    assert [p["id"] for p in ev["paesi"]] == ["380", "826", "826"]  # Scozia = Regno Unito
    assert "Atlantide" in errors[0]
    assert grafica.schedule([dict(ev, t=1.0)], 20.0)[0]["durata"] == grafica.DUR_BY_TYPE["mappa"]


# --- carosello ---------------------------------------------------------------------

def test_parse_carousel_slides_and_limit():
    from studio import grafica
    slides, errors = grafica.parse_carousel(
        "I 3 numeri\n\nGol attesi {barre: Inter 1,8 - Milan 0,9 | gol attesi}\n{forma: VVX}\nScarica BetForge")
    assert [s["testo"] for s in slides] == ["I 3 numeri", "Gol attesi", "", "Scarica BetForge"]
    assert slides[1]["grafica"]["tipo"] == "barre" and slides[0]["grafica"] is None
    assert slides[2]["grafica"]["sequenza"] == "VV"
    many, errors = grafica.parse_carousel("\n".join(f"slide {i}" for i in range(14)))
    assert len(many) == grafica.MAX_SLIDES and many[-1]["testo"] == "slide 13" and errors


def test_carousel_job_in_queue():
    jobs = bot.plan_queue([text(1, NOW - 5, "#carosello\nTitolo\nFine")], {ME}, NOW)
    assert jobs[0]["tipo"] == "carosello" and jobs[0]["ids"] == [1]


def test_send_photos_builds_album(tmp_path, monkeypatch):
    import json as js
    paths = []
    for i in range(3):
        p = os.path.join(str(tmp_path), f"s{i}.png")
        Image.new("RGB", (4, 4)).save(p)
        paths.append(p)
    sent = {}

    class Resp:
        def json(self):
            return {"ok": True, "result": []}

    def fake_post(url, data=None, json=None, files=None, timeout=None):
        sent.update(url=url, data=data, files=files)
        return Resp()
    monkeypatch.setattr(bot.requests, "post", fake_post)
    bot.Telegram("123:abc").send_photos(7, paths, caption="Pronto", reply_to=5)
    media = js.loads(sent["data"]["media"])
    assert sent["url"].endswith("/sendMediaGroup") and len(media) == 3 and set(sent["files"]) == {"p0", "p1", "p2"}
    assert media[0]["caption"] == "Pronto" and "caption" not in media[1] and media[2]["media"] == "attach://p2"
