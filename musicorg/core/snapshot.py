"""Export a folder tree to JSON and rebuild a layout from it (old export_structure + re_organize)."""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .plan import Action, Plan
from .util import sanitize_component


def export_structure(root: str, out_path: str, exts: list[str]) -> dict:
    """Scan ``root`` (Artist/Album/file layout) and write a JSON snapshot."""
    root = os.path.expanduser(root)
    ext_set = tuple("." + e.lower().lstrip(".") for e in exts)
    structure: dict = defaultdict(lambda: defaultdict(list))
    files = []
    for dp, _dn, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        parts = [] if rel == "." else rel.split(os.sep)
        for fn in sorted(fns):
            if not fn.lower().endswith(ext_set):
                continue
            files.append({"rel": "/".join(parts + [fn]), "size": os.path.getsize(os.path.join(dp, fn))})
            if len(parts) >= 2:
                structure[parts[0]][os.path.join(*parts[1:])].append(fn)
    data = {"version": 1, "root": root, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "structure": structure, "files": files}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def load_snapshot(path: str) -> dict:
    """Accepts the new format and the old {artist: {album: [files]}} format."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if "structure" not in d:
        d = {"version": 0, "structure": d, "files": []}
    return d


@dataclass
class RestoreReport:
    wanted: int = 0
    found: int = 0
    missing: list[str] = field(default_factory=list)
    ambiguous: int = 0


def restore_plan(snapshot: dict, source_root: str, dest_root: str, cfg: Config, mode: str = "move",
                 sanitize: bool = True) -> tuple[Plan, RestoreReport]:
    """Rebuild snapshot layout. The source tree is indexed ONCE (old code walked it per file)."""
    source_root, dest_root = os.path.expanduser(source_root), os.path.expanduser(dest_root)
    index: dict[str, list[str]] = defaultdict(list)
    for dp, _dn, fns in os.walk(source_root):
        for fn in fns:
            index[fn].append(os.path.join(dp, fn))
    o = cfg.organize

    def san(s, keep_ext=False):
        if not sanitize:
            return s
        return sanitize_component(s, profile=o.sanitize_profile, repl=o.replace_char, norm=o.unicode_norm,
                                  max_len=o.max_component_len, keep_ext=keep_ext)

    plan, rep = Plan("restore", meta={"mode": mode, "dest_root": dest_root}), RestoreReport()
    for artist, albums in snapshot["structure"].items():
        for album, songs in albums.items():
            for song in songs:
                rep.wanted += 1
                hits = index.get(song)
                if not hits:
                    rep.missing.append(song)
                    continue
                if len(hits) > 1:
                    rep.ambiguous += 1
                rep.found += 1
                album_parts = [san(p) for p in album.replace("\\", "/").split("/")]
                dst = os.path.join(dest_root, san(artist), *album_parts, san(song, keep_ext=True))
                if os.path.abspath(dst) != os.path.abspath(hits[0]):
                    plan.actions.append(Action(mode, src=hits[0], dst=dst, note="ambiguous name" if len(hits) > 1 else ""))
    return plan, rep
