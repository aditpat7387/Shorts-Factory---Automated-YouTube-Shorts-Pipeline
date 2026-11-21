import json, re

SYSTEM = """You write narration for 20-40 second YouTube Shorts gaming clips.
The narration is spoken by an AI voice OVER the clip, so it must add insight: explain the
mechanic, the decision, or why the play worked or failed. Never just describe the visuals.
Only state events that appear in the editor notes; never invent details (wanted levels, items,
backstory, motives). No generic life-lesson closing lines.
Return ONLY JSON: {"hook": "<=7 words, on-screen text, curiosity-driven",
"narration": "45-70 words, energetic simple English, first sentence is the hook payoff",
"title": "<=55 chars, no clickbait lies", "description": "2 sentences",
"tags": ["5-8 tags"]}"""

SERIES_RULES = """
This Short is PART {p} of {n} of a series built from one continuous story.
- The ONLY events that happen in this Short are those under 'THIS PART ONLY'. The story overview is
  background: anything in it that is not under THIS PART ONLY happens in ANOTHER part, so never
  mention it, imply it already happened, or spoil it.
- {opening}
- Do not repeat narration from earlier parts.
- {ending}
- Do NOT put part numbers in the title or hook; they are added automatically."""

def _notes_for_prompt(job):
    """Normalise notes so the model can always tell 'this part' from the whole-story overview."""
    n = job.get("notes") or ""
    if "THIS PART ONLY:" in n:
        return n
    if "\nThis part: " in n:                       # older jobs queued before the label change
        ov, pt = n.split("\nThis part: ", 1)
        return f"STORY OVERVIEW (background only; later events are spoilers): {ov}\nTHIS PART ONLY: {pt}"
    return n

def _series_rules(job):
    p, n = job.get("part_no"), job.get("total_parts")
    if not (p and n and n > 1):
        return ""
    ending = ("End with a one-line cliffhanger that raises a question. Tease only vaguely; do not name "
              "or describe what happens next." if p < n
              else "This is the finale: deliver the payoff, then a short closing line.")
    opening = ("Open with a recap of at most 8 words drawn from the earlier narration (never from the "
               "overview), then go straight into this part." if p > 1
               else "Open with the hook payoff; the viewer is new to the story.")
    return SERIES_RULES.format(p=p, n=n, ending=ending, opening=opening)

VISION_NOTE = ("The {n} images above are frames from the clip in chronological order. Base the narration on "
               "what is visible and on the transcript/notes. If you cannot tell what is happening, keep it "
               "general and honest; never invent specific events, names or numbers.")

def _template(job):
    who = job.get("streamer") or "this streamer"
    game = job.get("game") or "this game"
    p, n = job.get("part_no"), job.get("total_parts")
    hook = "WAIT FOR THE END"
    if p and n and n > 1:
        hook = "WHAT HAPPENS NEXT?" if p < n else "THE FINAL PAYOFF"
    return {
        "hook": hook,
        "narration": (f"Watch what {who} does here in {game}. Most players would panic and "
                      f"retreat, but the key is the positioning in the first two seconds. "
                      f"That one decision creates the opening, and everything after it is "
                      f"just execution. Now watch the result."),
        "title": f"{who}'s Clutch Play in {game}",
        "description": f"A breakdown of a great moment from {who} in {game}.",
        "tags": [game, "gaming", "clutch", "highlights", "shorts"],
    }

def write_script(job, cfg):
    llm = cfg["llm"]
    if llm["provider"] != "anthropic":
        return _template(job)
    import anthropic
    client = anthropic.Anthropic()
    prev = job.get("prev_parts") or []
    prev_txt = "\n".join(f"Part {x['part_no']} narration: {x['narration']}" for x in prev)
    ctx = (_series_rules(job) + "\n\n" if _series_rules(job) else "") + \
          (f"Earlier parts already published:\n{prev_txt}\n\n" if prev_txt else "") + \
          (f"Streamer: {job.get('streamer')}\nGame: {job.get('game')}\n"
           f"Notes from editor: {_notes_for_prompt(job)}\n"
           f"Transcript of the clip: {job.get('transcript') or '(none)'}")
    content = ctx
    if llm.get("vision", True) and job.get("clip_path"):
        try:
            from . import vision
            frames = vision.extract_frames(job["clip_path"], int(llm.get("frames", 6)))
            if frames:
                content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": f}}
                           for f in frames]
                content.append({"type": "text", "text": VISION_NOTE.format(n=len(frames)) + "\n\n" + ctx})
        except Exception as e:                      # fall back to text-only rather than failing the job
            print(f"  ! could not extract frames ({e}); writing from notes only")
    last = None
    for _ in range(3):
        msg = client.messages.create(model=llm["model"], max_tokens=8000, system=SYSTEM,
                                     messages=[{"role": "user", "content": content}])
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        a, b = txt.find("{"), txt.rfind("}")
        if msg.stop_reason != "max_tokens" and a != -1 and b > a:
            try:
                return json.loads(txt[a:b + 1])
            except json.JSONDecodeError as e:
                last = e
        else:
            last = RuntimeError(f"incomplete reply (stop_reason={msg.stop_reason})")
    raise RuntimeError(f"script generation failed after 3 attempts: {last}")

def transcribe(path, cfg):
    t = cfg["transcribe"]
    if t["engine"] != "faster-whisper":
        return ""
    from faster_whisper import WhisperModel
    segs, _ = WhisperModel(t["model"], compute_type="int8").transcribe(str(path))
    return " ".join(s.text.strip() for s in segs)
