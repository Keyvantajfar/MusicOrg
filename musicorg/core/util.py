from __future__ import annotations

import re
import unicodedata

_WIN_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def fmt_size(nbytes: float) -> str:
    """Human size with all three units the old FileSize.py printed."""
    mib = nbytes / (1024 * 1024)
    return f"{mib:,.2f} MiB / {mib / 1024:,.2f} GiB / {nbytes / 1e9:,.2f} GB"


def short_size(nbytes: float) -> str:
    n = float(nbytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TiB"


def normalize_text(s: str, *, case: bool = True, punct: bool = True, brackets: bool = False) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    if brackets:
        s = _BRACKETS.sub(" ", s)
    if case:
        s = s.casefold()
    if punct:
        s = _PUNCT.sub("", s)
    return _WS.sub(" ", s).strip()


def sanitize_component(name: str, *, profile: str = "windows", repl: str = "_", norm: str = "NFC",
                       case_mode: str = "keep", max_len: int = 0, keep_ext: bool = False) -> str:
    """Make one path component safe. ``keep_ext`` protects the extension when shortening."""
    s = str(name)
    if norm in ("NFC", "NFD"):
        s = unicodedata.normalize(norm, s)
    if profile == "ascii":
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    if profile in ("windows", "ascii"):
        s = _WIN_ILLEGAL.sub(repl, s)
    else:
        s = s.replace("/", repl).replace("\x00", "")
    s = _WS.sub(" ", s).strip()
    if profile in ("windows", "ascii"):
        s = s.rstrip(". ")
        stem = s.split(".")[0].upper()
        if stem in _RESERVED:
            s = "_" + s
    if case_mode == "lower":
        s = s.lower()
    elif case_mode == "title":
        s = s.title()
    if max_len and len(s) > max_len:
        if keep_ext and "." in s[-8:]:
            stem, _, ext = s.rpartition(".")
            s = stem[: max(1, max_len - len(ext) - 1)].rstrip() + "." + ext
        else:
            s = s[:max_len].rstrip()
    return s or "_"
