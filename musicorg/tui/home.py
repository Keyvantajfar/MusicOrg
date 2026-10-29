from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Grid, Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Static

BANNER = r"""
 ███╗   ███╗██╗   ██╗███████╗██╗ ██████╗  ██████╗ ██████╗  ██████╗
 ████╗ ████║██║   ██║██╔════╝██║██╔════╝ ██╔═══██╗██╔══██╗██╔════╝
 ██╔████╔██║██║   ██║███████╗██║██║      ██║   ██║██████╔╝██║  ███╗
 ██║╚██╔╝██║██║   ██║╚════██║██║██║      ██║   ██║██╔══██╗██║   ██║
 ██║ ╚═╝ ██║╚██████╔╝███████║██║╚██████╗ ╚██████╔╝██║  ██║╚██████╔╝
 ╚═╝     ╚═╝ ╚═════╝ ╚══════╝╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝
"""

# workflow id -> (title, glyph art, description, tabs in pipeline order)
WORKFLOWS = {
    "all": ("FULL PIPELINE", "♫ ♪ ♫",
            "Every step, in order:\nscan → duplicates → artwork\n→ organize → device sync", 
            ["library", "duplicates", "artwork", "organize", "sync", "snapshot", "reports"]),
    "cleanup": ("CLEAN UP", "⧉ ⧉ ⧉",
                "Find duplicates, keep the best\nquality, label unknown files,\nrepair missing cover art",
                ["library", "duplicates", "artwork", "reports"]),
    "organize": ("ORGANIZE", "▤ ▸ ▤",
                 "Artist / Album folders from a\ntemplate, safe link-preview mode,\nsnapshots & restore",
                 ["library", "organize", "snapshot", "reports"]),
    "device": ("DEVICE SYNC", "▣ ⇄ ▣",
               "Rules for big files, convert for\nyour iPod / phone, incremental\nsync, work on device or local",
               ["library", "sync", "snapshot", "reports"]),
}


class Tile(Static, can_focus=True):
    BINDINGS = [Binding("enter", "choose", "Open")]

    class Chosen(Message):
        def __init__(self, tile_id: str):
            super().__init__()
            self.tile_id = tile_id

    def __init__(self, wid: str, key: str):
        title, art, desc, _tabs = WORKFLOWS[wid]
        super().__init__(f"\n[b]{art}[/b]\n\n[b]{title}[/b]  [dim]\\[{key}][/dim]\n[dim]{desc}[/dim]", id=f"tile-{wid}", classes="tile")
        self.wid = wid

    def on_click(self) -> None:
        self.action_choose()

    def action_choose(self) -> None:
        self.post_message(self.Chosen(self.wid))


class HomeScreen(Screen):
    BINDINGS = [
        Binding("1", "open('all')", "Full pipeline"), Binding("2", "open('cleanup')", "Clean up"),
        Binding("3", "open('organize')", "Organize"), Binding("4", "open('device')", "Device sync"),
        Binding("s", "app.open_settings", "Settings"), Binding("b", "app.open_bin", "Recycle bin"),
        Binding("q", "app.quit", "Quit"),
        Binding("right,down", "next", show=False), Binding("left,up", "prev", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="home"):
            yield Static(BANNER, id="banner")
            yield Static("clean · de-duplicate · organize · sync your music", id="tagline")
            with Center():
                with Grid(id="tiles"):
                    for i, wid in enumerate(WORKFLOWS, 1):
                        yield Tile(wid, str(i))
            with Center():
                with Horizontal(id="home-foot"):
                    yield Static("[b]\\[S][/b] Settings", classes="foot-link", id="foot-settings")
                    yield Static("[b]\\[B][/b] Recycle bin", classes="foot-link", id="foot-bin")
                    yield Static("[b]\\[Q][/b] Quit", classes="foot-link", id="foot-quit")
            yield Static("", id="home-info")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#tile-all").focus()
        self.on_state_changed()

    def on_state_changed(self) -> None:
        pass

    def on_screen_resume(self) -> None:
        cfg = self.app.cfg
        try:
            n_bin = len(self.app.bin().list())
        except Exception:
            n_bin = 0
        roots = ", ".join(cfg.scan.roots) or "no folders yet — add them in the first tab"
        self.query_one("#home-info", Static).update(
            f"[dim]library:[/dim] {roots}    [dim]bin:[/dim] {n_bin} items    "
            f"[dim]mode:[/dim] {'[b red]DRY-RUN[/b red]' if cfg.general.dry_run else 'live'}")

    def on_click(self, event) -> None:
        wid = getattr(event.widget, "id", "") or ""
        if wid == "foot-settings":
            self.app.action_open_settings()
        elif wid == "foot-bin":
            self.app.action_open_bin()
        elif wid == "foot-quit":
            self.app.exit()

    def on_tile_chosen(self, msg: Tile.Chosen) -> None:
        self.action_open(msg.tile_id)

    def action_open(self, wid: str) -> None:
        from .workspace import WorkspaceScreen
        self.app.push_screen(WorkspaceScreen(wid))

    def action_next(self) -> None:
        self.focus_next()

    def action_prev(self) -> None:
        self.focus_previous()
