import argparse
import os

from . import __version__
from .core.config import Config


def main() -> None:
    ap = argparse.ArgumentParser(prog="musicorg", description="Music library organizer (TUI)")
    ap.add_argument("--config", help="path to config.toml (default ~/.config/musicorg/config.toml)")
    ap.add_argument("--data-dir", help="override data directory (index, bin, journals)")
    ap.add_argument("--root", action="append", default=[], help="add a music folder for this session (repeatable)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()
    cfg = Config.load(a.config)
    if a.data_dir:
        cfg.general.data_dir = a.data_dir
    for r in a.root:
        r = os.path.abspath(os.path.expanduser(r))
        if r not in cfg.scan.roots:
            cfg.scan.roots.append(r)
    from .tui.app import MusicOrgApp
    MusicOrgApp(cfg).run()


if __name__ == "__main__":
    main()
