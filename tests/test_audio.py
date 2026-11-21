"""Audio must cover the whole video; end card adds the CTA; CTA can be disabled."""
import subprocess, sys, tempfile, yaml, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import pipeline.util

def streams(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(p)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(r.stdout)["streams"]}

def make(tmp, secs, cta):
    src = tmp / f"v{secs}.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=30:duration={secs}",
        "-f", "lavfi", "-i", f"sine=frequency=500:duration={secs}", "-t", str(secs), "-c:v", "libx264",
        "-c:a", "aac", str(src)], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text()); cfg["workspace"] = str(tmp / f"ws{secs}")
    cfg["llm"]["provider"] = "template"; cfg["voice"]["engine"] = "test_tone"
    cfg["approvals"] = {"clip": False, "final": False}; cfg["upload"]["enabled"] = False
    cfg["cta"]["enabled"] = cta
    (tmp / f"c{secs}.yaml").write_text(yaml.safe_dump(cfg))
    def cli(*a):
        r = subprocess.run([sys.executable, str(ROOT / "cli.py"), "--config", str(tmp / f"c{secs}.yaml"), *a],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr + r.stdout
    cli("add", str(src), "--start", "0", "--end", str(secs), "--streamer", "T", "--game", "G"); cli("run")
    return tmp / f"ws{secs}" / "renders" / "job1_short.mp4"

tmp = Path(tempfile.mkdtemp())
out = make(tmp, 30, True)                     # narration (~19s) is much shorter than the clip
s = streams(out); print("with CTA:", s)
assert s["video"] > 30 + 2.4, s               # end card was appended
assert abs(s["audio"] - s["video"]) < 0.4, s  # audio covers the full video (was 16s of 30s)
r = subprocess.run(["ffmpeg", "-nostdin", "-ss", "24", "-t", "5", "-i", str(out), "-vn", "-af", "volumedetect",
                    "-f", "null", "-"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
mean = [l for l in r.stderr.splitlines() if "mean_volume" in l]; print("game audio 24-29s:", mean)
assert mean and float(mean[0].split("mean_volume:")[1].split()[0]) > -60       # not silent
ass = (tmp / "ws30" / "work" / "job1" / "captions.ass").read_text()
assert "LIKE\\NSHARE\\NSUBSCRIBE" in ass

out = make(tmp, 10, False)                    # narration (~19s) LONGER than the 10s clip, CTA off
s = streams(out); print("no CTA, long narration:", s)
assert abs(s["video"] - 10) < 0.2 and abs(s["audio"] - s["video"]) < 0.4, s
print("AUDIO TESTS PASSED")
