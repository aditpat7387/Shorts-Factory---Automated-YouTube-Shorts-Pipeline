"""Offline end-to-end test: synthetic 'stream', no network, no API keys.  python tests/test_e2e.py"""
import subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import config
from pipeline.db import DB
from pipeline.runner import advance
from pipeline.detect import find_highlights
from pipeline.util import probe
import pipeline.util   # applies FFMPEG_DIR to PATH
from pipeline.upload import next_slot, IST

quiet = lambda *_: None
tmp = Path(tempfile.mkdtemp())
src = tmp / "stream.mp4"
# 90s synthetic stream: quiet hum, with a loud burst at ~58-64s
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=90",
    "-f", "lavfi", "-i", "sine=frequency=300:duration=90",
    "-f", "lavfi", "-i", "sine=frequency=900:duration=6,adelay=58000|58000,apad=whole_dur=90",
    "-filter_complex", "[1:a]volume=0.05[q];[q][2:a]amix=inputs=2:duration=longest,volume=2[a]",
    "-map", "0:v", "-map", "[a]", "-t", "90", "-c:v", "libx264", "-c:a", "aac", str(src)],
    check=True, capture_output=True)

hl = find_highlights(src, n=1, window=30)
print("highlight:", hl)
peak = hl[0][0] + 30 * 0.7
assert 50 <= peak <= 70, hl

cfg = config.load(str(ROOT / "config.yaml"), workspace=str(tmp / "ws"))
cfg["llm"]["provider"] = "template"       # the test must stay offline and free
cfg["voice"]["engine"] = "test_tone"
cfg["transcribe"]["engine"] = "none"
cfg["approvals"] = {"clip": True, "final": True}
cfg["upload"]["enabled"] = False
db = DB(cfg["_ws"] / "jobs.db")
jid = db.add(source=str(src), start=hl[0][0], end=hl[0][1], streamer="TestStreamer",
             game="TestGame", source_credit="https://example.com/TestStreamer")

assert advance(db, cfg, jid, log=quiet)["status"] == "AWAITING_CLIP_APPROVAL"
db.update(jid, status="CLIP_APPROVED")
assert advance(db, cfg, jid, log=quiet)["status"] == "AWAITING_FINAL_APPROVAL"
job = db.get(jid)
v = [s for s in probe(job["video_path"])["streams"] if s["codec_type"] == "video"][0]
assert (v["width"], v["height"]) == (1080, 1920), v
assert "Original stream:" in job["description"]
db.update(jid, status="FINAL_APPROVED")
assert advance(db, cfg, jid, log=quiet)["status"] == "FINAL_APPROVED"
assert next_slot(cfg).astimezone(IST).hour in (19, 21)
print("render:", job["video_path"])
print("ALL PASSED")
