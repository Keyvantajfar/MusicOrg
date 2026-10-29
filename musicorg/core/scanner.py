"""Library scan with an SQLite cache (path + size + mtime), threaded tag reading."""
from __future__ import annotations

import fnmatch
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import tags
from .config import Config
from .models import Track

Progress = Callable[[int, int, str], None]


class Index:
    """Persistent cache of scanned tracks."""

    def __init__(self, cfg: Config):
        self.path = cfg.data_dir() / "index.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.execute("CREATE TABLE IF NOT EXISTS tracks (path TEXT PRIMARY KEY, size INT, mtime_ns INT, data TEXT)")
        self.db.commit()

    def load(self) -> dict[str, tuple[int, int, str]]:
        return {r[0]: (r[1], r[2], r[3]) for r in self.db.execute("SELECT path,size,mtime_ns,data FROM tracks")}

    def put_many(self, tracks: list[Track]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?)",
                            [(t.path, t.size, t.mtime_ns, json.dumps(t.to_dict(), ensure_ascii=False)) for t in tracks])
        self.db.commit()

    def forget(self, paths: list[str]) -> None:
        self.db.executemany("DELETE FROM tracks WHERE path=?", [(p,) for p in paths])
        self.db.commit()

    def clear(self) -> None:
        self.db.execute("DELETE FROM tracks")
        self.db.commit()

    def close(self) -> None:
        self.db.close()


@dataclass
class ScanResult:
    tracks: list[Track] = field(default_factory=list)
    errors: list[Track] = field(default_factory=list)
    cached: int = 0
    read: int = 0
    cancelled: bool = False

    @property
    def total_size(self) -> int:
        return sum(t.size for t in self.tracks)


def iter_music_files(cfg: Config, roots: list[str]):
    sc = cfg.scan
    exts = {"." + e.lower().lstrip(".") for e in sc.formats}
    for root in roots:
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=sc.follow_symlinks):
            if not sc.recursive:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames
                           if not any(fnmatch.fnmatch(os.path.join(dirpath, d) + "/x", g) or fnmatch.fnmatch(os.path.join(dirpath, d), g)
                                      for g in sc.exclude_globs)]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in exts:
                    continue
                full = os.path.join(dirpath, fn)
                if any(fnmatch.fnmatch(full, g) for g in sc.exclude_globs):
                    continue
                if sc.include_globs and not any(fnmatch.fnmatch(full, g) for g in sc.include_globs):
                    continue
                if sc.min_size_kib:
                    try:
                        if os.path.getsize(full) < sc.min_size_kib * 1024:
                            continue
                    except OSError:
                        continue
                yield full


def scan(cfg: Config, roots: list[str] | None = None, progress: Progress | None = None,
         cancel: threading.Event | None = None) -> ScanResult:
    roots = roots or cfg.scan.roots
    res = ScanResult()
    idx = Index(cfg)
    try:
        cache = idx.load() if cfg.scan.use_cache else {}
        files = list(iter_music_files(cfg, roots))
        total = len(files)
        todo: list[str] = []
        for i, f in enumerate(files):
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                return res
            try:
                st = os.stat(f)
            except OSError:
                continue
            c = cache.get(f)
            if c and c[0] == st.st_size and c[1] == st.st_mtime_ns:
                try:
                    res.tracks.append(Track.from_dict(json.loads(c[2])))
                    res.cached += 1
                    continue
                except Exception:
                    pass
            todo.append(f)
        done = res.cached
        if progress:
            progress(done, total, "cache")
        fresh: list[Track] = []
        with ThreadPoolExecutor(max_workers=max(1, cfg.general.threads)) as pool:
            futs = {pool.submit(tags.read_track, f, cfg.scan.read_picture_size): f for f in todo}
            for fut in as_completed(futs):
                if cancel is not None and cancel.is_set():
                    for g in futs:
                        g.cancel()
                    res.cancelled = True
                    break
                t = fut.result()
                fresh.append(t)
                done += 1
                res.read += 1
                if progress and (done % 25 == 0 or done == total):
                    progress(done, total, t.name)
        good = [t for t in fresh if not t.error]
        res.tracks.extend(good)
        res.errors.extend(t for t in fresh if t.error)
        if good:
            idx.put_many(good)
        res.tracks.sort(key=lambda t: t.path)
        if progress:
            progress(total, total, "done")
    finally:
        idx.close()
    return res
