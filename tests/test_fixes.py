import subprocess, sys, tempfile, types, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import pipeline.util
from pipeline.runner import _shorten, _decorate
from pipeline import script as S
from pipeline.db import DB

# 1. title trimming no longer leaves dangling words
t = "GTA VI: Cops Crash a Plane Heist, Streamer Makes a Run for the Border Fast"
print(_decorate({"part_no": 1, "total_parts": 4}, t, "d")[0])
assert not _decorate({"part_no": 1, "total_parts": 4}, t, "d")[0].split(" (Part")[0].endswith(("for", "the"))
assert _shorten("Short title", 59) == "Short title"

# 2. old-format notes are split so overview is labelled as spoilers
old = "Streamer steals plane, missile hits, ejects\nThis part: slips away in his car"
n = S._notes_for_prompt({"notes": old})
assert "THIS PART ONLY: slips away in his car" in n and "later events are spoilers" in n

# 3. the prompt that reaches the model carries the rules + labelled notes
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
S.write_script({"streamer": "A", "game": "G", "part_no": 2, "total_parts": 4, "notes": old,
                "prev_parts": [{"part_no": 1, "narration": "Part one text"}]},
               {"llm": {"provider": "anthropic", "model": "m"}})
ctx = seen["messages"][0]["content"]
for must in ("PART 2 of 4", "ANOTHER part", "THIS PART ONLY: slips away", "Part 1 narration: Part one text",
             "recap of at most 8 words"):
    assert must in ctx, must
assert "finale" not in ctx
print("prompt ok")

# 4. CLI: series -> run -> rescript with corrected part notes
tmp = Path(tempfile.mkdtemp()); src = tmp / "v.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=10",
    "-f", "lavfi", "-i", "sine=frequency=300:duration=10", "-t", "10", "-c:v", "libx264", "-c:a", "aac",
    str(src)], check=True, capture_output=True, stdin=subprocess.DEVNULL)
cfg = yaml.safe_load((ROOT / "config.yaml").read_text()); cfg["workspace"] = str(tmp / "ws")
cfg["llm"]["provider"] = "template"; cfg["voice"]["engine"] = "test_tone"
cfg["approvals"] = {"clip": False, "final": False}; cfg["upload"]["enabled"] = False
(tmp / "c.yaml").write_text(yaml.safe_dump(cfg))
def cli(*a):
    r = subprocess.run([sys.executable, str(ROOT / "cli.py"), "--config", str(tmp / "c.yaml"), *a],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr + r.stdout
    return r.stdout
cli("series", str(src), "--start", "0", "--end", "10", "--parts", "2", "--notes", "whole story with spoiler",
    "--part-note", "first half", "--part-note", "second half"); cli("run")
db = DB(tmp / "ws" / "jobs.db")
assert "STORY OVERVIEW" in db.get(1)["notes"] and "THIS PART ONLY: first half" in db.get(1)["notes"]
cli("rescript", "1", "2", "--part-note", "FIXED one", "--part-note", "FIXED two")
j1, j2 = db.get(1), db.get(2)
assert "THIS PART ONLY: FIXED one" in j1["notes"] and "whole story with spoiler" in j1["notes"], j1["notes"]
assert "THIS PART ONLY: FIXED two" in j2["notes"]
assert j1["status"] == j2["status"] == "FINAL_APPROVED" and j1["title"].endswith("(Part 1/2)")
print("FIXES PASSED")
