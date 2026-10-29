from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class Track:
    path: str
    size: int = 0
    mtime_ns: int = 0
    fmt: str = ""
    title: str = ""
    artist: str = ""
    albumartist: str = ""
    album: str = ""
    track: int = 0
    disc: int = 0
    year: str = ""
    genre: str = ""
    duration: float = 0.0
    bitrate: int = 0          # kbps
    sample_rate: int = 0
    bit_depth: int = 0
    channels: int = 0
    lossless: bool = False
    has_picture: bool = False
    pic_w: int = 0
    pic_h: int = 0
    error: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def size_mib(self) -> float:
        return self.size / (1024 * 1024)

    @property
    def pic_res(self) -> int:
        return max(self.pic_w, self.pic_h)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})
