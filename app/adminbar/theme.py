"""The look shared by the bar and the config window: colours, fonts and a few small widgets."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, List, Optional, Sequence, Tuple

from .rules import Action

BG = "#131417"
BG_SIDE = "#0e0f12"
BG_ROW = "#1a1c20"
BG_SEL = "#212329"
BG_HOVER = "#1d1f24"
BG_FIELD = "#1c1e23"
BG_KEY = "#24262c"
BORDER = "#2c2f36"
FG = "#ecebe8"
FG2 = "#a9acb3"
FG3 = "#6d717a"
ACCENT = "#e9b44c"
ACCENT_BG = "#2b2517"
GOOD = "#6cc58a"
BAD = "#ef6f6c"

PILL = {
    "jail": ("#f3a463", "#2b2018"),
    "warn": ("#e8c766", "#29261a"),
    "block": ("#f07c78", "#2c1b1b"),
    "perm": ("#ff6461", "#361617"),
    "mute": ("#7fb6e8", "#18222c"),
    "manual": ("#9a9fa8", "#202227"),
}
STYLE_EXAMPLE = {"dotted": "G.R 6.2", "plain": "GR 6.2", "dotted_name": "G.R 6.2 RDM", "plain_name": "GR 6.2 RDM"}


def pill_key(action: Optional[Action]) -> str:
    if action is None or not action.sendable:
        return "manual"
    if action.kind == "block" and action.perm:
        return "perm"
    return action.kind


def pill_of(action: Optional[Action], more: bool = False) -> Tuple[str, str, str]:
    """('JAIL 60m', fg, bg) for the coloured tag next to a rule."""
    if action is None:
        return "INFO", *PILL["manual"]
    key = pill_key(action)
    if action.kind == "jail":
        t = f"JAIL {action.amount}m" if action.amount else "JAIL"
    elif action.kind == "warn":
        t = "WARN"
    elif key == "perm":
        t = "PERM BAN"
    elif action.kind == "block":
        t = (f"BAN ≤{action.amount}d" if action.max_amount else f"BAN {action.amount}d") if action.amount \
            else "BAN"
    elif action.kind == "mute":
        t = f"MUTE {action.amount}m" if action.amount else "MUTE"
    else:
        t = "NO CMD"
    return t + (" +" if more else ""), *PILL[key]


class Fonts:
    def __init__(self, root: tk.Misc) -> None:
        fams = set(tkfont.families(root))
        ui = "Segoe UI" if "Segoe UI" in fams else "TkDefaultFont"
        semi = "Segoe UI Semibold" if "Segoe UI Semibold" in fams else ui
        mono = next((f for f in ("Cascadia Mono", "Consolas", "Courier New") if f in fams), "TkFixedFont")
        F = tkfont.Font
        self.query = F(root, family=ui, size=15)
        self.row = F(root, family=ui, size=11)
        self.row_b = F(root, family=semi, size=11)
        self.small = F(root, family=ui, size=9)
        self.cap = F(root, family=semi, size=8)
        self.pill = F(root, family=semi, size=8)
        self.mono = F(root, family=mono, size=10)
        self.mono_s = F(root, family=mono, size=9)
        self.mono_l = F(root, family=mono, size=14)
        self.title = F(root, family=semi, size=13)
        self.h1 = F(root, family=semi, size=17)
        self.text = F(root, family=ui, size=10)
        self.btn = F(root, family=semi, size=9)


def fit_text(text: str, font: tkfont.Font, width: int) -> str:
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def label(parent, text="", font=None, fg=FG, bg=BG, **kw) -> tk.Label:
    return tk.Label(parent, text=text, font=font, fg=fg, bg=bg, bd=0, **kw)


def entry(parent, var, font, width, px: Callable[[float], int], bg=BG_FIELD) -> tk.Entry:
    return tk.Entry(parent, textvariable=var, font=font, width=width, bg=bg, fg=FG, bd=0, relief="flat",
                    insertbackground=ACCENT, insertwidth=px(2), selectbackground="#3b3e46", selectforeground=FG,
                    highlightthickness=px(1), highlightbackground=BORDER, highlightcolor=ACCENT,
                    disabledbackground=bg, disabledforeground=FG3)


class Button(tk.Label):
    """A flat text button: normal, primary (amber) or danger (red)."""

    STYLES = {
        "normal": (FG, BG_KEY, "#2c2f36"),
        "primary": ("#1a1407", ACCENT, "#f2c46a"),
        "danger": (BAD, "#2a1818", "#3a1d1d"),
        "ghost": (FG2, BG, BG_HOVER),
    }

    def __init__(self, parent, text: str, command: Callable[[], None], fonts: Fonts, px, kind: str = "normal",
                 **kw) -> None:
        fg, bg, hover = self.STYLES[kind]
        super().__init__(parent, text=text, font=fonts.btn, fg=fg, bg=bg, bd=0, padx=px(12), pady=px(6),
                         cursor="hand2", **kw)
        self._colors = (fg, bg, hover)
        self._command = command
        self._enabled = True
        self.bind("<Enter>", lambda e: self._enabled and self.configure(bg=hover))
        self._off_bg = BG if kind == "ghost" else BG_ROW
        self.bind("<Leave>", lambda e: self.configure(bg=self._colors[1] if self._enabled else self._off_bg))
        self.bind("<Button-1>", lambda e: self._enabled and self._command())

    def enable(self, on: bool) -> None:
        self._enabled = on
        fg, bg, _ = self._colors
        self.configure(fg=fg if on else FG3, bg=bg if on else self._off_bg, cursor="hand2" if on else "arrow")


class Toggle(tk.Canvas):
    """An on/off switch."""

    def __init__(self, parent, value: bool, on_change: Callable[[bool], None], px, bg=BG) -> None:
        self.w, self.h = px(38), px(20)
        super().__init__(parent, width=self.w, height=self.h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.value = value
        self.on_change = on_change
        self.bind("<Button-1>", lambda e: self.set(not self.value, notify=True))
        self._draw()

    def set(self, value: bool, notify: bool = False) -> None:
        self.value = value
        self._draw()
        if notify:
            self.on_change(value)

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.w, self.h
        r = h / 2
        fill = ACCENT if self.value else BG_KEY
        self.create_oval(0, 0, h, h, fill=fill, outline=fill)
        self.create_oval(w - h, 0, w, h, fill=fill, outline=fill)
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline=fill)
        k = h - 6
        x = w - k - 3 if self.value else 3
        self.create_oval(x, 3, x + k, 3 + k, fill="#1a1407" if self.value else FG2, outline="")


class Segmented(tk.Frame):
    """A row of choices, one of them picked."""

    def __init__(self, parent, options: Sequence[Tuple[str, str]], value: str, on_change: Callable[[str], None],
                 fonts: Fonts, px, bg=BG, colors: Optional[dict] = None, columns: int = 0) -> None:
        super().__init__(parent, bg=bg)
        self.on_change = on_change
        self.value = value
        self.colors = colors or {}
        self.items: List[Tuple[str, tk.Label]] = []
        for i, (val, text) in enumerate(options):
            lb = tk.Label(self, text=text, font=fonts.btn, bd=0, padx=px(11), pady=px(6), cursor="hand2",
                          highlightthickness=px(1))
            if columns:  # a grid of choices, every cell the same width
                lb.grid(row=i // columns, column=i % columns, sticky="ew", padx=(0, px(4)), pady=(0, px(4)))
            else:
                lb.pack(side="left", padx=(0, px(4)))
            lb.bind("<Button-1>", lambda e, v=val: self.set(v, notify=True))
            self.items.append((val, lb))
        self._paint()

    def set(self, value: str, notify: bool = False) -> None:
        changed = value != self.value
        self.value = value
        self._paint()
        if notify and changed:
            self.on_change(value)

    def _paint(self) -> None:
        for val, lb in self.items:
            on = val == self.value
            fg, bg = self.colors.get(val, (FG, ACCENT_BG))
            lb.configure(fg=fg if on else FG2, bg=bg if on else BG_ROW,
                         highlightbackground=(fg if on else BG_ROW), highlightcolor=(fg if on else BG_ROW))
