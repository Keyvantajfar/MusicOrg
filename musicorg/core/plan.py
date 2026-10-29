"""A Plan is a list of Actions. Nothing touches the disk until a Plan is applied."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Action:
    op: str                      # move copy hardlink symlink trash delete convert embed_art extract_art tag cleanup
    src: str = ""
    dst: str = ""
    params: dict = field(default_factory=dict)
    note: str = ""
    enabled: bool = True


@dataclass
class Plan:
    kind: str                    # organize | dedupe | artwork | sync | restore
    actions: list[Action] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def active(self) -> list[Action]:
        return [a for a in self.actions if a.enabled]

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"kind": self.kind, "meta": self.meta, "actions": [asdict(a) for a in self.actions]},
                                indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(d["kind"], [Action(**a) for a in d["actions"]], d.get("meta", {}))


def plan_path(cfg, kind: str) -> Path:
    return cfg.sub_dir("plans") / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.json"
