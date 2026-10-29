"""Settings screen: generated from the config dataclasses, so EVERY option is editable."""
from __future__ import annotations

from dataclasses import fields

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, ContentSwitcher, Footer, Input, Label, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from ..core.config import SECTIONS, Config, _coerce

TITLES = {"general": "General", "scan": "Library scan", "dedupe": "Duplicates", "artwork": "Artwork", "organize": "Organize",
          "convert": "Convert & Sync", "trash": "Recycle bin", "ui": "Interface"}


class SettingsScreen(Screen):
    BINDINGS = [Binding("escape", "close", "Back"), Binding("ctrl+s", "save", "Save")]

    def compose(self) -> ComposeResult:
        cfg = self.app.cfg
        yield Static(f"[b]⚙ Settings[/b]   [dim]{cfg.path or 'default location'}[/dim]", id="set-top")
        with Horizontal(id="set-body"):
            yield OptionList(*[Option(TITLES[s], id=s) for s in SECTIONS], id="set-nav")
            with ContentSwitcher(initial="sec-general", id="set-switch"):
                for sec, klass in SECTIONS.items():
                    with VerticalScroll(id=f"sec-{sec}", classes="set-sec"):
                        yield Static(f"[b]{TITLES[sec]}[/b]", classes="h")
                        for f in fields(klass):
                            if f.name == "rules":
                                yield Static("[b]rules[/b]  [dim]— edit them in the Convert & Sync tab[/dim]", classes="set-help")
                                continue
                            val = getattr(getattr(cfg, sec), f.name)
                            yield Static(f"[b]{f.name}[/b]  [dim]{f.metadata.get('help', '')}[/dim]", classes="set-help")
                            wid = f"set-{sec}__{f.name}"
                            ch = f.metadata.get("choices")
                            if isinstance(val, bool):
                                yield Switch(value=val, id=wid)
                            elif ch:
                                yield Select([(c, c) for c in ch], value=val if val in ch else ch[0], allow_blank=False, id=wid)
                            else:
                                yield Input(value=", ".join(map(str, val)) if isinstance(val, list) else str(val), id=wid)
        with Horizontal(id="set-buttons"):
            yield Button("Save", id="save", variant="primary")
            yield Button("Reset this section to defaults", id="reset")
            yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#set-nav", OptionList).highlighted = 0
        self.current = "general"

    def on_option_list_option_highlighted(self, e: OptionList.OptionHighlighted) -> None:
        self.current = e.option.id
        self.query_one("#set-switch", ContentSwitcher).current = f"sec-{e.option.id}"

    def _collect(self) -> None:
        cfg = self.app.cfg
        for sec, klass in SECTIONS.items():
            section = getattr(cfg, sec)
            for f in fields(klass):
                if f.name == "rules":
                    continue
                w = self.query_one(f"#set-{sec}__{f.name}")
                v = w.value
                if v is Select.NULL:
                    continue
                setattr(section, f.name, _coerce(v, getattr(section, f.name)))
        try:
            self.app.theme = cfg.ui.theme
        except Exception:
            pass

    def action_save(self) -> None:
        self._collect()
        self.app.save_cfg()
        self.app.notify("Settings saved")
        self.app.state_changed()

    def action_close(self) -> None:
        self.app.pop_screen()
        self.app.state_changed()

    def on_button_pressed(self, e: Button.Pressed) -> None:
        if e.button.id == "save":
            self.action_save()
        elif e.button.id == "back":
            self.action_close()
        elif e.button.id == "reset":
            sec, klass = self.current, SECTIONS[self.current]
            default = klass()
            for f in fields(klass):
                if f.name == "rules":
                    continue
                w = self.query_one(f"#set-{sec}__{f.name}")
                v = getattr(default, f.name)
                if isinstance(w, Switch):
                    w.value = v
                elif isinstance(w, Select):
                    w.value = v
                else:
                    w.value = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
            self.app.notify(f"{TITLES[sec]} reset (press Save to keep)")
