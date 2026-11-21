import argparse, json, sys
from pipeline import config
from pipeline.db import DB
from pipeline.runner import advance
from pipeline.detect import find_highlights

def _fc(text):
    """'0.78,0.05,0.2,0.3' -> JSON string of 4 fractions (x, y, width, height of the facecam)."""
    if not text:
        return None
    try:
        v = [float(t) for t in text.split(",")]
        assert len(v) == 4 and min(v) >= 0 and v[2] > 0 and v[3] > 0 and v[0] + v[2] <= 1.001 and v[1] + v[3] <= 1.001
    except Exception:
        sys.exit("--facecam must be x,y,width,height as fractions of the frame, e.g. 0.78,0.05,0.2,0.3")
    return json.dumps(v)

def main():
    ap = argparse.ArgumentParser(prog="shorts")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="queue one clip (local file or URL)")
    a.add_argument("source"); a.add_argument("--start", type=float); a.add_argument("--end", type=float)
    a.add_argument("--streamer"); a.add_argument("--game"); a.add_argument("--credit")
    a.add_argument("--permission", help="e.g. 'written OK from X on 2026-11-20'")
    a.add_argument("--notes", help="hints for the script writer")
    a.add_argument("--facecam", help="x,y,w,h fractions of the facecam, enlarged into its own panel")

    d = sub.add_parser("detect", help="auto-find highlights in a long recording and queue them")
    d.add_argument("video"); d.add_argument("-n", type=int, default=5)
    d.add_argument("--streamer"); d.add_argument("--game"); d.add_argument("--credit")

    se = sub.add_parser("series", help="split one long moment into Part 1..N")
    se.add_argument("source"); se.add_argument("--start", type=float, required=True)
    se.add_argument("--end", type=float, required=True)
    se.add_argument("--parts", type=int, help="number of parts (default: from --part-length)")
    se.add_argument("--part-length", type=float, default=35.0)
    se.add_argument("--streamer"); se.add_argument("--game"); se.add_argument("--credit")
    se.add_argument("--permission"); se.add_argument("--notes", help="overall story")
    se.add_argument("--facecam", help="x,y,w,h fractions of the facecam")
    se.add_argument("--part-note", action="append", default=[],
                    help="what happens in each part, repeat once per part, in order")

    fp = sub.add_parser("facecam-preview", help="draw the facecam rectangle on a frame to check it")
    fp.add_argument("video"); fp.add_argument("--rect", help="x,y,w,h fractions, e.g. 0.78,0.05,0.2,0.3")
    fp.add_argument("--at", type=float, default=5.0, help="second of the video to grab")
    sub.add_parser("auth", help="log in to YouTube (one time) and show the channel")
    lv = sub.add_parser("live", help="watch live streams, auto-clip highlights, run the pipeline")
    lv.add_argument("--no-record", action="store_true", help="don't record; just process segments already on disk")

    r = sub.add_parser("run", help="advance jobs until a human gate"); r.add_argument("--id", type=int)
    sub.add_parser("list")
    sub.add_parser("review", help="show everything waiting on you")
    ap_ = sub.add_parser("approve"); ap_.add_argument("id", type=int)
    rj = sub.add_parser("reject"); rj.add_argument("id", type=int); rj.add_argument("--reason", default="")
    st = sub.add_parser("set", help="edit script before re-render")
    st.add_argument("id", type=int); st.add_argument("--narration"); st.add_argument("--hook"); st.add_argument("--title")
    rt = sub.add_parser("retry", help="restart FAILED job(s) from the step that failed")
    rt.add_argument("id", type=int, nargs="?")
    rs = sub.add_parser("rescript", help="regenerate script+voice+render for jobs, in order")
    rs.add_argument("ids", type=int, nargs="+")
    rs.add_argument("--part-note", action="append", default=[],
                    help="replace each job's 'this part' note; one per id, same order as the ids")
    rd = sub.add_parser("redo", help="re-voice and re-render after editing the script"); rd.add_argument("id", type=int)

    args = ap.parse_args()
    cfg = config.load(args.config)
    db = DB(cfg["_ws"] / "jobs.db")

    if args.cmd == "add":
        jid = db.add(source=args.source, start=args.start, end=args.end, streamer=args.streamer,
                     game=args.game, source_credit=args.credit, permission=args.permission, notes=args.notes,
                     facecam=_fc(args.facecam))
        print(f"queued job {jid}")
    elif args.cmd == "detect":
        hl = find_highlights(args.video, args.n, cfg["detect"]["window_seconds"], cfg["detect"]["peak_position"])
        for s, e, sc in hl:
            jid = db.add(source=args.video, start=s, end=e, streamer=args.streamer,
                         game=args.game, source_credit=args.credit)
            print(f"job {jid}: {s}s-{e}s (score {sc:.3f})")
    elif args.cmd == "series":
        from pipeline.series import plan_parts
        plan = plan_parts(args.source, args.start, args.end, args.parts, args.part_length,
                          max_len=cfg["clip"]["max_seconds"])
        if args.part_note and len(args.part_note) != len(plan):
            sys.exit(f"{len(plan)} parts planned but {len(args.part_note)} --part-note given; "
                     f"give one per part or none (use --parts to change the count)")
        first = None
        for i, (s_, e_) in enumerate(plan, 1):
            ov = (f"STORY OVERVIEW (background only; later events are spoilers): {args.notes}\n"
                  if args.notes else "")
            pn = (args.part_note[i - 1] if args.part_note else
                  f"(not described; this is part {i} of {len(plan)}; stay general, invent nothing)")
            notes = f"{ov}THIS PART ONLY: {pn}"
            jid = db.add(source=args.source, start=s_, end=e_, streamer=args.streamer, game=args.game,
                         source_credit=args.credit, permission=args.permission, notes=notes,
                         facecam=_fc(args.facecam), series_id=first, part_no=i, total_parts=len(plan))
            first = first or jid
            db.update(jid, series_id=first)
            print(f"job {jid}: part {i}/{len(plan)}  {s_}s-{e_}s ({e_ - s_:.0f}s)")
    elif args.cmd == "facecam-preview":
        from pipeline.util import run
        out = cfg["_ws"] / "facecam_preview.png"
        vf = "null"
        if args.rect:
            x, y, w, h = json.loads(_fc(args.rect))
            vf = f"drawbox=x=iw*{x}:y=ih*{y}:w=iw*{w}:h=ih*{h}:color=red@1:t=6"
        run(["ffmpeg", "-y", "-ss", str(args.at), "-i", args.video, "-frames:v", "1", "-vf", vf, str(out)])
        print("saved", out, "- open it and check the red box covers the face")
    elif args.cmd == "auth":
        from pipeline import upload as up
        svc = up.build_service(up.load_creds(cfg, interactive=True))
        print("Logged in. Channel:", up.channel_title(svc))
    elif args.cmd == "live":
        from pipeline.live import run_live
        from pipeline.runner import advance_pending
        run_live(cfg, db, lambda: advance_pending(db, cfg), record=not args.no_record)
    elif args.cmd == "run":
        ids = [args.id] if args.id else [j["id"] for j in db.list() if j["status"] not in
               {"AWAITING_CLIP_APPROVAL", "AWAITING_FINAL_APPROVAL", "REJECTED", "FAILED", "UPLOADED"}]
        for i in ids:
            advance(db, cfg, i)
    elif args.cmd == "list":
        for j in db.list():
            tag = f"[{j['part_no']}/{j['total_parts']}] " if j.get("total_parts") else ""
            print(f"{j['id']:>3}  {j['status']:<24} {tag}{j['title'] or j['source']}"
                  + (f"  -> {j['publish_at']}" if j.get("publish_at") else ""))
    elif args.cmd == "review":
        for j in db.list():
            if j["status"] == "AWAITING_CLIP_APPROVAL":
                print(f"[{j['id']}] CLIP  {j['clip_path']}")
            elif j["status"] == "AWAITING_FINAL_APPROVAL":
                s = json.loads(j["script"])
                print(f"[{j['id']}] FINAL {j['video_path']}\n     title: {j['title']}\n     hook: {s['hook']}\n     narration: {s['narration']}")
            elif j["status"] == "FAILED":
                print(f"[{j['id']}] FAILED {j['error'][:200]}")
    elif args.cmd == "approve":
        j = db.get(args.id)
        nxt = {"AWAITING_CLIP_APPROVAL": "CLIP_APPROVED", "AWAITING_FINAL_APPROVAL": "FINAL_APPROVED"}.get(j["status"])
        if not nxt:
            sys.exit(f"job {args.id} is {j['status']}, nothing to approve")
        db.update(args.id, status=nxt); print(f"{args.id}: {nxt}. Run `run` to continue.")
    elif args.cmd == "reject":
        db.update(args.id, status="REJECTED", error=args.reason); print("rejected")
    elif args.cmd == "set":
        j = db.get(args.id); s = json.loads(j["script"])
        if args.narration: s["narration"] = args.narration
        if args.hook: s["hook"] = args.hook
        db.update(args.id, script=json.dumps(s), **({"title": args.title} if args.title else {}))
    elif args.cmd == "retry":
        ids = [args.id] if args.id else [j["id"] for j in db.list() if j["status"] == "FAILED"]
        for i in ids:
            j = db.get(i)
            if j["status"] != "FAILED":
                print(f"{i}: not failed ({j['status']})"); continue
            if not j["clip_path"]: back = "NEW"
            elif not j["script"]: back = "CLIP_APPROVED"
            elif not j["audio_path"]: back = "SCRIPTED"
            else: back = "VOICED"
            db.update(i, status=back, error=None)
            advance(db, cfg, i)
    elif args.cmd == "rescript":
        if args.part_note and len(args.part_note) != len(args.ids):
            sys.exit(f"{len(args.ids)} ids but {len(args.part_note)} --part-note")
        new = dict(zip(args.ids, args.part_note)) if args.part_note else {}
        for i in args.ids:
            j = db.get(i)
            if not j or not j["clip_path"]:
                sys.exit(f"job {i} has no clip yet; use run/approve first")
            fields = dict(status="CLIP_APPROVED", script=None, transcript=None, title=None,
                          description=None, audio_path=None, video_path=None, error=None)
            if i in new:
                n = j["notes"] or ""
                if n.startswith("THIS PART ONLY:"): ov = ""
                elif "\nTHIS PART ONLY:" in n: ov = n.split("\nTHIS PART ONLY:")[0] + "\n"
                elif "\nThis part: " in n:
                    ov = ("STORY OVERVIEW (background only; later events are spoilers): "
                          + n.split("\nThis part: ")[0] + "\n")
                else: ov = ""
                fields["notes"] = f"{ov}THIS PART ONLY: {new[i]}"
            db.update(i, **fields)
        for i in sorted(args.ids):          # in order, so each part can see the earlier ones
            advance(db, cfg, i)
    elif args.cmd == "redo":
        db.update(args.id, status="SCRIPTED"); advance(db, cfg, args.id)

if __name__ == "__main__":
    main()
