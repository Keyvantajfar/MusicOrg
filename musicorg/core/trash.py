"""App recycle bin: delete safely, restore later, or delete permanently."""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass
class BinItem:
    id: str
    original: str
    name: str
    deleted_at: float
    size: int
    reason: str = ""


class RecycleBin:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.sub_dir("bin", cfg.trash.bin_dir)
        self.manifest = self.root / "manifest.json"

    # -- manifest ------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            return json.loads(self.manifest.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, items: list[dict]) -> None:
        tmp = self.manifest.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.manifest)

    def list(self) -> list[BinItem]:
        return [BinItem(**d) for d in self._load()]

    def total_size(self) -> int:
        return sum(i.size for i in self.list())

    # -- operations ----------------------------------------------------
    def send(self, path: str | Path, reason: str = "") -> str:
        p = Path(path)
        size = p.stat().st_size
        item_id = uuid.uuid4().hex[:12]
        d = self.root / "items" / item_id
        d.mkdir(parents=True)
        shutil.move(str(p), str(d / p.name))
        items = self._load()
        items.append(BinItem(item_id, str(p.resolve() if p.exists() else p), p.name, time.time(), size, reason).__dict__)
        self._save(items)
        return item_id

    def restore(self, item_id: str, to: str | None = None) -> str:
        items = self._load()
        entry = next((i for i in items if i["id"] == item_id), None)
        if entry is None:
            raise KeyError(item_id)
        dst = Path(to or entry["original"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():  # never overwrite: add " (restored)"
            dst = dst.with_name(f"{dst.stem} (restored){dst.suffix}")
        src = self.root / "items" / item_id / entry["name"]
        shutil.move(str(src), str(dst))
        shutil.rmtree(self.root / "items" / item_id, ignore_errors=True)
        self._save([i for i in items if i["id"] != item_id])
        return str(dst)

    def purge(self, item_id: str) -> None:
        shutil.rmtree(self.root / "items" / item_id, ignore_errors=True)
        self._save([i for i in self._load() if i["id"] != item_id])

    def empty(self) -> int:
        n = len(self._load())
        shutil.rmtree(self.root / "items", ignore_errors=True)
        self._save([])
        return n

    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        old = [i for i in self._load() if i["deleted_at"] < cutoff]
        for i in old:
            self.purge(i["id"])
        return len(old)
