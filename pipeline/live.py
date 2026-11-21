"""Live capture: record streams into rolling .ts segments, find highlights in each finished
segment, queue them as jobs, and delete old footage."""
import json, os, re, shutil, subprocess, time
import datetime as dt
from pathlib import Path
from .detect import find_highlights

DEFAULT_LIVE = {
    "poll_seconds": 30,          # main loop interval
    "offline_poll_seconds": 60,  # how often to ask "is this channel live?"
    "segment_seconds": 300,      # recording chunk length
    "quality": "720p60,720p,best",
    "keep_hours": 6,             # delete processed segments after this long
    "clips_per_segment": 1,
    "clip_seconds": 30,
    "min_ratio": 1.6,            # loudness spike vs the local baseline needed to count as a highlight
    "max_clips_per_day": 8,
    "realtime": True,            # only used for file-simulated streams
    "channels": [],
}
AUTO_NOTE = ("Auto-captured from a live stream. Describe only what the frames and transcript show; "
             "do not guess details you cannot see.")


def live_cfg(cfg):
    return {**DEFAULT_LIVE, **(cfg.get("live") or {})}


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


class ChannelWatch:
    def __init__(self, ch, cfg, log=print):
        self.ch, self.cfg, self.L, self.log = ch, cfg, live_cfg(cfg), log
        self.dir = Path(cfg["_ws"]) / "live" / _safe(ch["name"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.procs, self.last_check, self.played = [], 0.0, False

    @property
    def recording(self):
        return bool(self.procs) and self.procs[-1].poll() is None

    def is_live(self):
        if self.ch.get("file"):                      # simulated stream: plays once
            return Path(self.ch["file"]).exists() and not self.played
        try:
            r = subprocess.run(["streamlink", "--stream-url", self.ch["url"], self.L["quality"]],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            return r.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def start(self):
        session = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        seg = ["-f", "segment", "-segment_time", str(self.L["segment_seconds"]), "-reset_timestamps", "1",
               "-segment_format", "mpegts", str(self.dir / f"seg_{session}_%04d.ts")]
        base = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        if self.ch.get("file"):
            cmd = base + (["-re"] if self.L["realtime"] else []) + ["-i", str(self.ch["file"]), "-c", "copy"] + seg
            self.procs = [subprocess.Popen(cmd, stdin=subprocess.DEVNULL)]
            self.played = True
        else:
            p1 = subprocess.Popen(["streamlink", "--stdout", "--retry-open", "3", self.ch["url"], self.L["quality"]],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            p2 = subprocess.Popen(base + ["-i", "pipe:0", "-c", "copy"] + seg, stdin=p1.stdout)
            p1.stdout.close()
            self.procs = [p1, p2]
        self.log(f"[live] {self.ch['name']}: recording started")

    def tick(self, now=None):
        now = now or time.time()
        if self.recording:
            return
        if self.procs:                               # it just ended
            self.procs = []
            self.log(f"[live] {self.ch['name']}: recording ended")
        if now - self.last_check < self.L["offline_poll_seconds"]:
            return
        self.last_check = now
        if self.is_live():
            self.start()

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []


def sweep_segments(db, cfg, watches, log=print):
    """Queue highlights from finished segments; delete old, fully-used ones. Returns jobs created."""
    L = live_cfg(cfg)
    created = 0
    for w in watches:
        files = sorted(w.dir.glob("seg_*.ts"))
        for i, f in enumerate(files):
            path = str(f)
            if db.seg_get(path):
                continue
            if not (i + 1 < len(files) or not w.recording):
                continue                              # still being written
            try:
                hl = find_highlights(path, n=L["clips_per_segment"], window=L["clip_seconds"],
                                     peak_pos=cfg.get("detect", {}).get("peak_position", 0.7),
                                     min_ratio=L["min_ratio"])
            except Exception as e:
                log(f"[live] could not analyse {f.name}: {e}")
                db.seg_add(path, w.ch["name"], "ERROR")
                continue
            used = db.count_jobs_since(f"%{os.sep}live{os.sep}%", time.time() - 86400)
            room = max(0, L["max_clips_per_day"] - used)
            n = 0
            for s, e, score in hl[:room]:
                db.add(source=path, start=float(s), end=float(e), streamer=w.ch.get("streamer"),
                       game=w.ch.get("game"), source_credit=w.ch.get("credit") or w.ch.get("url"),
                       notes=AUTO_NOTE,
                       facecam=json.dumps(list(w.ch["facecam"])) if w.ch.get("facecam") else None)
                n += 1
            created += n
            db.seg_add(path, w.ch["name"], "DONE", n)
            log(f"[live] {w.ch['name']}/{f.name}: {n} clip(s) queued" + ("" if n else " (nothing stood out)"))

    cutoff = time.time() - L["keep_hours"] * 3600
    for sg in db.segs("DONE") + db.segs("ERROR"):
        p = Path(sg["path"])
        if p.exists() and p.stat().st_mtime <= cutoff and not db.pending_ingest(sg["path"]):
            p.unlink()
            db.seg_set(sg["path"], "DELETED")
    return created


def run_live(cfg, db, advance_fn, log=print, record=True, max_loops=None):
    L = live_cfg(cfg)
    if not L["channels"]:
        raise RuntimeError("No channels configured. Add some under `live: channels:` in config.yaml")
    if record and any(not c.get("file") for c in L["channels"]) and not shutil.which("streamlink"):
        raise RuntimeError("streamlink not found. Install it with: python -m pip install streamlink")
    watches = [ChannelWatch(c, cfg, log) for c in L["channels"]]
    loops = 0
    try:
        while True:
            if record:
                for w in watches:
                    w.tick()
            sweep_segments(db, cfg, watches, log)
            advance_fn()
            loops += 1
            if max_loops and loops >= max_loops:
                break
            time.sleep(L["poll_seconds"])
    except KeyboardInterrupt:
        log("[live] stopping...")
    finally:
        for w in watches:
            w.stop()
        sweep_segments(db, cfg, watches, log)
