import os
_d = os.environ.get("FFMPEG_DIR")
if _d:
    os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
    
import subprocess, json

def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(map(str, cmd))}\n{r.stderr[-1500:]}")
    return r

def probe(path):
    r = run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)])
    return json.loads(r.stdout)

def duration(path):
    return float(probe(path)["format"]["duration"])

def has_audio(path):
    return any(s["codec_type"] == "audio" for s in probe(path)["streams"])
