import glob, shutil
from pathlib import Path
from .util import run

def _normalize(src, out, start, dur):
    cmd = ["ffmpeg", "-y"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src), "-t", str(dur), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)]
    run(cmd)

def ingest(job, cfg):
    """Produce a normalised raw clip at workspace/clips/job<ID>_clip.mp4"""
    ws, jid = cfg["_ws"], job["id"]
    out = ws / "clips" / f"job{jid}_clip.mp4"
    maxs = cfg["clip"]["max_seconds"]
    s, e = job["start"], job["end"]
    dur = maxs if (s is None or e is None) else min(e - s, maxs)
    src = job["source"]

    if src.startswith(("http://", "https://")):
        if not shutil.which("yt-dlp"):
            raise RuntimeError("yt-dlp not installed (pip install yt-dlp)")
        tmpl = str(ws / "raw" / f"job{jid}_raw.%(ext)s")
        cmd = ["yt-dlp", "-f", "bv*[height<=1080]+ba/b", "--merge-output-format", "mp4",
               "-o", tmpl]
        if s is not None and e is not None:
            cmd += ["--download-sections", f"*{s}-{e}", "--force-keyframes-at-cuts"]
        run(cmd + [src])
        raw = glob.glob(str(ws / "raw" / f"job{jid}_raw.*"))
        if not raw:
            raise RuntimeError("download produced no file")
        _normalize(raw[0], out, None, dur)
    else:
        p = Path(src)
        if not p.exists():
            raise FileNotFoundError(src)
        _normalize(p, out, s, dur)
    return str(out)
