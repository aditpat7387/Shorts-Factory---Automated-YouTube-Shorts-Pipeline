import glob, hashlib, json, math
from pathlib import Path
from .util import run, duration, has_audio
from .captions import build_ass
from . import tts

CTA_DEFAULTS = {
    "enabled": True,
    "text": "Like, share and subscribe for more!",
    "text_series": "Like, share and subscribe, and watch part {next} next!",
    "min_seconds": 2.5,
}
N = "aresample=44100,aformat=channel_layouts=stereo"


def _facecam(job):
    """Facecam rectangle as fractions (x, y, w, h) of the source frame, or None."""
    raw = job.get("facecam")
    if not raw:
        return None
    try:
        x, y, w, h = [float(v) for v in (json.loads(raw) if isinstance(raw, str) else raw)]
    except Exception:
        print("  ! facecam setting not understood; ignoring it")
        return None
    if min(x, y) < 0 or w <= 0.02 or h <= 0.02 or x + w > 1.001 or y + h > 1.001:
        print("  ! facecam rectangle is outside the frame; ignoring it")
        return None
    return x, y, w, h


def _cta_settings(cfg, job):
    c = {**CTA_DEFAULTS, **(cfg.get("cta") or {})}
    if not c["enabled"]:
        return None
    p, n = job.get("part_no"), job.get("total_parts")
    text = c["text_series"].format(next=p + 1) if (p and n and n > 1 and p < n) else c["text"]
    return text, float(c["min_seconds"])


def _cta_audio(text, cfg, ws):
    """Synthesize the outro line once per distinct text/voice and reuse it for every Short."""
    key = hashlib.md5(f"{text}|{cfg['voice']['engine']}|{cfg['voice'].get('voice')}".encode()).hexdigest()[:10]
    base = ws / "audio" / f"cta_{key}"
    found = glob.glob(str(base) + ".*")
    return found[0] if found else tts.synthesize(text, str(base), cfg)


def _analyze_audio_for_beats(clip_path, cfg):
    """
    Analyze audio to find beat moments: loud peaks (kills, clutches, reactions).
    Returns list of (timestamp, intensity, type) where type in {'peak', 'sustained', 'reaction'}
    """
    import subprocess
    import numpy as np

    SR = 16000
    BLOCK = SR // 2
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(clip_path), "-vn", "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "-"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    vals = []
    while True:
        buf = p.stdout.read(BLOCK * 2)
        if len(buf) < BLOCK * 2:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768
        vals.append(float(np.sqrt(np.mean(x * x))))
    p.wait()
    energy = np.array(vals)  # per 0.5s
    if len(energy) < 10:
        return []

    # Smooth and baseline
    from .detect import _movavg
    sm = _movavg(energy, 6)       # ~3s smoothing
    base = _movavg(energy, 60)    # ~30s baseline
    score = sm - base
    ratio = sm / (base + 0.005)

    beats = []
    clip_dur = duration(clip_path)
    for idx, (sc, rt) in enumerate(zip(score, ratio)):
        t = idx * 0.5
        if t >= clip_dur:
            break
        if rt > 2.0 and sc > 0.02:  # Strong peak
            beat_type = "reaction" if rt > 3.5 else "peak"
            beats.append((t, float(sc), beat_type))
        elif rt > 1.5 and sc > 0.015:  # Sustained high energy
            beats.append((t, float(sc), "sustained"))

    # Merge nearby beats (within 2s)
    merged = []
    for t, sc, bt in beats:
        if merged and t - merged[-1][0] < 2.0:
            if sc > merged[-1][1]:
                merged[-1] = (t, sc, bt)
        else:
            merged.append((t, sc, bt))
    return merged


def _build_zoom_keyframes(beats, clip_dur, lead_in=0.4, cfg=None):
    """
    Generate zoompan filter keyframes for dynamic zoom-punches on beats.
    Returns list of keyframe dicts.
    """
    if not beats:
        return []

    base_zoom = cfg.get("edit", {}).get("base_zoom", 1.15) if cfg else 1.15
    keyframes = []

    for i, (t, intensity, btype) in enumerate(beats):
        punch_zoom = base_zoom + min(0.35, intensity * 2.5)
        punch_dur = 0.4 if btype == "peak" else 0.6 if btype == "reaction" else 0.8

        seed = hash((int(t * 10), btype)) % 1000
        px = (seed % 31 - 15) / 100.0
        py = ((seed // 31) % 21 - 10) / 100.0

        start = max(lead_in, t - 0.15)
        end = min(clip_dur, t + punch_dur)

        keyframes.append({
            "start": start, "end": end,
            "zoom": punch_zoom, "px": px, "py": py,
            "ease_in": 0.1, "ease_out": 0.25
        })
    return keyframes


def _build_zoompan_expression(keyframes, base_zoom=1.15):
    """Build a zoompan z-expression with smooth keyframe transitions."""
    if not keyframes:
        return str(base_zoom)

    # Use a piecewise expression with smooth easing
    expr = str(base_zoom)
    for kf in keyframes:
        s, e = kf["start"], kf["end"]
        z = kf["zoom"]
        # Smooth sin-based easing in/out
        expr += f"+if(gte(t,{s:.3f})*lt(t,{e:.3f}),({z}-{base_zoom})*sin((t-{s:.3f})/({e-s:.3f})*1.5708),0)"
    return expr


def _build_tactical_overlay_filter(t, intensity, btype, idx, W, H, game_y, game_h, clip_dur):
    """Generate filter for a single tactical overlay (arrow, circle, crosshair)."""
    mark_dur = 1.5
    mark_start = max(0, t - 0.3)
    mark_end = min(clip_dur, mark_start + mark_dur)

    seed = hash((int(t * 10), btype, idx)) % 1000
    cx = 0.15 + (seed % 70) / 100.0  # 0.15 to 0.85
    cy = 0.15 + ((seed // 70) % 70) / 100.0

    gx = cx * W
    gy = game_y + cy * game_h

    if btype == "reaction":
        # Red pulsing circle for streamer reaction
        color = "0xFF3333"
        shape = "circle"
    elif btype == "peak":
        # Green expanding circle for kill/clutch
        color = "0x33FF33"
        shape = "circle"
    else:
        # Yellow crosshair for sustained action
        color = "0xFFFF33"
        shape = "crosshair"

    overlay_label = f"tactical_{idx}"

    if shape == "circle":
        return (
            f"color=c=black@0:s={W}x{H}:d={mark_dur:.2f}[base_{idx}];"
            f"[base_{idx}]drawbox=x={gx-80}:y={gy-80}:w=160:h=160:color={color}@0.0:t=4,"
            f"drawbox=x={gx-60}:y={gy-60}:w=120:h=120:color={color}@0.6:t=4,"
            f"drawbox=x={gx-40}:y={gy-40}:w=80:h=80:color={color}@0.3:t=4,"
            f"drawbox=x={gx-20}:y={gy-20}:w=40:h=40:color={color}@0.1:t=4"
            f"[tactical_{idx}];"
            f"[tactical_{idx}]fade=in:0:10:alpha=1,fade=out:{mark_dur-0.1}:10:alpha=1[tactical_{idx}_fade]"
        )
    elif shape == "crosshair":
        return (
            f"color=c=black@0:s={W}x{H}:d={mark_dur:.2f}[base_{idx}];"
            f"[base_{idx}]drawbox=x={gx-2}:y={gy-60}:w=4:h=120:color={color}@0.8:t=fill,"
            f"drawbox=x={gx-60}:y={gy-2}:w=120:h=4:color={color}@0.8:t=fill,"
            f"drawbox=x={gx-15}:y={gy-15}:w=30:h=30:color={color}@0.0:t=4"
            f"[tactical_{idx}];"
            f"[tactical_{idx}]fade=in:0:8:alpha=1,fade=out:{mark_dur-0.1}:8:alpha=1[tactical_{idx}_fade]"
        )
    return ""


def _build_freeze_frame_filter(t, intensity, btype, idx, W, H, game_y, game_h, clip_dur, lead_in):
    """Generate filter for a freeze-frame analysis beat."""
    freeze_dur = 1.5 if btype == "peak" else 1.2
    freeze_start = max(lead_in, t - 0.1)
    freeze_end = min(clip_dur, freeze_start + freeze_dur)

    overlay_label = f"freeze_{idx}"

    analysis_texts = [
        "POSITIONING\nDECIDES\nTHE FIGHT",
        "ONE SHOT\nONE KILL\nPERFECT TIMING",
        "READ THE\nENEMY\nPREDICT THE PLAY",
        "CLUTCH\nINSTINCTS\nACTIVATED",
        "GAME SENSE\nOVER\nMECHANICS"
    ]
    text = analysis_texts[idx % len(analysis_texts)]

    # Use a more portable font approach
    font_path = "/Windows/Fonts/arialbd.ttf"
    try:
        import os
        if not os.path.exists(font_path):
            # Try common Linux/macOS paths
            for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/System/Library/Fonts/HelveticaNeue.ttc",
                      "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]:
                if os.path.exists(p):
                    font_path = p
                    break
    except Exception:
        pass

    return (
        f"[0:v]trim=start={freeze_start:.3f}:end={freeze_start:.3f},"
        f"setpts=PTS-STARTPTS,"
        f"scale={W}:{game_h}:force_original_aspect_ratio=increase,"
        f"pad={W}:{game_h}:(ow-iw)/2:(oh-ih)/2:color=black@0.85,"
        f"drawtext=fontfile={font_path}:fontsize=56:fontcolor=white:"
        f"text='{text}':x=(w-tw)/2:y=(h-th)/2:line_spacing=10,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.3:t=fill,"
        f"tpad=stop_mode=clone:stop_duration={freeze_dur:.3f},"
        f"fade=in:0:15:alpha=1,fade=out:{freeze_dur-0.2}:15:alpha=1[{overlay_label}]"
    )


def _build_facecam_filter(job, W, H, face_y, face_h, clip_dur):
    """Build polished facecam filter with rounded corners, border, glow."""
    fc = _facecam(job)
    if not fc:
        return "", None

    x, y, fw, fh = fc
    face_w = int(W * 0.8)
    face_x = (W - face_w) // 2

    # Facecam with rounded mask, border, subtle glow, entrance/exit animation
    filter_str = (
        f"[0:v]crop=w=iw*{fw}:h=ih*{fh}:x=iw*{x}:y=ih*{y},"
        f"scale={face_w}:-2,"
        f"split=2[face_main][face_mask];"
        f"[face_mask]geq=lum=255:a='if(gt(hypot(X-{face_w//2},Y-{face_h//2}),min({face_w},{face_h})/2-12),0,255)',"
        f"format=rgba[face_rounded];"
        f"[face_main][face_rounded]alphamerge,"
        f"format=rgba,"
        f"fade=in:0:20:alpha=1,"
        f"fade=out:{clip_dur-0.5}:20:alpha=1[facecam_styled]"
    )

    return filter_str, (face_x, face_y)


def render(job, cfg, script):
    ws, jid = cfg["_ws"], job["id"]
    work = ws / "work" / f"job{jid}"
    work.mkdir(parents=True, exist_ok=True)
    clip, nar = job["clip_path"], job["audio_path"]
    e = cfg["edit"]
    W, H = e["width"], e["height"]
    clip_dur = duration(clip)
    nar_dur = duration(nar)
    lead = cfg["voice"]["lead_in_seconds"]
    if lead + nar_dur > clip_dur:
        print(f"  ! narration ({nar_dur:.1f}s) longer than clip ({clip_dur:.1f}s); will be cut")

    # --- CTA ---
    cta = _cta_settings(cfg, job)
    cta_audio, D = None, 0.0
    if cta:
        cta_audio = _cta_audio(cta[0], cfg, ws)
        D = max(cta[1], duration(cta_audio) + 0.6)
    total = clip_dur + D

    # --- ANALYZE AUDIO FOR BEATS ---
    print("  🔍 Analyzing audio for dynamic beats...")
    beats = _analyze_audio_for_beats(clip, cfg)
    print(f"  🎯 Found {len(beats)} beat moments: " +
          ", ".join(f"{t:.1f}s({bt})" for t, _, bt in beats[:5]) +
          ("..." if len(beats) > 5 else ""))

    # --- LAYOUT CALCULATIONS ---
    fc = _facecam(job)
    has_facecam = fc is not None

    facecam_height_ratio = e.get("facecam_height_ratio", 0.30)
    facecam_margin_bottom = e.get("facecam_margin_bottom", 50)

    if has_facecam:
        face_h = int(H * facecam_height_ratio)
        face_y = H - face_h - facecam_margin_bottom
        game_h = face_y - 30
        game_y = 20
    else:
        face_h = 0
        face_y = 0
        game_h = H - 40
        game_y = 20

    # --- BUILD VIDEO FILTER CHAIN ---
    v_filters = []
    v_labels = []  # Track output labels for chaining

    # 1. Base gameplay with dynamic zoom-punches
    zoom_keyframes = _build_zoom_keyframes(beats, clip_dur, lead, cfg)
    zoom_expr = _build_zoompan_expression(zoom_keyframes)

    v_filters.append(
        f"[0:v]zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}[v_zoomed]"
    )
    last_v = "v_zoomed"

    # 2. Tactical overlays (circles, crosshairs) on beats
    tactical_labels = []
    tactical_count = e.get("tactical_count", 6)
    for i, (t, intensity, btype) in enumerate(beats[:tactical_count]):  # Max tactical_count tactical marks
        tf = _build_tactical_overlay_filter(t, intensity, btype, i, W, H, game_y, game_h, clip_dur)
        if tf:
            v_filters.append(tf)
            tactical_labels.append(f"tactical_{i}_fade")

    # 3. Freeze-frame analysis beats (top strongest peaks)
    freeze_beats = [b for b in beats if b[2] in ("peak", "reaction") and b[1] > 0.025]
    freeze_labels = []
    freeze_frame_count = e.get("freeze_frame_count", 3)
    for i, (t, intensity, btype) in enumerate(freeze_beats[:freeze_frame_count]):
        ff = _build_freeze_frame_filter(t, intensity, btype, i, W, H, game_y, game_h, clip_dur, lead)
        if ff:
            v_filters.append(ff)
            freeze_labels.append(f"freeze_{i}")

    # 4. Facecam layer
    facecam_filter, facecam_pos = _build_facecam_filter(job, W, H, face_y, face_h, clip_dur)
    if facecam_filter:
        v_filters.append(facecam_filter)
        facecam_label = "facecam_styled"
    else:
        facecam_label = None
        facecam_pos = None

    # 5. Compose all layers: base -> tactical -> freeze -> facecam -> captions
    compose_parts = [f"[{last_v}]"]
    overlay_inputs = []

    # Add tactical overlays
    for i, tl in enumerate(tactical_labels):
        compose_parts.append(f"[{tl}]")
        overlay_inputs.append(tl)

    # Add freeze frames (these are full-frame overlays with transparency)
    for fl in freeze_labels:
        compose_parts.append(f"[{fl}]")
        overlay_inputs.append(fl)

    # Build overlay chain
    if overlay_inputs:
        # Chain overlays one by one
        current = "v_zoomed"
        for i, ol in enumerate(overlay_inputs):
            next_label = f"v_overlay_{i}"
            enable = ""  # Could add enable='between(t,...)' but overlays already have fade
            compose_parts.append(f"[{current}][{ol}]overlay=0:0{enable}[{next_label}]")
            current = next_label
        last_v = current
    # else last_v stays "v_zoomed"

    # Add facecam
    if facecam_label and facecam_pos:
        fx, fy = facecam_pos
        compose_parts.append(f"[{last_v}][{facecam_label}]overlay=x={fx}:y={fy}[v_with_face]")
        last_v = "v_with_face"

    # Final scale/pad to ensure exact 1080x1920
    compose_parts.append(f"[{last_v}]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black[v_final]")

    v_chain = ";".join(compose_parts)

    # --- AUDIO FILTER CHAIN ---
    delay = int(lead * 1000)
    a_parts = []
    a_parts.append(f"[1:a]{N},adelay={delay}|{delay},atrim=duration={clip_dur:.3f},apad=whole_dur={clip_dur:.3f}[n0]")

    if cta_audio:
        a_parts.append(f"[2:a]{N},adelay=200|200[c0];[n0][c0]concat=n=2:v=0:a=1[nr]")
    else:
        a_parts.append("[n0]anull[nr]")

    a_parts.append(f"[nr]apad=whole_dur={total:.3f}[narp]")

    if has_audio(clip):
        a_parts.append(
            f"[narp]asplit=2[nsc][nmix];"
            f"[0:a]{N},volume={e['game_volume']},apad=whole_dur={total:.3f}[g];"
            f"[g][nsc]sidechaincompress=threshold={e['duck_threshold']}:ratio={e['duck_ratio']}"
            f":attack=20:release=300[duck];"
            f"[duck][nmix]amix=inputs=2:duration=first:dropout_transition=0,volume=1.8[a]"
        )
    else:
        a_parts.append("[narp]anull[a]")

    a_chain = ";".join(a_parts)

    # --- CAPTIONS ---
    p_no, p_tot = job.get("part_no"), job.get("total_parts")
    series = bool(p_no and p_tot and p_tot > 1)
    build_ass(work / "captions.ass", script.get("hook", ""), script["narration"],
              min(nar_dur, clip_dur - lead), lead,
              badge=f"PART {p_no}/{p_tot}" if series else None,
              next_text=f"FOLLOW FOR PART {p_no + 1}" if series and p_no < p_tot else None,
              clip_dur=clip_dur, cta_dur=D)

    # --- FULL FILTER COMPLEX ---
    filter_complex = v_chain + ";" + a_chain + f";[v_final]ass=captions.ass[v]"

    # --- RENDER ---
    out = ws / "renders" / f"job{jid}_short.mp4"
    inputs = ["-i", str(Path(clip).resolve()), "-i", str(Path(nar).resolve())]
    if cta_audio:
        inputs += ["-i", str(Path(cta_audio).resolve())]

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
           "-map", "[v]", "-map", "[a]",
           "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
           str(out.resolve())]

    print(f"  🎬 Rendering with {len(beats)} beats, {len(tactical_labels)} tactical, {len(freeze_labels)} freeze-frames...")
    run(cmd, cwd=str(work))
    return str(out)