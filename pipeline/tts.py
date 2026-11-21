import asyncio, os
from .util import run

def synthesize(text, out_base, cfg):
    v = cfg["voice"]
    eng = v["engine"]
    if eng == "edge-tts":
        import edge_tts
        out = f"{out_base}.mp3"
        asyncio.run(edge_tts.Communicate(text, v["voice"]).save(out))
        return out
    if eng == "elevenlabs":
        import requests
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{v['voice']}",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
            json={"text": text, "model_id": "eleven_turbo_v2_5"}, timeout=120)
        r.raise_for_status()
        out = f"{out_base}.mp3"
        open(out, "wb").write(r.content)
        return out
    # test_tone: placeholder audio sized by word count, for offline pipeline testing
    out = f"{out_base}.wav"
    secs = max(3.0, len(text.split()) / 2.6)
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={secs}",
         "-af", "volume=0.15", out])
    return out
