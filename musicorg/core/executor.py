"""Applies a Plan: dry-run aware, cancellable, journaled, optionally parallel."""
from __future__ import annotations

import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import tags
from .config import Config
from .journal import Journal
from .plan import Action, Plan
from .trash import RecycleBin

Progress = Callable[[int, int, str], None]


@dataclass
class RunResult:
    done: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    bytes_done: int = 0
    journal_id: str = ""
    dry_run: bool = False
    cancelled: bool = False
    detail: dict = field(default_factory=dict)


def _makedirs(path: str) -> list[str]:
    """Create path; return the directories that did not exist before (for undo)."""
    created, p = [], path
    while p and not os.path.isdir(p):
        created.append(p)
        p = os.path.dirname(p)
    os.makedirs(path, exist_ok=True)
    return created


def _same(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


class Executor:
    def __init__(self, cfg: Config, dry_run: bool | None = None, progress: Progress | None = None,
                 cancel: threading.Event | None = None, workers: int = 1):
        self.cfg = cfg
        self.dry = cfg.general.dry_run if dry_run is None else dry_run
        self.progress, self.cancel, self.workers = progress, cancel or threading.Event(), workers
        self.bin = RecycleBin(cfg)

    # ---------------------------------------------------------------- run
    def run(self, plan: Plan, mode: str = "") -> RunResult:
        acts = plan.active()
        res = RunResult(dry_run=self.dry)
        journal = None if self.dry else Journal(self.cfg, plan.kind, mode or plan.meta.get("mode", ""))
        res.journal_id = journal.id if journal else ""
        lock, counter = threading.Lock(), {"n": 0}

        def work(a: Action):
            if self.cancel.is_set():
                return
            try:
                outcome = self._do(a, journal)
                with lock:
                    if outcome == "skipped":
                        res.skipped += 1
                    else:
                        res.done += 1
                        res.bytes_done += _size(a)
            except Exception as ex:
                with lock:
                    res.failed.append(f"{a.op} {a.src or a.dst}: {ex}")
            with lock:
                counter["n"] += 1
                if self.progress:
                    self.progress(counter["n"], len(acts), os.path.basename(a.dst or a.src))

        trailing = [a for a in acts if a.op == "cleanup"]
        main = [a for a in acts if a.op != "cleanup"]
        if self.workers > 1:
            with ThreadPoolExecutor(self.workers) as pool:
                list(pool.map(work, main))
        else:
            for a in main:
                work(a)
        for a in trailing:
            work(a)
        res.cancelled = self.cancel.is_set()
        if journal:
            journal.close()
        return res

    # ------------------------------------------------------------ one action
    def _do(self, a: Action, journal: Journal | None) -> str:
        op, src, dst = a.op, a.src, a.dst
        if self.dry:
            return "dry"
        entry: dict = {"op": op, "src": src, "dst": dst}

        if op in ("move", "copy", "hardlink", "symlink"):
            if os.path.lexists(dst) and not _same(src, dst):
                if a.params.get("overwrite"):
                    os.remove(dst)
                else:
                    raise FileExistsError(dst)
            if _same(src, dst) and os.path.exists(dst) and src != dst and op != "move":
                return "skipped"
            entry["mkdirs"] = _makedirs(os.path.dirname(dst))
            if op == "move":
                shutil.move(src, dst)
            elif op == "copy":
                shutil.copy2(src, dst)
            elif op == "symlink":
                os.symlink(os.path.abspath(src), dst)
            else:
                try:
                    os.link(src, dst)
                except OSError as ex:
                    fb = self.cfg.organize.link_fallback
                    if fb == "symlink":
                        os.symlink(os.path.abspath(src), dst)
                        entry["op"] = "symlink"
                    elif fb == "copy":
                        shutil.copy2(src, dst)
                        entry["op"] = "copy"
                    else:
                        raise OSError(f"hardlink impossible ({ex})")
                    entry["requested"] = "hardlink"
        elif op == "trash":
            entry["bin_id"] = self.bin.send(src, a.note or "")
        elif op == "delete":
            os.remove(src)
        elif op == "convert":
            from .convert import convert_file
            entry["mkdirs"] = _makedirs(os.path.dirname(dst))
            convert_file(src, dst, a.params, self.cfg)
        elif op == "embed_art":
            data, mime = _cover_source(a)
            tags.embed_cover(dst, data, mime)
        elif op == "extract_art":
            if os.path.exists(dst):
                return "skipped"
            cov = tags.read_cover(src)
            if not cov:
                raise ValueError("no embedded cover")
            entry["mkdirs"] = _makedirs(os.path.dirname(dst))
            with open(dst, "wb") as fh:
                fh.write(cov[0])
        elif op == "tag":
            entry["old"] = tags.write_tags(src, a.params["values"])
        elif op == "cleanup":
            for root in a.params.get("roots", []):
                _remove_empty_dirs(root)
            return "ok"
        else:
            raise ValueError(f"unknown op {op}")
        if journal:
            journal.append(entry)
        return "ok"


def _cover_source(a: Action) -> tuple[bytes, str | None]:
    if "image" in a.params:
        with open(a.params["image"], "rb") as fh:
            return fh.read(), None
    cov = tags.read_cover(a.src)
    if not cov:
        raise ValueError(f"donor has no cover: {a.src}")
    return cov


def _size(a: Action) -> int:
    try:
        return os.path.getsize(a.dst if os.path.exists(a.dst) else a.src) if (a.src or a.dst) else 0
    except OSError:
        return 0


def _remove_empty_dirs(root: str) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
