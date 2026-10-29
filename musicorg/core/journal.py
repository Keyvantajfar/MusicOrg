"""Journal of everything that was applied -> undo, finalize (hardlink/symlink previews) and discard."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import tags
from .config import Config
from .trash import RecycleBin

PREVIEW_OPS = {"hardlink", "symlink", "copy"}


class Journal:
    def __init__(self, cfg: Config, kind: str, mode: str = "", jid: str | None = None):
        self.dir = cfg.sub_dir("journals")
        self.id = jid or time.strftime("%Y%m%d-%H%M%S") + f"-{kind}"
        self.meta_path = self.dir / f"{self.id}.meta.json"
        self.log_path = self.dir / f"{self.id}.jsonl"
        self.lock = threading.Lock()
        self.meta = {"id": self.id, "kind": kind, "mode": mode, "created": time.time(), "state": "applied", "count": 0}
        if jid is None:
            self._write_meta()
            self.log_path.touch()
        else:
            self.meta = json.loads(self.meta_path.read_text())

    def _write_meta(self):
        self.meta_path.write_text(json.dumps(self.meta, indent=1))

    def append(self, entry: dict) -> None:
        with self.lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.meta["count"] += 1

    def entries(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(l) for l in self.log_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def set_state(self, state: str) -> None:
        self.meta["state"] = state
        self._write_meta()

    def close(self) -> None:
        self._write_meta()

    @property
    def state(self) -> str:
        return self.meta["state"]

    def is_preview(self) -> bool:
        return self.state == "applied" and any(e["op"] in PREVIEW_OPS for e in self.entries())


def list_journals(cfg: Config) -> list[Journal]:
    out = []
    for p in sorted(cfg.sub_dir("journals").glob("*.meta.json"), reverse=True):
        try:
            out.append(Journal(cfg, "", jid=p.name[:-len(".meta.json")]))
        except Exception:
            pass
    return out


def pending_previews(cfg: Config) -> list[Journal]:
    return [j for j in list_journals(cfg) if j.meta.get("kind") == "organize" and j.is_preview()]


def _rm_dirs(dirs: list[str]) -> None:
    for d in sorted(set(dirs), key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass


def _remove(p: str) -> None:
    if os.path.lexists(p):
        os.remove(p)


@dataclass
class Outcome:
    done: int = 0
    skipped: int = 0
    irreversible: int = 0
    errors: list[str] = None

    def __post_init__(self):
        self.errors = self.errors or []


def undo(cfg: Config, j: Journal) -> Outcome:
    """Reverse a journal, newest entry first."""
    out, bin_ = Outcome(), RecycleBin(cfg)
    dirs: list[str] = []
    for e in reversed(j.entries()):
        op, src, dst = e["op"], e.get("src", ""), e.get("dst", "")
        dirs += e.get("mkdirs", [])
        try:
            if op == "move":
                if os.path.lexists(dst) and not os.path.lexists(src):
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    shutil.move(dst, src)
                    out.done += 1
                else:
                    out.skipped += 1
            elif op in ("copy", "hardlink", "symlink", "convert", "extract_art"):
                _remove(dst)
                out.done += 1
            elif op == "trash":
                bin_.restore(e["bin_id"])
                out.done += 1
            elif op == "tag":
                tags.write_tags(src, e["old"])
                out.done += 1
            else:
                out.irreversible += 1
        except Exception as ex:
            out.errors.append(f"{op} {dst or src}: {ex}")
    _rm_dirs(dirs)
    j.set_state("undone")
    return out


def finalize(cfg: Config, j: Journal) -> Outcome:
    """Make a preview permanent: old names disappear, new names stay."""
    out, bin_ = Outcome(), RecycleBin(cfg)
    for e in j.entries():
        op, src, dst = e["op"], e.get("src", ""), e.get("dst", "")
        try:
            if op == "hardlink":
                if os.path.exists(dst) and (not os.path.exists(src) or os.path.samefile(src, dst)):
                    _remove(src)
                    out.done += 1
                else:
                    out.skipped += 1
            elif op == "symlink":
                if os.path.islink(dst) and os.path.exists(src):
                    os.remove(dst)
                    shutil.move(src, dst)
                    out.done += 1
                else:
                    out.skipped += 1
            elif op == "copy":
                if os.path.exists(src) and os.path.exists(dst) and os.path.getsize(src) == os.path.getsize(dst):
                    if cfg.organize.finalize_old == "delete":
                        os.remove(src)
                    else:
                        bin_.send(src, "finalized copy")
                    out.done += 1
                else:
                    out.skipped += 1
        except Exception as ex:
            out.errors.append(f"{op} {src}: {ex}")
    j.set_state("finalized")
    return out


def discard(cfg: Config, j: Journal) -> Outcome:
    """Throw a preview away; the originals were never touched."""
    out = Outcome()
    dirs: list[str] = []
    for e in j.entries():
        dirs += e.get("mkdirs", [])
        if e["op"] in PREVIEW_OPS:
            try:
                _remove(e["dst"])
                out.done += 1
            except Exception as ex:
                out.errors.append(f"{e['dst']}: {ex}")
    _rm_dirs(dirs)
    j.set_state("discarded")
    return out
