"""Split one long good moment into Part 1..N, cutting at natural pauses."""
import math, subprocess
import numpy as np

SR = 16000
BLOCK = SR // 2   # 0.5 s


def _energy(path, start, dur):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur),
                          "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    vals = []
    while True:
        buf = p.stdout.read(BLOCK * 2)
        if len(buf) < BLOCK * 2:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768
        vals.append(float(np.sqrt(np.mean(x * x))))
    p.wait()
    return np.array(vals)


def plan_parts(path, start, end, parts=None, part_length=35.0, max_len=45.0,
               min_len=12.0, radius=4.0, max_parts=8):
    """Return [(start, end), ...]. Cuts snap to the quietest moment within +-radius s
    of the even split, so parts end on a pause instead of mid-sentence/mid-action."""
    total = end - start
    if total <= 0:
        raise ValueError("end must be after start")
    n = parts or max(1, math.ceil(total / part_length))
    n = min(n, max_parts)
    if total / n > max_len:
        raise ValueError(f"{total:.0f}s in {n} parts is {total / n:.0f}s each, over the "
                         f"{max_len:.0f}s cap; use --parts {math.ceil(total / max_len)} or more")
    e = np.array([])
    if path and not str(path).startswith(("http://", "https://")):
        e = _energy(path, start, total)
        if len(e):
            e = np.convolve(e, np.ones(2) / 2, mode="same")

    cuts, prev = [], start
    for k in range(1, n):
        nominal = start + total * k / n
        lo = max(prev + min_len, nominal - radius, end - max_len * (n - k))
        hi = min(end - min_len, nominal + radius, prev + max_len)
        cut = nominal
        if len(e) and hi >= lo:
            i0, i1 = int((lo - start) / 0.5), min(int((hi - start) / 0.5), len(e) - 1)
            if i1 >= i0:
                i = min(range(i0, i1 + 1),
                        key=lambda j: (round(float(e[j]), 4), abs(start + j * 0.5 - nominal)))
                cut = start + i * 0.5 + 0.25
        cut = round(min(max(cut, prev + 1), end - 1), 2)
        cuts.append(cut)
        prev = cut
    bounds = [start] + cuts + [end]
    return [(round(bounds[i], 2), round(bounds[i + 1], 2)) for i in range(n)]
