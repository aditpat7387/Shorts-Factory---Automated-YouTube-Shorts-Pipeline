import sqlite3, time

SCHEMA = """CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL, start REAL, end REAL,
 streamer TEXT, game TEXT, source_credit TEXT, permission TEXT, notes TEXT,
 status TEXT NOT NULL DEFAULT 'NEW',
 clip_path TEXT, transcript TEXT, script TEXT,
 title TEXT, description TEXT, audio_path TEXT, video_path TEXT,
 youtube_id TEXT, error TEXT, created REAL, updated REAL)"""

SEG_SCHEMA = """CREATE TABLE IF NOT EXISTS segments(
 path TEXT PRIMARY KEY, channel TEXT, status TEXT NOT NULL DEFAULT 'NEW',
 n_jobs INTEGER DEFAULT 0, created REAL)"""

NEW_COLS = {"series_id": "INTEGER", "part_no": "INTEGER", "total_parts": "INTEGER",
            "publish_at": "TEXT", "playlist_id": "TEXT", "facecam": "TEXT"}


class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(str(path))
        self.c.row_factory = sqlite3.Row
        self.c.execute(SCHEMA)
        self.c.execute(SEG_SCHEMA)
        have = {r[1] for r in self.c.execute("PRAGMA table_info(jobs)")}
        for col, typ in NEW_COLS.items():          # migrate older databases in place
            if col not in have:
                self.c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
        self.c.commit()

    # ---- jobs
    def add(self, **f):
        f.setdefault("created", time.time()); f["updated"] = time.time()
        cols = ",".join(f); q = ",".join("?" * len(f))
        cur = self.c.execute(f"INSERT INTO jobs({cols}) VALUES({q})", list(f.values()))
        self.c.commit()
        return cur.lastrowid

    def get(self, jid):
        r = self.c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None

    def update(self, jid, **f):
        f["updated"] = time.time()
        sets = ",".join(f"{k}=?" for k in f)
        self.c.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*f.values(), jid])
        self.c.commit()

    def list(self, status=None):
        if status:
            rows = self.c.execute("SELECT * FROM jobs WHERE status=? ORDER BY id", (status,))
        else:
            rows = self.c.execute("SELECT * FROM jobs ORDER BY id")
        return [dict(r) for r in rows]

    def count_jobs_since(self, source_like, ts):
        return self.c.execute("SELECT COUNT(*) FROM jobs WHERE source LIKE ? AND created > ?",
                              (source_like, ts)).fetchone()[0]

    def pending_ingest(self, source):
        """Jobs that still need the source file (clip not cut yet)."""
        return self.c.execute("SELECT COUNT(*) FROM jobs WHERE source=? AND clip_path IS NULL "
                              "AND status NOT IN ('REJECTED','FAILED')", (source,)).fetchone()[0]

    # ---- live segments
    def seg_get(self, path):
        r = self.c.execute("SELECT * FROM segments WHERE path=?", (path,)).fetchone()
        return dict(r) if r else None

    def seg_add(self, path, channel, status="DONE", n_jobs=0):
        self.c.execute("INSERT OR REPLACE INTO segments(path,channel,status,n_jobs,created) VALUES(?,?,?,?,?)",
                       (path, channel, status, n_jobs, time.time()))
        self.c.commit()

    def seg_set(self, path, status):
        self.c.execute("UPDATE segments SET status=? WHERE path=?", (status, path))
        self.c.commit()

    def segs(self, status=None):
        q = "SELECT * FROM segments" + (" WHERE status=?" if status else "") + " ORDER BY path"
        return [dict(r) for r in self.c.execute(q, (status,) if status else ())]
