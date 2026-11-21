"""Facecam panel: the face region of the stream is enlarged into its own panel below the gameplay."""
import subprocess, sys, tempfile, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import pipeline.util
from pipeline.db import DB
DN = subprocess.DEVNULL
tmp = Path(tempfile.mkdtemp()); src = tmp / "s.mp4"
# 1280x720 grey 'stream' with a red 'face' box top-right (x=1000,y=40,w=256,h=216)
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=30:d=8,drawbox=x=1000:y=40:w=256:h=216:color=red:t=fill",
    "-f", "lavfi", "-i", "sine=frequency=300:duration=8", "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    "-c:a", "aac", str(src)], check=True, capture_output=True, stdin=DN)
cfg = yaml.safe_load((ROOT / "config.yaml").read_text()); cfg["workspace"] = str(tmp / "ws")
cfg["llm"]["provider"] = "template"; cfg["voice"]["engine"] = "test_tone"; cfg["transcribe"]["engine"] = "none"
cfg["approvals"] = {"clip": False, "final": False}; cfg["upload"]["enabled"] = False
(tmp / "c.yaml").write_text(yaml.safe_dump(cfg))
def cli(*a, ok=True):
    r = subprocess.run([sys.executable, str(ROOT / "cli.py"), "--config", str(tmp / "c.yaml"), *a],
                       capture_output=True, text=True, stdin=DN)
    if ok: assert r.returncode == 0, r.stderr + r.stdout
    return r
def pixel(video, t, x, y):
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", str(t), "-i", str(video), "-vf", f"crop=2:2:{x}:{y},format=rgb24",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, stdin=DN)
    return tuple(r.stdout[:3])

assert cli("add", str(src), "--start", "0", "--end", "8", "--facecam", "2,2,2,2", ok=False).returncode != 0   # bad rectangle rejected
cli("add", str(src), "--start", "0", "--end", "8", "--facecam", "0.78125,0.0556,0.2,0.3")   # job 1: with facecam
cli("add", str(src), "--start", "0", "--end", "8")                                          # job 2: without
cli("run")
db = DB(tmp / "ws" / "jobs.db"); out1, out2 = db.get(1)["video_path"], db.get(2)["video_path"]
r, g, b = pixel(out1, 2, 540, 1150); print("face panel pixel:", (r, g, b))
assert r > 180 and g < 80 and b < 80, (r, g, b)                    # red face is enlarged in the panel
r, g, b = pixel(out1, 2, 540, 700);  assert abs(r - g) < 30 and abs(g - b) < 30   # gameplay (grey) above it
r, g, b = pixel(out2, 2, 540, 1150); assert not (r > 180 and g < 80), (r, g, b)   # no panel without a facecam
cli("facecam-preview", str(src), "--rect", "0.78125,0.0556,0.2,0.3", "--at", "1")
assert (tmp / "ws" / "facecam_preview.png").exists()
subprocess.run(["ffmpeg", "-y", "-nostdin", "-ss", "2", "-i", out1, "-frames:v", "1", "-vf", "scale=360:-1",
                str(tmp / "facecam_frame.png")], check=True, capture_output=True, stdin=DN)
print("FACECAM TESTS PASSED  (sample frame:", tmp / "facecam_frame.png", ")")
