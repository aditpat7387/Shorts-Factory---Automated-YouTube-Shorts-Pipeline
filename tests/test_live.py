"""Live capture using a simulated stream (a file 'broadcast' split into segments)."""
import sys, subprocess, tempfile, time, types
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import pipeline.util
from pipeline import config, script as S
from pipeline.db import DB
from pipeline.live import ChannelWatch, sweep_segments
from pipeline.runner import advance
from pipeline.util import duration
quiet = lambda *_: None
DN = subprocess.DEVNULL
tmp = Path(tempfile.mkdtemp())

# 48 s "stream": quiet hum + loud bursts at 20-23 s and 40-43 s; keyframe every second
src = tmp / "stream.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=48",
    "-f", "lavfi", "-i", "sine=frequency=300:duration=48",
    "-f", "lavfi", "-i", "sine=frequency=900:duration=3,adelay=20000|20000,apad=whole_dur=48",
    "-f", "lavfi", "-i", "sine=frequency=900:duration=3,adelay=40000|40000,apad=whole_dur=48",
    "-filter_complex", "[1:a]volume=0.05[q];[q][2:a][3:a]amix=inputs=3:duration=longest,volume=3[a]",
    "-map", "0:v", "-map", "[a]", "-t", "48", "-c:v", "libx264", "-g", "30", "-keyint_min", "30",
    "-c:a", "aac", str(src)], check=True, capture_output=True, stdin=DN)

cfg = config.load(str(ROOT / "config.yaml"), workspace=str(tmp / "ws"))
cfg["llm"]["provider"] = "template"; cfg["voice"]["engine"] = "test_tone"; cfg["transcribe"]["engine"] = "none"
cfg["live"] = {"channels": [{"name": "fake chan", "file": str(src), "streamer": "S", "game": "G",
                             "credit": "https://example.com/s"}],
               "segment_seconds": 12, "realtime": False, "clip_seconds": 6, "min_ratio": 1.5,
               "clips_per_segment": 1, "max_clips_per_day": 8, "keep_hours": 6}
db = DB(cfg["_ws"] / "jobs.db")
w = ChannelWatch(cfg["live"]["channels"][0], cfg, log=quiet)

assert w.is_live(); w.tick(time.time())
for _ in range(200):
    if not w.recording: break
    time.sleep(0.5)
assert not w.recording and not w.is_live()                 # simulated stream plays once
segs = sorted(w.dir.glob("seg_*.ts")); print("segments:", [(p.name[-11:], round(duration(p), 1)) for p in segs])
assert len(segs) == 4 and all(abs(duration(p) - 12) < 1.5 for p in segs)

n = sweep_segments(db, cfg, [w], log=print)
jobs = db.list(); print("jobs:", [(j["source"][-11:], j["start"], j["end"]) for j in jobs])
assert n == len(jobs) == 2, jobs                           # only the two segments with a burst
assert {Path(j["source"]).name for j in jobs} == {segs[1].name, segs[3].name}
for j in jobs:
    burst = (8, 11) if j["source"].endswith(segs[1].name) else (4, 7)     # burst position inside its segment
    assert j["start"] < burst[1] and j["end"] > burst[0], j
    assert j["streamer"] == "S" and j["source_credit"] == "https://example.com/s" and "Auto-captured" in j["notes"]
assert sweep_segments(db, cfg, [w], log=quiet) == 0 and len(db.list()) == 2         # no double-processing

# the clip is cut straight out of the .ts segment by the normal pipeline
assert advance(db, cfg, jobs[0]["id"], log=quiet)["status"] == "AWAITING_CLIP_APPROVAL"
clip = db.get(jobs[0]["id"])["clip_path"]; assert abs(duration(clip) - 6) < 0.6, duration(clip)

# cleanup: used/empty segments are deleted, the one still waiting to be cut is kept
cfg["live"]["keep_hours"] = 0
sweep_segments(db, cfg, [w], log=quiet)
left = sorted(p.name for p in w.dir.glob("seg_*.ts")); print("left:", left)
assert left == [Path(jobs[1]["source"]).name], left

# daily cap
cfg["live"]["max_clips_per_day"] = 2
assert db.count_jobs_since("%live%", time.time() - 86400) == 2

# ---- vision: frames are sent to the model ahead of the text
seen = {}
class B:
    def __init__(s, t): s.text, s.type = t, "text"
class M:
    stop_reason = "end_turn"
    def __init__(s): s.content = [B('{"hook":"h","narration":"n","title":"t","description":"d","tags":[]}')]
class C:
    def __init__(s): s.messages = s
    def create(s, **k): seen.update(k); return M()
sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=C)
S.write_script({"streamer": "S", "game": "G", "clip_path": clip, "notes": "x"},
               {"llm": {"provider": "anthropic", "model": "m", "frames": 4}})
content = seen["messages"][0]["content"]
imgs = [b for b in content if b["type"] == "image"]
assert len(imgs) == 4 and content[-1]["type"] == "text" and "frames from the clip" in content[-1]["text"]
assert all(len(b["source"]["data"]) > 500 and b["source"]["media_type"] == "image/jpeg" for b in imgs)
seen.clear()
S.write_script({"streamer": "S", "game": "G", "clip_path": clip},
               {"llm": {"provider": "anthropic", "model": "m", "vision": False}})
assert isinstance(seen["messages"][0]["content"], str)                              # vision can be switched off
print("LIVE TESTS PASSED")
