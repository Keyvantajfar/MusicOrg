from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .config import Config
from .models import Track
from .util import fmt_size


def library_stats(tracks: list[Track], cfg: Config) -> dict:
    total = sum(t.size for t in tracks)
    by_fmt: Counter = Counter()
    by_fmt_size: Counter = Counter()
    for t in tracks:
        by_fmt[t.fmt] += 1
        by_fmt_size[t.fmt] += t.size
    artists = Counter((t.albumartist or t.artist or "(none)") for t in tracks)
    miss = {f: sum(1 for t in tracks if not getattr(t, f)) for f in ("title", "artist", "album", "albumartist", "year", "genre")}
    miss["cover"] = sum(1 for t in tracks if not t.has_picture)
    big = cfg.convert.rules[0]["min_size_mib"] if cfg.convert.rules else 0
    return {
        "tracks": len(tracks), "total_bytes": total, "total_size": fmt_size(total),
        "duration_hours": round(sum(t.duration for t in tracks) / 3600, 2),
        "lossless": sum(t.lossless for t in tracks), "lossy": sum(not t.lossless for t in tracks),
        "by_format": {k: {"files": by_fmt[k], "bytes": by_fmt_size[k]} for k in by_fmt},
        "top_artists": artists.most_common(15), "missing_tags": miss,
        "largest": [(t.name, round(t.size_mib, 1)) for t in sorted(tracks, key=lambda x: -x.size)[:10]],
        "files_over_rule_threshold": sum(1 for t in tracks if big and t.size_mib >= big),
    }


def export_tracks(tracks: list[Track], path: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".csv":
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(tracks[0].to_dict()) if tracks else ["path"])
            w.writeheader()
            for t in tracks:
                w.writerow(t.to_dict())
    else:
        p.write_text(json.dumps([t.to_dict() for t in tracks], indent=2, ensure_ascii=False), encoding="utf-8")
    return p
