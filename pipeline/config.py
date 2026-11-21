import os
from pathlib import Path
import yaml

def load(path="config.yaml", workspace=None):
    p = Path(path)
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg = cfg or {}
    raw = workspace or cfg.get("workspace") or "workspace"
    ws = Path(raw).expanduser()
    if not ws.is_absolute():
        ws = p.resolve().parent / ws       # relative to the config file, not the terminal
    for d in ("raw", "clips", "audio", "renders", "work"):
        os.makedirs(ws / d, exist_ok=True)
    cfg["_ws"] = ws
    return cfg