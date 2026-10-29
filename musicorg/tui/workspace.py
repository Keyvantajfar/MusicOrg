from __future__ import annotations

import io
import json
import os

from rich.console import Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (DataTable, DirectoryTree, Footer, Input, ProgressBar, RichLog, Select, Static, Switch,
                             TabbedContent, TabPane)

from ..core import tags
from ..core.config import _coerce
from ..core.util import short_size
from .home import WORKFLOWS

PANE_TITLES = {"library": "① Library", "duplicates": "② Duplicates", "artwork": "③ Artwork", "organize": "④ Organize",
               "sync": "⑤ Convert & Sync", "snapshot": "⑥ Snapshot", "reports": "⑦ Reports"}
NEEDS_LIBRARY = {"duplicates", "artwork", "organize", "sync", "reports"}


class MusicTree(DirectoryTree):
    def filter_paths(self, paths):
        exts = {"." + e.lower().lstrip(".") for e in self.app.cfg.scan.formats}
        return [p for p in paths if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() in exts)]


class InfoPanel(VerticalScroll):
    """Live metadata / cover thumbnail / JSON of whatever is selected anywhere."""

    def compose(self) -> ComposeResult:
        yield Static("[dim]Select a file or row to see its details here.[/dim]", id="info-body")

    def show(self, path: str) -> None:
        self._load(path)

    @work(thread=True, exclusive=True)
    def _load(self, path: str) -> None:
        app = self.app
        ui = app.cfg.ui
        if os.path.isdir(path):
            tracks = [t for t in app.state.tracks if t.path.startswith(path.rstrip("/") + "/")]
            tb = Table.grid(padding=(0, 1))
            tb.add_row("[b]📁[/b]", Text(os.path.basename(path) or path, style="bold"))
            tb.add_row("path", Text(path, style="dim"))
            if tracks:
                tb.add_row("tracks", str(len(tracks)))
                tb.add_row("size", short_size(sum(t.size for t in tracks)))
            app.call_from_thread(self._set, tb)
            return
        if not os.path.isfile(path):
            return
        t = app.state.by_path.get(path) or tags.read_track(path)
        tb = Table.grid(padding=(0, 1))
        tb.add_column(style="dim", justify="right")
        tb.add_column(overflow="fold")
        tb.add_row("", Text(t.name, style="bold"))
        for k, v in (("title", t.title), ("artist", t.artist), ("album", t.album), ("album artist", t.albumartist),
                     ("track/disc", f"{t.track or '-'} / {t.disc or '-'}"), ("year", t.year), ("genre", t.genre),
                     ("format", f"{t.fmt}{' (lossless)' if t.lossless else ''}"),
                     ("quality", f"{t.bit_depth or '?'} bit · {t.sample_rate / 1000:g} kHz · {t.channels} ch · {t.bitrate} kbps"),
                     ("duration", f"{int(t.duration // 60)}:{int(t.duration % 60):02d}"),
                     ("size", short_size(t.size)),
                     ("cover", f"{t.pic_w}×{t.pic_h}" if t.has_picture else "[red]none[/red]")):
            tb.add_row(k, Text.from_markup(str(v)) if v not in ("", None) else Text("—", style="dim"))
        if t.error:
            tb.add_row("error", Text(t.error, style="red"))
        parts = []
        if ui.thumbnail:
            thumb = self._thumb(path, ui.thumbnail_width)
            if thumb is not None:
                parts.append(thumb)
        parts.append(tb)
        if ui.show_json:
            parts.append(Text("\nlibrary record", style="dim"))
            parts.append(Syntax(json.dumps(t.to_dict(), indent=1, ensure_ascii=False), "json", theme="ansi_dark", word_wrap=True))
            parts.append(Text("\nraw tags", style="dim"))
            parts.append(Syntax(json.dumps(tags.raw_tags(path), indent=1, ensure_ascii=False), "json", theme="ansi_dark", word_wrap=True))
        app.call_from_thread(self._set, Group(*parts))

    @staticmethod
    def _thumb(path: str, width: int):
        try:
            from PIL import Image
            from rich_pixels import Pixels
            cov = tags.read_cover(path)
            if cov is None:
                return Text("\n   ( no cover art )\n", style="dim italic")
            im = Image.open(io.BytesIO(cov[0])).convert("RGB")
            w, h = im.size
            im = im.resize((width, max(2, int(width * h / w) // 2 * 2)))
            return Pixels.from_image(im)
        except Exception:
            return None

    def _set(self, renderable) -> None:
        self.query_one("#info-body", Static).update(renderable)


class StatusBar(Horizontal):
    def compose(self) -> ComposeResult:
        yield Static("", id="st-left")
        yield Static("", id="st-mid")
        yield ProgressBar(id="st-bar", show_eta=False, show_percentage=False)
        yield Static("", id="st-right")


class WorkspaceScreen(Screen):
    BINDINGS = [
        Binding("escape", "home", "Home"),
        Binding("ctrl+t", "toggle('tree')", "Tree"),
        Binding("ctrl+i", "toggle('info')", "Preview"),
        Binding("ctrl+l", "toggle('log')", "Log"),
        Binding("ctrl+d", "toggle_dry", "Dry-run"),
        Binding("alt+right", "tab(1)", "Next tab"),
        Binding("alt+left", "tab(-1)", "Prev tab"),
    ]

    def __init__(self, workflow: str):
        super().__init__()
        self.workflow = workflow
        self.tabs = WORKFLOWS[workflow][3]

    # ------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        from . import panes
        title = WORKFLOWS[self.workflow][0]
        with Horizontal(id="topbar"):
            yield Static(f"[b]♫ MusicOrg[/b]  ›  [b]{title}[/b]", id="top-left")
            yield Static("", id="top-right")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Static("FILES", classes="panel-title")
                root = (self.app.cfg.scan.roots or [os.path.expanduser("~")])[0]
                yield MusicTree(os.path.expanduser(root), id="tree")
            with Vertical(id="center"):
                with TabbedContent(id="tabs"):
                    for tid in self.tabs:
                        with TabPane(PANE_TITLES[tid], id=tid):
                            yield getattr(panes, tid.capitalize() + "Pane")(id=f"pane-{tid}")
            with Vertical(id="right"):
                yield Static("DETAILS", classes="panel-title")
                yield InfoPanel(id="info")
        yield RichLog(id="log", markup=True, wrap=True, max_lines=2000)
        yield StatusBar(id="status")
        yield Footer()

    def on_mount(self) -> None:
        app = self.app
        app.progress_listeners.append(self._on_progress)
        app.log_listeners.append(self._on_log)
        for sec in ("tree", "info", "log"):
            self._apply_visibility(sec)
        self.query_one("#st-bar", ProgressBar).display = False
        self.on_state_changed()
        self._top()

    def on_unmount(self) -> None:
        app = self.app
        for lst, cb in ((app.progress_listeners, self._on_progress), (app.log_listeners, self._on_log)):
            if cb in lst:
                lst.remove(cb)
        app.save_cfg()

    # ---------------------------------------------------------- visibility
    def _apply_visibility(self, sec: str) -> None:
        ui = self.app.cfg.ui
        flag = {"tree": ui.show_tree, "info": ui.show_preview, "log": ui.show_log}[sec]
        target = {"tree": "#left", "info": "#right", "log": "#log"}[sec]
        self.query_one(target).display = flag

    def action_toggle(self, sec: str) -> None:
        ui = self.app.cfg.ui
        attr = {"tree": "show_tree", "info": "show_preview", "log": "show_log"}[sec]
        setattr(ui, attr, not getattr(ui, attr))
        self._apply_visibility(sec)

    def goto(self, tab_id: str) -> None:
        """Switch tab (clears focus first: TabbedContent re-activates the tab of the focused widget)."""
        self.set_focus(None)
        self.query_one("#tabs", TabbedContent).active = tab_id

    def action_tab(self, step: int) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        order = [t for t in self.tabs if not tabs.get_tab(t).disabled]
        if tabs.active in order:
            self.goto(order[(order.index(tabs.active) + step) % len(order)])

    def action_toggle_dry(self) -> None:
        g = self.app.cfg.general
        g.dry_run = not g.dry_run
        self._top()
        self.app.notify("DRY-RUN on: applying only simulates" if g.dry_run else "DRY-RUN off: changes are real", timeout=3)

    def action_home(self) -> None:
        if not self.app.busy:
            self.app.pop_screen()

    # -------------------------------------------------------------- status
    def _top(self) -> None:
        cfg = self.app.cfg
        dry = "[b black on yellow] DRY-RUN [/]" if cfg.general.dry_run else "[b white on green] LIVE [/]"
        self.query_one("#top-right", Static).update(f"{dry}  [dim]theme[/dim] {cfg.ui.theme}  [dim]F2[/dim] settings  [dim]F3[/dim] bin")

    def on_state_changed(self) -> None:
        st = self.app.state
        tabs = self.query_one("#tabs", TabbedContent)
        have = bool(st.tracks)
        for tid in self.tabs:
            if tid in NEEDS_LIBRARY:
                (tabs.enable_tab if have else tabs.disable_tab)(tid)
        total = sum(t.size for t in st.tracks)
        dup = f" · [yellow]{st.dupes.loser_count} duplicates[/yellow]" if st.dupes else ""
        self.query_one("#st-left", Static).update(
            f"♫ [b]{len(st.tracks):,}[/b] tracks · {short_size(total)}{dup}" if have else "[dim]library not scanned yet[/dim]")
        try:
            from ..core.journal import pending_previews
            pend = len(pending_previews(self.app.cfg))
            b = self.app.bin()
            right = f"🗑 {len(b.list())} in bin ({short_size(b.total_size())})" + (f" · [cyan]{pend} preview(s) pending[/cyan]" if pend else "")
        except Exception:
            right = ""
        self.query_one("#st-right", Static).update(right)
        self._top()
        for pane in self.query(".pane"):
            fn = getattr(pane, "refresh_state", None)
            if fn:
                fn()
        tree = self.query_one("#tree", MusicTree)
        if self.app.cfg.scan.roots:
            want = os.path.expanduser(self.app.cfg.scan.roots[0])
            if str(tree.path) != want and os.path.isdir(want):
                tree.path = want

    def _on_progress(self, d: int, t: int, msg: str) -> None:
        bar = self.query_one("#st-bar", ProgressBar)
        mid = self.query_one("#st-mid", Static)
        if not self.app.busy:
            bar.display = False
            mid.update("")
            return
        bar.display = True
        bar.update(total=t or None, progress=d)
        mid.update(f"[b]{msg[:36]}[/b] {d}/{t}  [dim]Ctrl+X cancel[/dim]" if t else f"[b]{msg[:40]}[/b]  [dim]Ctrl+X cancel[/dim]")

    def _on_log(self, msg: str, level: str) -> None:
        color = {"error": "red", "warn": "yellow"}.get(level, "")
        self.query_one("#log", RichLog).write(Text(msg, style=color))

    # ------------------------------------------- selection -> detail panel
    def _show(self, path) -> None:
        if path and self.app.cfg.ui.show_preview and os.path.exists(str(path)):
            self.query_one("#info", InfoPanel).show(str(path))

    def on_tree_node_highlighted(self, event) -> None:
        data = getattr(event.node, "data", None)
        self._show(getattr(data, "path", None))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key else None
        if key and os.sep in str(key):
            self._show(key)

    # ------------------------------------- generic config-bound widgets
    def _bind(self, widget_id: str | None, value) -> None:
        if not widget_id or not widget_id.startswith("cfg-"):
            return
        sec, _, name = widget_id[4:].partition("__")
        section = getattr(self.app.cfg, sec, None)
        if section is None or not hasattr(section, name):
            return
        cur = getattr(section, name)
        new = _coerce(value, cur)
        if new != cur:
            setattr(section, name, new)

    def on_input_changed(self, e: Input.Changed) -> None:
        self._bind(e.input.id, e.value)

    def on_select_changed(self, e: Select.Changed) -> None:
        if e.value is not Select.NULL:
            self._bind(e.select.id, e.value)

    def on_switch_changed(self, e: Switch.Changed) -> None:
        self._bind(e.switch.id, e.value)
