"""Highlight finder for long VODs/recordings: loudness spikes (crowd/streamer reactions,
explosions, kill sounds) relative to the local baseline."""
import subprocess
import numpy as np

SR = 16000
BLOCK = SR // 2          # 0.5 s blocks

def _energy(path):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "-"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    vals = []
    while True:
        buf = p.stdout.read(BLOCK * 2)
        if len(buf) < BLOCK * 2:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768
        vals.append(float(np.sqrt(np.mean(x * x))))
    p.wait()
    return np.array(vals)

def _movavg(x, k):
    """Centered moving average; near the edges it averages only the samples that exist."""
    n = len(x)
    k = max(1, min(k, n))
    c = np.concatenate(([0.0], np.cumsum(x)))
    i = np.arange(n)
    lo = np.clip(i - k // 2, 0, n)
    hi = np.clip(i - k // 2 + k, 0, n)
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)

def find_highlights(path, n=5, window=30, peak_pos=0.7, min_ratio=0.0):
    e = _energy(path)
    if len(e) < 10:
        return []
    sm = _movavg(e, 6)        # ~3 s smoothing
    base = _movavg(e, 120)    # ~60 s local baseline (window shrinks near the edges, never fails on short audio)
    score = sm - base
    total = len(e) * 0.5
    picks = []
    for idx in np.argsort(score)[::-1]:
        if min_ratio and sm[idx] / (base[idx] + 0.005) < min_ratio:
            continue
        t = idx * 0.5
        s = max(0.0, t - window * peak_pos)
        en = min(total, s + window)
        if en - s < window * 0.6:
            continue
        if any(not (en <= ps or s >= pe) for ps, pe, _ in picks):
            continue
        picks.append((float(round(s, 1)), float(round(en, 1)), float(score[idx])))
        if len(picks) >= n:
            break
    return sorted(picks)
