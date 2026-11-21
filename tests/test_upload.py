"""Upload logic against a fake YouTube service (no network, no credentials)."""
import sys, json, types, tempfile, datetime as dt
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import pipeline.util
from pipeline import upload as up, runner
from pipeline.db import DB
IST, UTC = up.IST, up.UTC
quiet = lambda *_: None
def ist(y, m, d, h, mi): return dt.datetime(y, m, d, h, mi, tzinfo=IST).astimezone(UTC)

cfg = {"upload": {"slots_ist": ["19:45", "21:15"], "min_lead_minutes": 30, "category_id": "20", "enabled": True},
       "approvals": {}}
# ---- slot scheduling
NOW = ist(2026, 11, 20, 15, 30)
s1 = up.next_slot(cfg, now=NOW);                       assert s1 == ist(2026, 11, 20, 19, 45), s1
s2 = up.next_slot(cfg, taken=[s1], now=NOW);           assert s2 == ist(2026, 11, 20, 21, 15)
s3 = up.next_slot(cfg, taken=[s1, s2], now=NOW);       assert s3 == ist(2026, 11, 21, 19, 45)
assert up.next_slot(cfg, now=ist(2026, 11, 20, 19, 30)) == ist(2026, 11, 20, 21, 15)   # 30-min lead rule
assert up.next_slot(cfg, after=s1, now=NOW) == s2                                       # series ordering
print("slots ok")

# ---- fake YouTube service
class Req:
    def __init__(s, resp, fail=0): s.resp, s.fail = resp, fail
    def next_chunk(s):
        if s.fail:
            s.fail -= 1; e = Exception("boom"); e.resp = types.SimpleNamespace(status=503); raise e
        return None, s.resp
    def execute(s): return s.resp
class Coll:
    def __init__(s, svc, name): s.svc, s.name = svc, name
    def insert(s, **kw):
        s.svc.calls.append((s.name, kw))
        if s.name == "videos": return Req({"id": "VIDX"}, fail=s.svc.fail)
        if s.name == "playlists":
            if s.svc.playlist_error: raise RuntimeError("playlist denied")
            return Req({"id": "PL1"})
        return Req({"id": "ITEM"})
class Svc:
    def __init__(s, fail=0, playlist_error=False): s.calls, s.fail, s.playlist_error = [], fail, playlist_error
    def videos(s): return Coll(s, "videos")
    def playlists(s): return Coll(s, "playlists")
    def playlistItems(s): return Coll(s, "playlistItems")
up._media = lambda p: "MEDIA"; up.time.sleep = lambda x: None

job = {"id": 1, "title": "A" * 120, "description": "desc", "video_path": "v.mp4"}
c2 = {"upload": {**cfg["upload"], "description_footer": "FOOTER", "synthetic_media": True}}
svc = Svc(fail=2); seen = []
res = up.upload(job, c2, {"tags": ["gta", "", "  "]}, service=svc, now=NOW,
                on_uploaded=lambda v, w: seen.append((v, w, len(svc.calls))))
body = svc.calls[0][1]["body"]
assert res["id"] == "VIDX" and res["publish_at"] == "2026-11-20T14:15:00Z", res        # 19:45 IST
assert body["status"] == {"privacyStatus": "private", "publishAt": "2026-11-20T14:15:00Z",
                          "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}, body["status"]
assert len(body["snippet"]["title"]) <= 100 and body["snippet"]["title"].endswith("#Shorts")
assert body["snippet"]["tags"] == ["gta"] and body["snippet"]["description"].endswith("FOOTER")
assert seen == [("VIDX", "2026-11-20T14:15:00Z", 1)]       # callback fired right after the video insert
assert "containsSyntheticMedia" not in up.upload(job, cfg, {}, service=Svc(), now=NOW)["publish_at"] and True
b2 = Svc(); up.upload(job, cfg, {}, service=b2, now=NOW)
assert "containsSyntheticMedia" not in b2.calls[0][1]["body"]["status"]
print("upload body + retry ok")

# ---- series playlist: created once, reused; failure never loses the upload
p1 = {"id": 1, "title": "Heist (Part 1/2)", "description": "", "video_path": "v", "part_no": 1, "total_parts": 2}
sv = Svc(); r1 = up.upload(p1, cfg, {}, service=sv, now=NOW)
assert [c[0] for c in sv.calls] == ["videos", "playlists", "playlistItems"] and r1["playlist_id"] == "PL1"
assert sv.calls[1][1]["body"]["snippet"]["title"] == "Heist"
sv = Svc(); r2 = up.upload({**p1, "part_no": 2}, cfg, {}, service=sv, playlist_id="PL1", now=NOW)
assert [c[0] for c in sv.calls] == ["videos", "playlistItems"]
saved = []; sv = Svc(playlist_error=True)
r3 = up.upload(p1, cfg, {}, service=sv, now=NOW, on_uploaded=lambda v, w: saved.append(v), log=quiet)
assert saved == ["VIDX"] and r3["id"] == "VIDX"
print("playlist ok")

# ---- runner: ordering + slot allocation + playlist propagation
tmp = Path(tempfile.mkdtemp()); db = DB(tmp / "j.db")
def mk(**f): return db.add(source="x", status="FINAL_APPROVED", description="d", video_path="v.mp4",
                           script=json.dumps({"tags": []}), **f)
a = mk(title="T (Part 1/2)", part_no=1, total_parts=2); db.update(a, series_id=a)
b = mk(title="T (Part 2/2)", part_no=2, total_parts=2, series_id=a)
solo = mk(title="Solo")
calls = []
def fake_upload(job, cfg, script, taken=(), after=None, playlist_id=None, on_uploaded=None, log=print, **kw):
    slot = up.next_slot(cfg, taken, after, now=NOW)
    calls.append((job["id"], slot, playlist_id, after))
    iso = slot.strftime("%Y-%m-%dT%H:%M:%SZ")
    on_uploaded(f"VID{job['id']}", iso)
    return {"id": f"VID{job['id']}", "publish_at": iso, "playlist_id": "PL9" if job.get("total_parts") else None}
up.upload = fake_upload
runner.advance(db, cfg, b, log=quiet)
assert db.get(b)["status"] == "FINAL_APPROVED" and not calls          # part 2 must wait for part 1
runner.advance(db, cfg, a, log=quiet);  assert db.get(a)["status"] == "UPLOADED"
runner.advance(db, cfg, b, log=quiet);  assert db.get(b)["status"] == "UPLOADED"
runner.advance(db, cfg, solo, log=quiet)
slots = {i: s for i, s, _, _ in calls}
assert slots[a] == s1 and slots[b] == s2 and slots[solo] == s3, slots
assert calls[1][2] == "PL9" and calls[1][3] == s1                     # part 2 saw playlist + part 1's time
assert db.get(a)["playlist_id"] == db.get(b)["playlist_id"] == "PL9"
assert db.get(solo)["publish_at"] == "2026-11-21T14:15:00Z"
runner.advance_pending(db, cfg, log=quiet)                             # nothing left to do, no crash
print("UPLOAD TESTS PASSED")
