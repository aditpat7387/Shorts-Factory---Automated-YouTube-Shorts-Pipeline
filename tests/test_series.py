"""Series test: cuts snap to pauses, parts rendered with badges, titles numbered."""
import subprocess, sys, tempfile, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.series import plan_parts
from pipeline.db import DB
import pipeline.util

tmp = Path(tempfile.mkdtemp())
src = tmp / "long.mp4"
# 120s: constant tone with short silences at 38-40s and 79-81s (natural pauses)
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=120",
    "-f", "lavfi", "-i", "sine=frequency=300:duration=120",
    "-af", "volume=0:enable='between(t,38,40)+between(t,79,81)'",
    "-map", "0:v", "-map", "1:a", "-t", "120", "-c:v", "libx264", "-c:a", "aac", str(src)],
    check=True, capture_output=True, stdin=subprocess.DEVNULL)

plan = plan_parts(src, 0, 120, parts=3)
print("plan:", plan)
assert len(plan) == 3 and plan[0][0] == 0 and plan[-1][1] == 120
assert 38 <= plan[0][1] <= 40.1, plan          # snapped into first pause
assert 79 <= plan[1][1] <= 81.1, plan          # snapped into second pause
assert all(b - a <= 45 for a, b in plan)
try:
    plan_parts(src, 0, 120, parts=2); raise SystemExit("should have rejected 60s parts")
except ValueError as e:
    print("rejects too-long parts:", e)

cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
cfg.update(workspace=str(tmp / "ws"))
cfg["llm"]["provider"] = "template"; cfg["voice"]["engine"] = "test_tone"
cfg["transcribe"]["engine"] = "none"; cfg["approvals"] = {"clip": False, "final": False}
cfg["upload"]["enabled"] = False
(tmp / "cfg.yaml").write_text(yaml.safe_dump(cfg))

def cli(*a):
    r = subprocess.run([sys.executable, str(ROOT / "cli.py"), "--config", str(tmp / "cfg.yaml"), *a],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr + r.stdout
    return r.stdout

print(cli("series", str(src), "--start", "0", "--end", "120", "--parts", "3",
          "--streamer", "Tester", "--game", "TestGame", "--notes", "plane heist",
          "--part-note", "steals the plane", "--part-note", "chase", "--part-note", "missile and eject"))
cli("run")
db = DB(tmp / "ws" / "jobs.db")
jobs = db.list()
assert [j["status"] for j in jobs] == ["FINAL_APPROVED"] * 3, [j["status"] for j in jobs]
assert [j["title"][-10:] for j in jobs] == ["(Part 1/3)", "(Part 2/3)", "(Part 3/3)"], [j["title"] for j in jobs]
assert all(j["series_id"] == jobs[0]["id"] for j in jobs)
ass1 = (tmp / "ws" / "work" / "job1" / "captions.ass").read_text()
ass3 = (tmp / "ws" / "work" / "job3" / "captions.ass").read_text()
assert "PART 1/3" in ass1 and "FOLLOW FOR PART 2" in ass1
assert "PART 3/3" in ass3 and "FOLLOW FOR" not in ass3
assert jobs[1]["description"].startswith("Part 2 of 3.")
print(cli("list"))
print("SERIES TEST PASSED")
