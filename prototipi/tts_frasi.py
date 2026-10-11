"""Sintetizza frase per frase, con pause, e salva i tempi di ogni frase (servono per stacchi e sottotitoli)."""
import json, sys, numpy as np, soundfile as sf
from kokoro import KPipeline
SR = 24000
pipe = KPipeline(lang_code="i", repo_id="hexgrad/Kokoro-82M")
# Pronuncia all'inglese di BetForge: si corregge direttamente la stringa di fonemi
FIX = {"bˈɛt fˈɔrʤe": "bˈɛt fˈɔːrʤ", "bɛt fˈɔrʤe": "bɛt fˈɔːrʤ"}
script = json.load(open(sys.argv[1], encoding="utf-8"))  # [{"testo":..., "pausa_dopo":s}]
PITCH = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0   # <1 = piu' grave (applicato dopo con asetrate)
G_DURA = len(sys.argv) > 5 and sys.argv[5] == "dura"
if G_DURA:
    FIX = {k: v.replace("ʤ", "ɡ") for k, v in FIX.items()}
out, timeline, t = [], [], 0.0
for item in script:
    ps, _ = pipe.g2p(item["testo"])
    for a, b in FIX.items():
        ps = ps.replace(a, b)
    audio = np.concatenate([r.audio.numpy() for r in pipe.generate_from_tokens(
        ps, voice=item.get("voce", "im_nicola"), speed=item.get("velocita", 0.92) / PITCH)])
    # taglia i silenzi ai bordi
    idx = np.where(np.abs(audio) > 0.01)[0]
    audio = audio[max(idx[0] - 600, 0): idx[-1] + 1200]
    pause = item.get("pausa_dopo", 0.35) * PITCH  # la pausa si allunga con asetrate: la compenso
    timeline.append({"testo": item["testo"], "inizio": round(t / PITCH, 3), "fine": round((t + len(audio) / SR) / PITCH, 3)})
    out += [audio, np.zeros(int(pause * SR), np.float32)]
    t += len(audio) / SR + pause
sf.write(sys.argv[2], np.concatenate(out), SR)
json.dump(timeline, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"ok {t / PITCH:.1f}s, {len(timeline)} frasi")
