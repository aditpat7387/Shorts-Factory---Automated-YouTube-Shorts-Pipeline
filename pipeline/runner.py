import json
from . import ingest as ing, script as scr, tts, edit

STOP = {"AWAITING_CLIP_APPROVAL", "AWAITING_FINAL_APPROVAL", "REJECTED", "FAILED", "UPLOADED"}

def _credit(job, desc):
    extra = f"\n\nOriginal stream: {job['source_credit']}" if job.get("source_credit") else ""
    return (desc + extra).strip()

def _prev_parts(db, job):
    if not job.get("series_id"):
        return []
    out = []
    for j in db.list():
        if j["series_id"] == job["series_id"] and (j["part_no"] or 0) < (job["part_no"] or 0) and j["script"]:
            out.append({"part_no": j["part_no"], "narration": json.loads(j["script"])["narration"]})
    return sorted(out, key=lambda x: x["part_no"])

_DANGLING = {"for", "a", "an", "the", "of", "to", "and", "in", "on", "at", "with", "as", "by", "from", "but", "or"}

def _shorten(t, limit):
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(" ,:;-")
    words = cut.split()
    while words and words[-1].lower() in _DANGLING:
        words.pop()
    return " ".join(words) or t[:limit]

def _decorate(job, title, desc):
    p, n = job.get("part_no"), job.get("total_parts")
    if not (p and n and n > 1):
        return title, desc
    suffix = f" (Part {p}/{n})"
    return _shorten(title, max(10, 70 - len(suffix))) + suffix, f"Part {p} of {n}. {desc}"

def _siblings(db, job):
    if not job.get("series_id"):
        return []
    return [j for j in db.list() if j["series_id"] == job["series_id"] and j["id"] != job["id"]]

def _blocking_part(db, job):
    """Part N must not upload before the earlier parts (rejected parts don't block)."""
    for j in sorted(_siblings(db, job), key=lambda x: x["part_no"] or 0):
        if (j["part_no"] or 0) < (job["part_no"] or 0) and j["status"] not in ("UPLOADED", "REJECTED"):
            return j["part_no"]
    return None

def advance_pending(db, cfg, log=print):
    """Advance every job that is not waiting on a human, failed, or finished."""
    for j in db.list():
        if j["status"] in STOP:
            continue
        if j["status"] == "FINAL_APPROVED" and not cfg["upload"]["enabled"]:
            continue
        advance(db, cfg, j["id"], log)

def advance(db, cfg, jid, log=print):
    """Run a job forward until it hits a human gate, an error, or the end."""
    while True:
        job = db.get(jid)
        st = job["status"]
        try:
            if st == "NEW":
                log(f"[{jid}] ingest")
                db.update(jid, clip_path=ing.ingest(job, cfg), status="CLIP_READY")
            elif st == "CLIP_READY":
                nxt = "AWAITING_CLIP_APPROVAL" if cfg["approvals"]["clip"] else "CLIP_APPROVED"
                db.update(jid, status=nxt)
                if nxt in STOP:
                    log(f"[{jid}] waiting for clip approval -> {job['clip_path']}")
            elif st == "CLIP_APPROVED":
                log(f"[{jid}] script")
                transcript = scr.transcribe(job["clip_path"], cfg)
                job["transcript"] = transcript
                job["prev_parts"] = _prev_parts(db, job)
                s = scr.write_script(job, cfg)
                title, desc = _decorate(job, s["title"], s["description"])
                db.update(jid, transcript=transcript, script=json.dumps(s),
                          title=title, description=_credit(job, desc),
                          status="SCRIPTED")
            elif st == "SCRIPTED":
                log(f"[{jid}] voice")
                s = json.loads(job["script"])
                base = str(cfg["_ws"] / "audio" / f"job{jid}_voice")
                db.update(jid, audio_path=tts.synthesize(s["narration"], base, cfg), status="VOICED")
            elif st == "VOICED":
                log(f"[{jid}] render")
                s = json.loads(job["script"])
                db.update(jid, video_path=edit.render(job, cfg, s), status="RENDERED")
            elif st == "RENDERED":
                nxt = "AWAITING_FINAL_APPROVAL" if cfg["approvals"]["final"] else "FINAL_APPROVED"
                db.update(jid, status=nxt)
                if nxt in STOP:
                    log(f"[{jid}] waiting for final approval -> {job['video_path']}")
            elif st == "FINAL_APPROVED":
                if not cfg["upload"]["enabled"]:
                    log(f"[{jid}] approved; upload disabled in config (ready: {job['video_path']})")
                    return db.get(jid)
                blocker = _blocking_part(db, job)
                if blocker:
                    log(f"[{jid}] waiting: part {blocker} of this series must upload first")
                    return db.get(jid)
                from . import upload as up
                log(f"[{jid}] upload")
                taken = [up.parse_iso(j["publish_at"]) for j in db.list()
                         if j["publish_at"] and j["id"] != jid]
                sibs = _siblings(db, job)
                prev = [up.parse_iso(j["publish_at"]) for j in sibs
                        if (j["part_no"] or 0) < (job["part_no"] or 0) and j["publish_at"]]
                pid = next((j["playlist_id"] for j in sibs if j["playlist_id"]), None)

                def saved(vid, when):
                    db.update(jid, youtube_id=vid, publish_at=when, status="UPLOADED")

                res = up.upload(job, cfg, json.loads(job["script"]), taken=taken,
                                after=max(prev) if prev else None, playlist_id=pid,
                                on_uploaded=saved, log=log)
                if res.get("playlist_id"):
                    for j in sibs + [job]:
                        db.update(j["id"], playlist_id=res["playlist_id"])
                log(f"[{jid}] uploaded https://youtu.be/{res['id']} (publishes {res['publish_at']} UTC)")
            else:
                return job
        except Exception as ex:
            db.update(jid, status="FAILED", error=str(ex)[:2000])
            log(f"[{jid}] FAILED: {ex}")
            return db.get(jid)
        if db.get(jid)["status"] in STOP:
            return db.get(jid)
