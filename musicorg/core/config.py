"""Configuration: every option of the program lives here, with defaults.

The TUI builds its Settings screen automatically from these dataclasses, so adding
a field here (with ``opt(...)``) is all that is needed to make it configurable.
Stored as TOML (read with ``tomllib``, written with a tiny built-in writer).
"""
from __future__ import annotations

import copy
import json
import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


def opt(default: Any, help: str = "", choices: list[str] | None = None):
    """Declare a configurable option."""
    meta = {"help": help, "choices": choices}
    if isinstance(default, (list, dict)):
        return field(default_factory=lambda d=default: copy.deepcopy(d), metadata=meta)
    return field(default=default, metadata=meta)


AUDIO_EXTS = ["flac", "mp3", "m4a", "mp4", "ogg", "opus", "wav", "aiff", "aif"]


@dataclass
class GeneralCfg:
    """General"""
    data_dir: str = opt("", "Where index, recycle bin, journals and plans are stored (empty = ~/.local/share/musicorg)")
    threads: int = opt(8, "Worker threads for reading tags / hashing")
    dry_run: bool = opt(False, "Dry-run: 'Apply' only simulates and changes nothing")
    confirm_apply: bool = opt(True, "Ask for confirmation before applying a plan")


@dataclass
class ScanCfg:
    """Library scan"""
    roots: list = opt([], "Folders to scan (the 'library')")
    formats: list = opt(list(AUDIO_EXTS), "File extensions treated as music")
    recursive: bool = opt(True, "Scan sub-folders")
    follow_symlinks: bool = opt(False, "Follow symbolic links while scanning")
    include_globs: list = opt([], "If not empty, only paths matching one of these globs are used")
    exclude_globs: list = opt(["*/.*", "*/@eaDir/*", "*/_Unsorted/*"], "Paths matching these globs are ignored")
    min_size_kib: int = opt(0, "Ignore files smaller than this (KiB, 0 = off)")
    use_cache: bool = opt(True, "Use the scan cache (only re-read files whose size/mtime changed)")
    read_picture_size: bool = opt(True, "Decode embedded cover to learn its resolution (needs Pillow)")


@dataclass
class DedupeCfg:
    """Duplicates"""
    match_fields: list = opt(["title", "artist", "album"],
                             "Tags that must be equal for two files to be duplicates (title, artist, album, albumartist)")
    case_insensitive: bool = opt(True, "Ignore upper/lower case when comparing")
    strip_punctuation: bool = opt(True, "Ignore punctuation and extra spaces when comparing")
    strip_brackets: bool = opt(False, "Ignore text in () and [] such as '(Remastered)' when comparing")
    duration_tolerance_s: float = opt(3.0, "Same song only if durations differ by <= this many seconds (0 = ignore duration)")
    exact_hash: bool = opt(True, "Byte-identical files are always duplicates, even with missing tags")
    missing_policy: str = opt("review", "Files missing a match tag: review = never touched, put in Review list | filename = use file name as title | loose = treat missing as empty value (risky)",
                              ["review", "filename", "loose"])
    unknown_values: list = opt(["unknown", "unknown artist", "unknown album", "untitled", "<unknown>", "n/a", "none", ""],
                               "Tag values that count as 'missing'")
    criteria: list = opt(["lossless", "bit_depth", "sample_rate", "bitrate", "has_picture", "tag_completeness", "size"],
                         "Keeper ranking, first = most important. Options: lossless, bit_depth, sample_rate, bitrate, has_picture, pic_resolution, tag_completeness, size, newest, oldest, preferred_path")
    tie_tolerance_pct: float = opt(5.0, "Numeric criteria within this % of the best are a tie -> next criterion decides")
    preferred_paths: list = opt([], "For criterion 'preferred_path': files whose path contains one of these win")
    action: str = opt("bin", "What to do with the losing copies", ["report", "quarantine", "bin", "delete"])
    quarantine_dir: str = opt("", "Folder for action=quarantine (empty = <data_dir>/quarantine)")
    review_dir: str = opt("", "Folder to move 'Review' files to when you choose that (empty = <data_dir>/review)")


@dataclass
class ArtworkCfg:
    """Artwork"""
    enabled: bool = opt(True, "Enable artwork fixing")
    donor_order: list = opt(["duplicate", "album_sibling", "folder_image"],
                            "Where to look for a cover, in order: duplicate, album_sibling, folder_image")
    folder_image_names: list = opt(["cover", "folder", "front", "albumart", "album"], "File names (without extension) of folder covers")
    min_resolution: int = opt(0, "Cover counts as 'too small' below this many pixels (longest side, 0 = off)")
    replace_lower_res: bool = opt(False, "Also replace covers smaller than min_resolution when a better one exists")
    extract_to_folder: bool = opt(False, "Also save the embedded cover as an image file next to the music")
    extract_name: str = opt("cover.jpg", "File name used when extracting covers")


@dataclass
class OrganizeCfg:
    """Organize"""
    dest_root: str = opt("", "Destination root of the organized library")
    template: str = opt("{albumartist}/{album}/{filename}",
                        "Path template. Tokens: albumartist artist album title track disc disc_prefix year genre filename format. e.g. {albumartist}/{album}/{disc_prefix}{track:02} {title}")
    mode: str = opt("move", "move | copy | hardlink (preview, originals untouched) | symlink (preview, originals untouched)",
                    ["move", "copy", "hardlink", "symlink"])
    link_fallback: str = opt("symlink", "If a hardlink is impossible (other disk / FAT32): fall back to", ["symlink", "copy", "fail"])
    finalize_old: str = opt("bin", "On 'Finalize' of a copy preview: old files go to", ["bin", "delete"])
    main_artist_rule: str = opt("smart", "How the album's artist folder is chosen. smart = album-artist tag, else dominant artist, else split / Various Artists | albumartist_tag = tag or track artist | most_frequent = old behaviour (album name only)",
                                ["smart", "albumartist_tag", "most_frequent"])
    dominant_share: float = opt(0.6, "smart: an artist owning at least this share of an album's untagged tracks is its main artist")
    compilation_min_artists: int = opt(5, "smart: albums with at least this many different artists (and no dominant one) become compilations")
    compilation_name: str = opt("Various Artists", "Folder name for compilations")
    unify_case_variants: bool = opt(True, "'greatest hits' and 'Greatest Hits' go to the same folder (most common spelling wins)")
    required_tags: list = opt(["artist", "album"], "Files missing one of these tags are treated as 'unknown'")
    unknown_policy: str = opt("unsorted_dir", "unsorted_dir = put in the unsorted folder | defaults = use the default names below | skip = leave them where they are",
                              ["unsorted_dir", "defaults", "skip"])
    unsorted_dir: str = opt("_Unsorted", "Unsorted folder (relative to dest_root, or an absolute path)")
    unknown_artist: str = opt("Unknown Artist", "Name used when policy=defaults")
    unknown_album: str = opt("Unknown Album", "Name used when policy=defaults")
    collision: str = opt("rename", "If the target file already exists", ["skip", "rename", "overwrite", "keep_larger"])
    sanitize_profile: str = opt("windows", "none = only '/' | windows = FAT32/NTFS/exFAT safe | ascii = windows + only ASCII letters",
                                ["none", "windows", "ascii"])
    replace_char: str = opt("_", "Replacement for illegal characters (empty = remove them, like the old scripts)")
    unicode_norm: str = opt("NFC", "Unicode normalisation of names", ["none", "NFC", "NFD"])
    case_mode: str = opt("keep", "Change case of folder/file names", ["keep", "lower", "title"])
    max_component_len: int = opt(150, "Max length of one folder/file name (0 = off)")
    max_path_len: int = opt(0, "Max length of the whole path (0 = off, e.g. 255 for old Windows)")
    move_sidecars: bool = opt(True, "Also move/copy sidecar files with the same name (.lrc, .cue)")
    sidecar_exts: list = opt(["lrc", "cue"], "Sidecar extensions")
    remove_empty_dirs: bool = opt(True, "After 'move', delete folders that became empty")


@dataclass
class ConvertCfg:
    """Convert & Sync (device / iPod)"""
    target_root: str = opt("", "Where the device copy is written (e.g. the iPod mount point)")
    layout: str = opt("mirror", "mirror = keep sub-folders | flat = all files in one folder (old behaviour) | template = use the Organize template",
                      ["mirror", "flat", "template"])
    default_action: str = opt("copy", "Action when no rule matches", ["copy", "link", "convert", "skip"])
    link_mode: str = opt("symlink", "How 'link' works (hardlinks fail on FAT32 / other disks)", ["hardlink", "symlink"])
    incremental: bool = opt(True, "Skip files already present and not older than the source")
    delete_orphans: bool = opt(False, "Remove files on the device that are no longer in the library")
    orphan_action: str = opt("bin", "Orphans go to", ["bin", "delete"])
    sanitize_profile: str = opt("windows", "Name sanitising for the device file system", ["none", "windows", "ascii"])
    ffmpeg_path: str = opt("ffmpeg", "ffmpeg executable")
    jobs: int = opt(2, "Parallel ffmpeg jobs")
    dither: bool = opt(True, "Use dither when reducing bit depth")
    no_upsample: bool = opt(True, "Never raise sample rate / bit depth above the source")
    rules: list = opt([
        {"name": "Big files -> CD quality", "enabled": True, "min_size_mib": 200.0, "max_size_mib": 0.0,
         "min_sample_rate": 0, "min_bit_depth": 0, "formats": "", "action": "convert",
         "codec": "flac", "sample_rate": 44100, "bit_depth": 16, "channels": 2, "bitrate_kbps": 0},
    ], "Rules: first matching rule decides. Edit them in the Convert tab")


@dataclass
class TrashCfg:
    """Recycle bin"""
    bin_dir: str = opt("", "Recycle-bin folder (empty = <data_dir>/bin). Put it on the same disk as your music for instant moves")
    retention_days: int = opt(0, "Automatically purge bin items older than this (0 = keep forever)")
    confirm_permanent: bool = opt(True, "Ask before permanent deletion")


@dataclass
class UiCfg:
    """Interface"""
    theme: str = opt("textual-dark", "Colour theme", ["textual-dark", "textual-light", "nord", "gruvbox", "tokyo-night", "dracula", "monokai"])
    show_tree: bool = opt(True, "Show the file-browser panel on the left")
    show_preview: bool = opt(True, "Show the info/preview panel on the right")
    thumbnail: bool = opt(True, "Show the cover thumbnail in the preview panel")
    thumbnail_width: int = opt(28, "Thumbnail width in terminal columns")
    show_json: bool = opt(False, "Show raw JSON of the selected item in the preview panel")
    show_log: bool = opt(True, "Show the activity log at the bottom")


SECTIONS = {
    "general": GeneralCfg, "scan": ScanCfg, "dedupe": DedupeCfg, "artwork": ArtworkCfg,
    "organize": OrganizeCfg, "convert": ConvertCfg, "trash": TrashCfg, "ui": UiCfg,
}


@dataclass
class Config:
    general: GeneralCfg = field(default_factory=GeneralCfg)
    scan: ScanCfg = field(default_factory=ScanCfg)
    dedupe: DedupeCfg = field(default_factory=DedupeCfg)
    artwork: ArtworkCfg = field(default_factory=ArtworkCfg)
    organize: OrganizeCfg = field(default_factory=OrganizeCfg)
    convert: ConvertCfg = field(default_factory=ConvertCfg)
    trash: TrashCfg = field(default_factory=TrashCfg)
    ui: UiCfg = field(default_factory=UiCfg)

    path: Path | None = field(default=None, repr=False, compare=False)

    # ---- paths -------------------------------------------------------
    def data_dir(self) -> Path:
        p = self.general.data_dir or os.environ.get("MUSICORG_HOME") or "~/.local/share/musicorg"
        d = Path(os.path.expanduser(p))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def sub_dir(self, name: str, override: str = "") -> Path:
        d = Path(os.path.expanduser(override)) if override else self.data_dir() / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- (de)serialisation ----------------------------------------------
    def to_dict(self) -> dict:
        out = {}
        for name in SECTIONS:
            sec = getattr(self, name)
            out[name] = {f.name: copy.deepcopy(getattr(sec, f.name)) for f in fields(sec)}
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        cfg = cls()
        for name, klass in SECTIONS.items():
            raw = data.get(name, {})
            sec = getattr(cfg, name)
            for f in fields(klass):
                if f.name in raw:
                    setattr(sec, f.name, _coerce(raw[f.name], getattr(sec, f.name)))
        return cfg

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else default_config_path()
        cfg = cls()
        if p.exists():
            with open(p, "rb") as fh:
                cfg = cls.from_dict(tomllib.load(fh))
        cfg.path = p
        return cfg

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path) if path else (self.path or default_config_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dumps(self.to_dict()) + "\n", encoding="utf-8")
        self.path = p
        return p


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "musicorg" / "config.toml"


def _coerce(value, current):
    """Convert a loaded value to the type of the default."""
    try:
        if isinstance(current, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(current, int):
            return int(value)
        if isinstance(current, float):
            return float(value)
        if isinstance(current, str):
            return str(value)
        if isinstance(current, list):
            return list(value) if isinstance(value, (list, tuple)) else [v.strip() for v in str(value).split(",") if v.strip()]
    except (TypeError, ValueError):
        return current
    return value


# ---------------------------------------------------------------- TOML writer
def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v)} to TOML")


def dumps(data: dict, prefix: str = "") -> str:
    lines: list[str] = []
    tables: list[tuple[str, Any]] = []
    for k, v in data.items():
        if isinstance(v, dict) or (isinstance(v, list) and v and all(isinstance(x, dict) for x in v)):
            tables.append((k, v))
        else:
            lines.append(f"{k} = {_val(v)}")
    out = "\n".join(lines)
    for k, v in tables:
        name = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out += f"\n\n[{name}]\n" + dumps(v, name)
        else:
            for item in v:
                out += f"\n\n[[{name}]]\n" + dumps(item, name)
    return out.strip("\n")


def section_fields(section_name: str):
    """Yield (field_name, default_type, help, choices) for the Settings UI."""
    klass = SECTIONS[section_name]
    for f in fields(klass):
        yield f.name, f.metadata.get("help", ""), f.metadata.get("choices")
