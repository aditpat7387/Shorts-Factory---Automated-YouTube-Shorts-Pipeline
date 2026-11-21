"""YouTube upload: OAuth, slot scheduling (series-aware), resumable upload, series playlists."""
import datetime as dt
import re
import time
from pathlib import Path

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))   # India has no DST: a fixed offset is exact
UTC = dt.timezone.utc
SCOPES = ["https://www.googleapis.com/auth/youtube"]   # upload + playlists
_PART_RE = re.compile(r"\s*\(Part \d+/\d+\)\s*$")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def next_slot(cfg, taken=(), after=None, now=None):
    """Next free publish slot (UTC). Skips slots already used and anything before `after`
    (used to keep Part N publishing after Part N-1)."""
    u = cfg["upload"]
    now = now or dt.datetime.now(UTC)
    earliest = now + dt.timedelta(minutes=u.get("min_lead_minutes", 30))
    if after:
        earliest = max(earliest, after + dt.timedelta(minutes=1))
    taken = {t.astimezone(UTC).replace(second=0, microsecond=0) for t in taken}
    slots = sorted(tuple(map(int, s.split(":"))) for s in u["slots_ist"])
    day0 = earliest.astimezone(IST).date()
    for d in range(120):
        day = day0 + dt.timedelta(days=d)
        for h, m in slots:
            cand = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=IST).astimezone(UTC)
            if cand >= earliest and cand not in taken:
                return cand
    raise RuntimeError("no free upload slot in the next 120 days")


def load_creds(cfg, interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    u = cfg["upload"]
    tok = Path(u["token"])
    creds = Credentials.from_authorized_user_file(str(tok), SCOPES) if tok.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            tok.write_text(creds.to_json())
            return creds
        except Exception as e:
            if not interactive:
                raise RuntimeError("YouTube login expired or was revoked. If your Google OAuth app is "
                                   "in 'Testing' mode, logins last only 7 days. Run: python cli.py auth") from e
    if not interactive:
        raise RuntimeError("Not logged in to YouTube. Run: python cli.py auth")
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = InstalledAppFlow.from_client_secrets_file(u["client_secret"], SCOPES).run_local_server(port=0)
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json())
    return creds


def build_service(creds):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def get_service(cfg):
    return build_service(load_creds(cfg, interactive=False))


def channel_title(service):
    items = service.channels().list(part="snippet", mine=True).execute().get("items", [])
    return items[0]["snippet"]["title"] if items else "(no channel found on this account)"


def _media(path):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(path, chunksize=-1, resumable=True, mimetype="video/mp4")


def _run_upload(req, retries=5):
    resp, attempt = None, 0
    while resp is None:
        try:
            _, resp = req.next_chunk()
        except Exception as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status in (500, 502, 503, 504) and attempt < retries:
                attempt += 1
                time.sleep(min(2 ** attempt, 30))
                continue
            raise
    return resp


def _title(job):
    t = job["title"]
    if "#shorts" in t.lower():
        return t[:100]
    return t[:100 - len(" #Shorts")].rstrip() + " #Shorts"


def upload(job, cfg, script, taken=(), after=None, service=None, playlist_id=None,
           on_uploaded=None, now=None, log=print):
    """Upload as private + scheduled (publishAt). `on_uploaded(video_id, publish_iso)` fires the
    moment the video exists, so a later playlist problem can never cause a duplicate upload."""
    u = cfg["upload"]
    service = service or get_service(cfg)
    slot = next_slot(cfg, taken, after, now)
    publish_iso = slot.strftime("%Y-%m-%dT%H:%M:%SZ")

    desc = job["description"] or ""
    if u.get("description_footer"):
        desc = f"{desc}\n\n{u['description_footer']}".strip()
    status = {"privacyStatus": "private", "publishAt": publish_iso, "selfDeclaredMadeForKids": False}
    if u.get("synthetic_media") is not None:
        status["containsSyntheticMedia"] = bool(u["synthetic_media"])
    body = {"snippet": {"title": _title(job), "description": desc,
                        "tags": [t for t in script.get("tags", []) if t and str(t).strip()],
                        "categoryId": str(u.get("category_id", "20"))},
            "status": status}
    req = service.videos().insert(part="snippet,status", body=body, media_body=_media(job["video_path"]))
    vid = _run_upload(req)["id"]
    if on_uploaded:
        on_uploaded(vid, publish_iso)

    pid = playlist_id
    p_no, p_tot = job.get("part_no"), job.get("total_parts")
    if p_no and p_tot and p_tot > 1:
        try:
            if not pid:
                pid = service.playlists().insert(
                    part="snippet,status",
                    body={"snippet": {"title": _PART_RE.sub("", job["title"])[:150],
                                      "description": f"All {p_tot} parts, in order."},
                          "status": {"privacyStatus": "public"}}).execute()["id"]
            service.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": pid,
                                  "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
        except Exception as e:                      # the video is already safe; just report it
            log(f"  ! video uploaded, but adding it to the series playlist failed: {e}")
    return {"id": vid, "publish_at": publish_iso, "playlist_id": pid}
