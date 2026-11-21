HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,76,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,420,1
Style: Badge,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,7,50,50,150,1
Style: Next,Arial,80,&H003C3CFF,&H000000FF,&H00FFFFFF,&H64000000,-1,0,0,0,100,100,0,0,1,8,2,8,60,60,260,1
Style: CTA,Arial,130,&H0000F0FF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,9,2,5,60,60,0,1
Style: Hook,Arial,96,&H0000F0FF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,2,8,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def _ts(t):
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"

def build_ass(path, hook, narration, nar_dur, lead_in=0.4, words_per_chunk=3,
              badge=None, next_text=None, clip_dur=None, cta_dur=0):
    w = narration.split()
    chunks = [" ".join(w[i:i + words_per_chunk]) for i in range(0, len(w), words_per_chunk)]
    total = sum(len(c) for c in chunks) or 1
    lines, t = [], lead_in
    if hook:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(2.8)},Hook,,0,0,0,,{hook.upper()}")
    end = (clip_dur or 0) + cta_dur
    if badge and clip_dur:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(end)},Badge,,0,0,0,,{badge}")
    if next_text and clip_dur:
        start = clip_dur if cta_dur else max(0, clip_dur - 2.5)
        lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Next,,0,0,0,,{next_text.upper()}")
    if cta_dur and clip_dur:
        lines.append(f"Dialogue: 1,{_ts(clip_dur)},{_ts(end)},CTA,,0,0,0,,LIKE\\NSHARE\\NSUBSCRIBE")
    for c in chunks:
        d = nar_dur * len(c) / total
        lines.append(f"Dialogue: 0,{_ts(t)},{_ts(t + d)},Cap,,0,0,0,,{c.upper()}")
        t += d
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n".join(lines) + "\n")
