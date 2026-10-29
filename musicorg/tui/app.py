from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from textual.app import App
from textual.binding import Binding

from ..core import dedupe as dd
from ..core import scanner
from ..core.artwork import ArtworkResult
from ..core.config import Config
from ..core.models import Track
from ..core.trash import RecycleBin


@dataclass
class AppState:
    tracks: list[Track] = field(default_factory=list)
    by_path: dict[str, Track] = field(default_factory=dict)
    scan_errors: list[Track] = field(default_factory=list)
    dupes: dd.DedupeResult | None = None
    art: ArtworkResult | None = None
    org_plan: object = None
    sync_plan: object = None
    restore_plan: object = None

    def set_tracks(self, tracks: list[Track], errors: list[Track]) -> None:
        self.tracks, self.scan_errors = tracks, errors
        self.by_path = {t.path: t for t in tracks}
        self.dupes = self.art = self.org_plan = self.sync_plan = None


class MusicOrgApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "MusicOrg"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+x", "cancel_task", "Cancel task"),
        Binding("f2", "open_settings", "Settings"),
        Binding("f3", "open_bin", "Recycle bin"),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.state = AppState()
        self.busy = False
        self.cancel = threading.Event()
        self.progress_listeners: list[Callable[[int, int, str], None]] = []
        self.log_listeners: list[Callable[[str, str], None]] = []
        self.logfile: Path | None = None

    # ---------------------------------------------------------------- setup
    def on_mount(self) -> None:
        try:
            self.theme = self.cfg.ui.theme
        except Exception:
            pass
        try:
            self.logfile = self.cfg.data_dir() / "musicorg.log"
            RecycleBin(self.cfg).purge_older_than(self.cfg.trash.retention_days)
        except Exception:
            pass
        from .home import HomeScreen
        self.push_screen(HomeScreen())

    def bin(self) -> RecycleBin:
        return RecycleBin(self.cfg)

    def save_cfg(self) -> None:
        try:
            self.cfg.save()
        except Exception as e:
            self.log_line(f"could not save config: {e}", "error")

    # -------------------------------------------------------------- logging
    def log_line(self, msg: str, level: str = "info") -> None:
        stamp = time.strftime("%H:%M:%S")
        if self.logfile:
            try:
                with open(self.logfile, "a", encoding="utf-8") as fh:
                    fh.write(f"{stamp} [{level}] {msg}\n")
            except OSError:
                pass
        for cb in list(self.log_listeners):
            cb(msg, level)

    # ---------------------------------------------------- background tasks
    def bg(self, fn: Callable, *, label: str, done: Callable | None = None) -> bool:
        """Run ``fn(progress, cancel)`` in a thread. ``done(result)`` runs on the UI thread."""
        if self.busy:
            self.notify("Another task is still running (Ctrl+X cancels it)", severity="warning")
            return False
        self.busy, self.cancel = True, threading.Event()
        cancel = self.cancel
        self.log_line(f"▶ {label}")
        self._progress(0, 0, label)

        def prog(d: int, t: int, msg: str = "") -> None:
            self.call_from_thread(self._progress, d, t, msg)

        def runner():
            try:
                result = fn(prog, cancel)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(self._bg_fail, label, e)
            else:
                self.call_from_thread(self._bg_done, label, result, done)

        self.run_worker(runner, thread=True, exit_on_error=False)
        return True

    def _progress(self, d: int, t: int, msg: str) -> None:
        for cb in list(self.progress_listeners):
            cb(d, t, msg)

    def _bg_done(self, label: str, result, done) -> None:
        self.busy = False
        self._progress(0, 0, "")
        self.log_line(f"✔ {label}" if not self.cancel.is_set() else f"■ {label} (cancelled)")
        if done:
            done(result)

    def _bg_fail(self, label: str, e: Exception) -> None:
        self.busy = False
        self._progress(0, 0, "")
        self.log_line(f"✖ {label}: {type(e).__name__}: {e}", "error")
        self.notify(f"{label} failed: {e}", severity="error", timeout=8)

    def action_cancel_task(self) -> None:
        if self.busy:
            self.cancel.set()
            self.log_line("cancelling…", "warn")

    # ------------------------------------------------------------- scanning
    def scan_library(self, use_cache: bool = True, done: Callable | None = None) -> None:
        if not self.cfg.scan.roots:
            self.notify("Add at least one music folder first", severity="warning")
            return
        saved = self.cfg.scan.use_cache
        self.cfg.scan.use_cache = use_cache and saved

        def job(prog, cancel):
            try:
                return scanner.scan(self.cfg, progress=prog, cancel=cancel)
            finally:
                self.cfg.scan.use_cache = saved

        def finished(res):
            if not res.cancelled:
                self.state.set_tracks(res.tracks, res.errors)
            self.log_line(f"scan: {len(res.tracks)} tracks ({res.cached} cached, {res.read} read, {len(res.errors)} unreadable)")
            self.state_changed()
            if done and not res.cancelled:
                done()

        self.bg(job, label="Scanning library", done=finished)

    def state_changed(self) -> None:
        for scr in list(self.screen_stack):
            cb = getattr(scr, "on_state_changed", None)
            if cb:
                cb()

    # ------------------------------------------------------------ navigation
    def action_open_settings(self) -> None:
        from .settings import SettingsScreen
        if not isinstance(self.screen, SettingsScreen):
            self.push_screen(SettingsScreen())

    def action_open_bin(self) -> None:
        from .binscreen import BinScreen
        if not isinstance(self.screen, BinScreen):
            self.push_screen(BinScreen())
