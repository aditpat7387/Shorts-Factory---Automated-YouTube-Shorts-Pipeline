import base64, tempfile
from pathlib import Path
from .util import run, duration


def extract_frames(clip, n=6, width=640):
    """n evenly spaced JPEG frames from the clip, as base64 strings (for the script writer)."""
    d = duration(clip)
    out = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(n):
            f = Path(td) / f"f{i}.jpg"
            run(["ffmpeg", "-y", "-ss", f"{d * (i + 0.5) / n:.2f}", "-i", str(clip), "-frames:v", "1",
                 "-vf", f"scale={width}:-2", "-q:v", "4", str(f)])
            if f.exists():
                out.append(base64.standard_b64encode(f.read_bytes()).decode())
    return out
