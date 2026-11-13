"""The config window: a normal app window (taskbar, title bar) in the bar's look. Every change is saved at
once and the bar uses it straight away.

    Home         what is running, how to use it, where to find it, start / stop
    Punishments  find any rule, change its default punishment (jail / warn / ban / perm / mute / by hand) and
                 its time in two clicks, or put it back to the rulebook's
    Quick list   the rules on the empty bar and on Shift+1-9
    Log          every punishment the bar typed into the game
    Shortcuts    open / hide shortcuts, the game's chat key, the bar's own keys
    Commands     live or test, the command formats (/jail ...), flags, permanent-ban days, reason format
    Sending      typing method and timings, mouse, focus, confirmations
    General      start with Windows, data folder, quit, reset
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import tkinter as tk
from pathlib import Path
from typing import Callable, List, Optional

from . import APP_NAME, VERSION, logbook, winapi
from .hotkeys import parse_hotkey
from .rules import DEFAULT_TEMPLATES, Rule, check_template, finalize, format_command, make_action
from .settings import DEFAULT_FAVORITES, Settings, autostart_enabled, data_dir, set_autostart
from .theme import (ACCENT, ACCENT_BG, BAD, BG, BG_FIELD, BG_HOVER, BG_KEY, BG_ROW, BG_SEL, BG_SIDE, BORDER, FG,
                    FG2, FG3, GOOD, PILL, STYLE_EXAMPLE, Button, Segmented, Toggle, entry, fit_text, label, pill_of)

log = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parent / "data"
ICON = DATA / "icon.ico"
PAGES = [
    ("home", "Home", "Admin Bar runs in the background while you play."),
    ("punish", "Punishments", "Change the default punishment of any rule - the bar uses it straight away."),
    ("quick", "Quick list", "The rules on the empty bar. Shift + their number punishes right away."),
    ("log", "Log", "Every punishment the bar typed into the game, newest first."),
    ("shortcuts", "Shortcuts", "Keys that open and hide the bar, and the game's chat key."),
    ("commands", "Commands", "What gets typed into the game chat."),
    ("sending", "Sending", "How the bar talks to the game."),
    ("general", "General", "Start-up, files and the app itself."),
]
KIND_OPTIONS = [("jail", "Jail"), ("warn", "Warn"), ("block", "Ban"), ("perm", "Perm ban"), ("mute", "Mute"),
                ("manual", "By hand")]
KIND_COLORS = {"jail": PILL["jail"], "warn": PILL["warn"], "block": PILL["block"], "perm": PILL["perm"],
               "mute": PILL["mute"], "manual": PILL["manual"]}
PRESETS = {"jail": [10, 15, 20, 30, 45, 60, 90, 120], "mute": [10, 15, 30, 45, 60, 120, 180],
           "block": [1, 3, 7, 14, 30, 60, 90]}
SUGGEST = {"jail": 30, "mute": 30, "block": 7}
FILTERS = [("all", "All"), ("changed", "Changed"), ("jail", "Jail"), ("warn", "Warn"), ("block", "Ban"),
           ("mute", "Mute"), ("manual", "By hand")]
LIST_ROWS = 14
LOG_COLS = (160, 76, 360, 150, 60)  # time, player, rule, punishment, mode (pixels at 100 %)
KEYSYM_NAMES = {
    "space": "Space", "Tab": "Tab", "Insert": "Insert", "Delete": "Delete", "Home": "Home", "End": "End",
    "Prior": "PageUp", "Next": "PageDown", "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
    "Pause": "Pause", "Scroll_Lock": "ScrollLock", "grave": "`", "quoteleft": "`", "asciitilde": "`",
    "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]", "backslash": "\\", "semicolon": ";",
    "apostrophe": "'", "comma": ",", "period": ".", "slash": "/",
    **{f"F{n}": f"F{n}" for n in range(1, 25)},
    **{f"KP_{d}": f"Num{d}" for d in range(10)},
}
MODIFIER_KEYSYMS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Win_L", "Win_R",
                    "Meta_L", "Meta_R", "Caps_Lock"}


def rule_kind(rule: Rule) -> str:
    a = rule.default_action
    if a is None or not a.sendable:
        return "manual"
    return "perm" if a.perm else a.kind


class ConfigWindow:
    def __init__(self, app) -> None:
        self.app = app
        self.s: Settings = app.s
        self.px = app.px
        self.f = app.f
        self.capturing: Optional[str] = None
        self.page_name = "home"
        self.images = {}
        self._jobs: dict = {}
        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.title(APP_NAME)
        w.configure(bg=BG)
        try:
            w.iconbitmap(str(ICON))
        except tk.TclError:
            pass
        W, H = self.px(1100), self.px(700)
        left, top, right, bottom = winapi.work_area_for(0)
        W, H = min(W, right - left - 40), min(H, bottom - top - 40)
        w.geometry(f"{W}x{H}+{left + (right - left - W) // 2}+{top + (bottom - top - H) // 2}")
        w.minsize(min(self.px(900), W), min(self.px(600), H))
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Control-w>", lambda e: self.close())
        self._build()
        w.update_idletasks()
        self.hwnd = winapi.toplevel_of(w.winfo_id())
        winapi.add_ex_style(self.hwnd, winapi.WS_EX_APPWINDOW, winapi.WS_EX_TOOLWINDOW)
        dark_title_bar(self.hwnd)

    # ------------------------------------------------------------------ window
    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def show(self, page: Optional[str] = None) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        winapi.bring_to_front(self.hwnd, settle=0.08)
        if page and page != self.page_name:
            self.go(page)

    def close(self) -> None:
        if self.capturing:
            self._end_capture(None)
        for job in self._jobs.values():
            try:
                self.win.after_cancel(job)
            except tk.TclError:
                pass
        self.win.destroy()

    def changed(self, rebuild_list: bool = True) -> None:
        self.app.settings_changed(rebuild_list)
        self._side_status()

    def later(self, key: str, ms: int, fn: Callable[[], None]) -> None:
        """Debounce: run fn once typing pauses."""
        if key in self._jobs:
            self.win.after_cancel(self._jobs[key])
        self._jobs[key] = self.win.after(ms, lambda: (self._jobs.pop(key, None), fn()))

    def game_changed(self) -> None:
        self._side_status()

    # ------------------------------------------------------------------ frame
    def _build(self) -> None:
        px = self.px
        side = tk.Frame(self.win, bg=BG_SIDE, width=px(214))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=BG_SIDE)
        brand.pack(fill="x", padx=px(20), pady=(px(22), px(26)))
        logo = self.logo(36)
        if logo is not None:
            label(brand, image=logo, bg=BG_SIDE).pack(side="left")
        t = tk.Frame(brand, bg=BG_SIDE)
        t.pack(side="left", padx=(px(11), 0))
        label(t, APP_NAME, self.f.title, bg=BG_SIDE).pack(anchor="w")
        label(t, f"version {VERSION}", self.f.small, fg=FG3, bg=BG_SIDE).pack(anchor="w")
        self.nav = {}
        for key, title, _ in PAGES:
            row = tk.Frame(side, bg=BG_SIDE, cursor="hand2")
            row.pack(fill="x")
            bar = tk.Frame(row, bg=BG_SIDE, width=px(3))
            bar.pack(side="left", fill="y")
            lb = label(row, title, self.f.row, fg=FG2, bg=BG_SIDE, anchor="w", padx=px(18), pady=px(9))
            lb.pack(side="left", fill="x", expand=True)
            for w in (row, lb):
                w.bind("<Button-1>", lambda e, k=key: self.go(k))
                w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self.nav[key] = (row, bar, lb)
        label(side, "Made by King Specter\nDiscord: 9king", self.f.small, fg=FG3, bg=BG_SIDE,
              justify="left").pack(side="bottom", anchor="w", padx=px(20), pady=(0, px(16)))
        status = tk.Frame(side, bg=BG_SIDE)
        status.pack(side="bottom", fill="x", padx=px(20), pady=px(20))
        g = tk.Frame(status, bg=BG_SIDE)
        g.pack(anchor="w")
        self.s_dot = tk.Canvas(g, width=px(8), height=px(8), bg=BG_SIDE, highlightthickness=0, bd=0)
        self.s_dot_id = self.s_dot.create_oval(0, 0, px(7), px(7), fill=FG3, outline="")
        self.s_dot.pack(side="left", padx=(0, px(7)))
        self.s_game = label(g, "", self.f.small, fg=FG2, bg=BG_SIDE)
        self.s_game.pack(side="left")
        self.s_mode = label(status, "", self.f.pill, padx=px(7), pady=px(2))
        self.s_mode.pack(anchor="w", pady=(px(8), 0))
        self.s_keys = label(status, "", self.f.small, fg=FG3, bg=BG_SIDE, justify="left")
        self.s_keys.pack(anchor="w", pady=(px(8), 0))
        tk.Frame(self.win, bg=BORDER, width=px(1)).pack(side="left", fill="y")
        main = tk.Frame(self.win, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        head = tk.Frame(main, bg=BG)
        head.pack(fill="x", padx=px(30), pady=(px(24), px(4)))
        self.h_title = label(head, "", self.f.h1)
        self.h_title.pack(anchor="w")
        self.h_sub = label(head, "", self.f.text, fg=FG3)
        self.h_sub.pack(anchor="w", pady=(px(3), 0))
        self.page = tk.Frame(main, bg=BG)
        self.page.pack(fill="both", expand=True, padx=px(30), pady=(px(14), px(20)))
        self._side_status()
        self.go(self.page_name)

    def _nav_hover(self, key: str, on: bool) -> None:
        if key == self.page_name:
            return
        row, bar, lb = self.nav[key]
        bg = BG_HOVER if on else BG_SIDE
        for w in (row, bar, lb):
            w.configure(bg=bg)

    def _side_status(self) -> None:
        if not self.alive():
            return
        g = self.app.tracker.game
        self.s_dot.itemconfigure(self.s_dot_id, fill=GOOD if g else FG3)
        self.s_game.configure(text=f"{g.label} found" if g else "Game not running", fg=FG2 if g else FG3)
        if self.s.test_mode:
            self.s_mode.configure(text="TEST MODE  ·  NO /", fg=ACCENT, bg=ACCENT_BG)
        else:
            self.s_mode.configure(text="LIVE  ·  SENDS /COMMANDS", fg=GOOD, bg="#17261c")
        self.s_keys.configure(text=f"{self.s.open_hotkey}  opens the bar\n{self.s.hide_hotkey}  hides it")

    def go(self, key: str) -> None:
        if self.capturing:
            self._end_capture(None)
        self.page_name = key
        for k, (row, bar, lb) in self.nav.items():
            on = k == key
            row.configure(bg=BG_SEL if on else BG_SIDE)
            bar.configure(bg=ACCENT if on else BG_SIDE)
            lb.configure(bg=BG_SEL if on else BG_SIDE, fg=FG if on else FG2,
                         font=self.f.row_b if on else self.f.row)
        title, sub = next((t, s) for k, t, s in PAGES if k == key)
        self.h_title.configure(text=title)
        self.h_sub.configure(text=sub)
        for w in self.page.winfo_children():
            w.destroy()
        getattr(self, f"_page_{key}")()

    # ------------------------------------------------------------------ small parts
    def logo(self, size: int) -> Optional[tk.PhotoImage]:
        """The logo PNG closest to size * display scale (Tk cannot scale PNGs smoothly itself)."""
        want = size * self.app.k
        best = min((36, 44, 54, 72), key=lambda n: abs(n - want))
        if best not in self.images:
            try:
                self.images[best] = tk.PhotoImage(master=self.win, file=str(DATA / f"logo_{best}.png"))
            except tk.TclError:
                return None
        return self.images[best]

    def refresh_page(self) -> None:
        if self.alive():
            self.go(self.page_name)

    def log_changed(self) -> None:
        if self.alive() and self.page_name in ("log", "home"):
            self.go(self.page_name)

    def caption(self, parent, text: str, top: int = 18) -> tk.Label:
        lb = label(parent, text.upper(), self.f.cap, fg=FG3)
        lb.pack(anchor="w", pady=(self.px(top), self.px(6)))
        return lb

    def row(self, parent, title: str, hint: str = "") -> tk.Frame:
        """A settings line: title (+ hint) on the left, the control goes in the returned frame on the right."""
        px = self.px
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x")
        tk.Frame(outer, bg=BG_ROW, height=px(1)).pack(fill="x")
        inner = tk.Frame(outer, bg=BG)
        inner.pack(fill="x", pady=px(10))
        txt = tk.Frame(inner, bg=BG)
        txt.pack(side="left", fill="x", expand=True)
        label(txt, title, self.f.row, anchor="w").pack(anchor="w")
        if hint:
            label(txt, hint, self.f.small, fg=FG3, anchor="w", justify="left",
                  wraplength=px(430)).pack(anchor="w", pady=(px(2), 0))
        ctl = tk.Frame(inner, bg=BG)
        ctl.pack(side="right")
        return ctl

    def number(self, parent, value: int, lo: int, hi: int, on_ok: Callable[[int], None], width: int = 7,
               key: str = "") -> tk.Entry:
        var = tk.StringVar(value=str(value))
        e = entry(parent, var, self.f.mono, width, self.px)
        e.pack(side="left", ipady=self.px(4), ipadx=self.px(6))

        def check(*_a) -> None:
            v = re.sub(r"\D", "", var.get())[:6]
            if v != var.get():
                var.set(v)
                return
            ok = v.isdigit() and lo <= int(v) <= hi
            e.configure(highlightbackground=BORDER if ok else BAD, highlightcolor=ACCENT if ok else BAD)
            if ok:
                self.later(key or str(id(e)), 350, lambda: on_ok(int(v)))

        var.trace_add("write", check)
        return e

    def placeholder(self, ent: tk.Entry, var: tk.StringVar, text: str) -> None:
        ph = label(ent.master, text, ent.cget("font"), fg=FG3, bg=ent.cget("bg"))

        def upd(*_a) -> None:
            if var.get():
                ph.place_forget()
            else:
                ph.place(in_=ent, x=self.px(8), rely=0.5, anchor="w")

        ph.bind("<Button-1>", lambda e: ent.focus_set())
        var.trace_add("write", upd)
        upd()

    def code_style(self) -> str:
        return "dotted" if self.s.reason_style.startswith("dotted") else "plain"

    def example(self, rule: Optional[Rule], kind: str, amount: Optional[int]) -> str:
        if kind == "manual" or rule is None:
            return "Nothing is typed - the bar copies the reason so you can do it by hand"
        a = make_action(kind, amount)
        flag = self.s.jail_flag if a.kind == "jail" else self.s.warn_flag
        cmd = format_command(a, "[id]", rule.code(self.s.reason_style), amount, flag, self.s.perm_days,
                             self.s.commands)
        return finalize(cmd, self.s.test_mode)

    # ================================================================== Home
    def _page_home(self) -> None:
        px = self.px
        s = self.s
        g = self.app.tracker.game
        card = tk.Frame(self.page, bg=BG_ROW)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=BG_ROW)
        inner.pack(fill="x", padx=px(18), pady=px(16))
        logo = self.logo(72)
        if logo is not None:
            label(inner, image=logo, bg=BG_ROW).pack(side="left", padx=(0, px(16)))
        txt = tk.Frame(inner, bg=BG_ROW)
        txt.pack(side="left", fill="x", expand=True)
        label(txt, f"{APP_NAME} is running", self.f.title, bg=BG_ROW).pack(anchor="w")
        line = tk.Frame(txt, bg=BG_ROW)
        line.pack(anchor="w", pady=(px(6), 0))
        if s.test_mode:
            label(line, "TEST MODE  \u00b7  NO /", self.f.pill, fg=ACCENT, bg=ACCENT_BG, padx=px(7),
                  pady=px(2)).pack(side="left")
        else:
            label(line, "LIVE  \u00b7  SENDS /COMMANDS", self.f.pill, fg=GOOD, bg="#17261c", padx=px(7),
                  pady=px(2)).pack(side="left")
        label(line, f"   {g.label} found" if g else "   Waiting for the game", self.f.small,
              fg=FG2 if g else FG3, bg=BG_ROW).pack(side="left")
        n = len(logbook.read(100000))
        label(txt, f"{n} punishment{'s' if n != 1 else ''} in the log", self.f.small, fg=FG3,
              bg=BG_ROW).pack(anchor="w", pady=(px(6), 0))
        btns = tk.Frame(inner, bg=BG_ROW)
        btns.pack(side="right")
        Button(btns, "Open the bar", lambda: self.app.show(), self.f, px, kind="primary").pack(anchor="e")
        Button(btns, f"Stop {APP_NAME}", self.app.quit, self.f, px, kind="danger").pack(anchor="e",
                                                                                       pady=(px(6), 0))
        self.caption(self.page, "How to use it", 22)
        steps = [
            (s.open_hotkey, "In the game, press this. The bar opens at the top of the screen."),
            ("rdm  cl  6.2", "Type the rule, the slang or what happened. 1-8 opens a rule, Shift + 1-8 goes "
                             "straight to the punishment."),
            ("12  Enter", "Type the player ID and press Enter. The command is typed into the game chat for you."),
            (s.hide_hotkey + "  /  Esc", "Hides the bar and puts you back in the game."),
        ]
        for keys, what in steps:
            row = tk.Frame(self.page, bg=BG)
            row.pack(fill="x", pady=px(4))
            label(row, keys, self.f.mono_s, fg=FG, bg=BG_KEY, width=14, padx=px(8), pady=px(4)).pack(side="left")
            label(row, what, self.f.text, fg=FG2, anchor="w", justify="left", wraplength=px(560)).pack(
                side="left", padx=(px(14), 0))
        self.caption(self.page, "Where to find it", 22)
        where = [
            ("Taskbar", "The icon at the bottom right of the taskbar (click ^ if Windows hid it). Right-click it "
                        "to open the bar, the settings or the log, or to quit."),
            ("Start again", "After quitting: the Admin Bar icon on the desktop or in the Start menu. It also "
                            "starts by itself when you turn on the PC."),
            ("Log", "Every punishment you send is saved, see the Log page."),
            ("Remove", "Windows Settings > Apps > Admin Bar > Uninstall."),
        ]
        for title, what in where:
            row = tk.Frame(self.page, bg=BG)
            row.pack(fill="x", pady=px(3))
            label(row, title, self.f.row_b, width=12, anchor="w").pack(side="left")
            label(row, what, self.f.text, fg=FG2, anchor="w", justify="left", wraplength=px(600)).pack(
                side="left", padx=(px(6), 0))

    # ================================================================== Log
    def _page_log(self) -> None:
        px = self.px
        top = tk.Frame(self.page, bg=BG)
        top.pack(fill="x")
        self.l_q = tk.StringVar()
        e = entry(top, self.l_q, self.f.row, 28, px)
        e.pack(side="left", ipady=px(6), ipadx=px(8))
        self.placeholder(e, self.l_q, "Filter  -  player ID, rule, jail ...")
        Button(top, "Open folder", lambda: open_path(logbook.path().parent), self.f, px).pack(side="right")
        Button(top, "Open the log file", lambda: open_path(logbook.ensure_file()), self.f, px).pack(
            side="right", padx=(0, px(8)))
        self.l_count = label(self.page, "", self.f.cap, fg=FG3)
        self.l_count.pack(anchor="w", pady=(px(12), px(6)))
        head = tk.Frame(self.page, bg=BG)
        head.pack(fill="x")
        for text, width in zip(("TIME", "PLAYER", "RULE", "PUNISHMENT", "MODE"), LOG_COLS):
            self._cell(head, px(width), px(22), text, self.f.cap, FG3)
        tk.Frame(self.page, bg=BG_ROW, height=px(1)).pack(fill="x", pady=(px(4), 0))
        box = tk.Frame(self.page, bg=BG)
        box.pack(fill="both", expand=True)
        self.l_rows = []
        fonts = (self.f.mono_s, self.f.mono, self.f.text, self.f.small, self.f.pill)
        for _ in range(15):
            f = tk.Frame(box, bg=BG)
            f.pack(fill="x")
            cells = [self._cell(f, px(w), px(30), "", font, FG) for w, font in zip(LOG_COLS, fonts)]
            for w in [f] + cells + [c.master for c in cells]:
                w.bind("<MouseWheel>", self._l_wheel)
            self.l_rows.append(cells)
        self.l_empty = label(box, "", self.f.text, fg=FG3)
        self.l_off = 0
        self.l_all = logbook.read(5000)
        self.l_q.trace_add("write", lambda *_: self.later("lfilter", 150, self._l_filter))
        self._l_filter()

    def _cell(self, parent, width: int, height: int, text: str, font, fg) -> tk.Label:
        """A fixed-width table cell (pixels, so every font lines up)."""
        box = tk.Frame(parent, bg=BG, width=width, height=height)
        box.pack(side="left")
        box.pack_propagate(False)
        lb = label(box, text, font, fg=fg, anchor="w")
        lb.pack(side="left", fill="x")
        return lb

    def _l_filter(self) -> None:
        q = self.l_q.get().strip().lower()
        if q:
            words = q.split()
            self.l_items = [x for x in self.l_all
                            if all(w in " ".join((x.player, x.rule, x.name, x.what, x.command, x.mode,
                                                  x.time)).lower() for w in words)]
        else:
            self.l_items = list(self.l_all)
        self.l_off = 0
        n, total = len(self.l_items), len(self.l_all)
        self.l_count.configure(text=f"{n} OF {total} PUNISHMENTS" if q else f"{total} PUNISHMENTS")
        self._l_render()

    def _l_render(self) -> None:
        px = self.px
        vis = self.l_items[self.l_off:self.l_off + len(self.l_rows)]
        for i, cells in enumerate(self.l_rows):
            if i >= len(vis):
                for c in cells:
                    c.configure(text="", bg=BG)
                continue
            x = vis[i]
            what = x.what.lower()
            key = "jail" if what.startswith(("jail", "kick")) else "warn" if what.startswith("warn") else \
                "perm" if what.startswith("perm") else "block" if what.startswith("ban") else \
                "mute" if what.startswith("mute") else "manual"
            pfg, _pbg = PILL[key]
            rule = x.rule if x.rule != "-" else ""
            name = f"{rule}  {x.name}" if rule else x.name
            vals = (x.time, x.player, fit_text(name, self.f.text, px(LOG_COLS[2] - 12)),
                    fit_text(x.what, self.f.small, px(LOG_COLS[3] - 10)), x.mode)
            colors = (FG3, FG, FG2, pfg, GOOD if x.mode == "LIVE" else ACCENT)
            for c, v, fg in zip(cells, vals, colors):
                c.configure(text=v, fg=fg, bg=BG)
        if not self.l_all:
            self.l_empty.configure(text="Nothing yet. Every punishment you send with the bar shows up here.")
            self.l_empty.place(x=0, y=px(10))
        else:
            self.l_empty.place_forget()

    def _l_wheel(self, e) -> None:
        n = len(self.l_items)
        self.l_off = max(0, min(max(0, n - len(self.l_rows)), self.l_off + (-3 if e.delta > 0 else 3)))
        self._l_render()

    # ================================================================== Punishments
    def _page_punish(self) -> None:
        px = self.px
        top = tk.Frame(self.page, bg=BG)
        top.pack(fill="x")
        self.p_q = tk.StringVar()
        e = entry(top, self.p_q, self.f.row, 30, px)
        e.pack(side="left", ipady=px(6), ipadx=px(8))
        self.placeholder(e, self.p_q, "Find a rule  -  rdm, 6.2, fear rp")
        self.p_entry = e
        self.p_filter = "all"
        Segmented(top, FILTERS, "all", self._p_set_filter, self.f, px).pack(side="left", padx=(px(14), 0))
        body = tk.Frame(self.page, bg=BG)
        body.pack(fill="both", expand=True, pady=(px(14), 0))
        lst = tk.Frame(body, bg=BG, width=px(372))
        lst.pack(side="left", fill="y")
        lst.pack_propagate(False)
        self.p_count = label(lst, "", self.f.cap, fg=FG3)
        self.p_count.pack(anchor="w", pady=(0, px(6)))
        rows_box = tk.Frame(lst, bg=BG)
        rows_box.pack(fill="both", expand=True)
        self.p_scroll = tk.Canvas(rows_box, width=px(4), bg=BG, highlightthickness=0, bd=0)
        self.p_scroll.pack(side="right", fill="y", padx=(px(4), 0))
        rows_f = tk.Frame(rows_box, bg=BG)
        rows_f.pack(side="left", fill="both", expand=True)
        self.p_rows = []
        for i in range(LIST_ROWS):
            f = tk.Frame(rows_f, bg=BG, height=px(33))
            f.pack(fill="x")
            f.pack_propagate(False)
            bar = tk.Frame(f, bg=BG, width=px(3))
            bar.pack(side="left", fill="y")
            dot = label(f, "", self.f.small, fg=ACCENT, width=2)
            dot.pack(side="left")
            code = label(f, "", self.f.mono, fg=ACCENT, anchor="w", width=10)
            code.pack(side="left")
            pill = label(f, "", self.f.pill, padx=px(6), pady=px(2))
            pill.pack(side="right", padx=(px(6), px(8)))
            name = label(f, "", self.f.text, anchor="w")
            name.pack(side="left", fill="x", expand=True, padx=(px(6), 0))
            for w in (f, bar, dot, code, pill, name):
                w.bind("<Button-1>", lambda ev, i=i: self._p_click(i))
                w.bind("<MouseWheel>", self._p_wheel)
            self.p_rows.append({"f": f, "bar": bar, "dot": dot, "code": code, "name": name, "pill": pill})
        tk.Frame(body, bg=BORDER, width=px(1)).pack(side="left", fill="y", padx=(px(16), px(22)))
        self.p_ed = tk.Frame(body, bg=BG)
        self.p_ed.pack(side="left", fill="both", expand=True)
        self.p_rules: List[Rule] = []
        self.p_sel = 0
        self.p_off = 0
        self.p_q.trace_add("write", lambda *_: self.later("psearch", 120, self._p_refresh))
        e.bind("<Down>", lambda ev: self._p_move(1))
        e.bind("<Up>", lambda ev: self._p_move(-1))
        e.bind("<Next>", lambda ev: self._p_move(LIST_ROWS))
        e.bind("<Prior>", lambda ev: self._p_move(-LIST_ROWS))
        self._p_refresh()
        e.focus_set()

    def _p_set_filter(self, value: str) -> None:
        self.p_filter = value
        self._p_refresh()

    def _p_refresh(self, keep: Optional[str] = None) -> None:
        q = self.p_q.get().strip()
        if q:
            rules = [m.rule for m in self.app.engine.search(q, limit=200).items]
        else:
            rules = list(self.app.book.rules)
        f = self.p_filter
        if f == "changed":
            rules = [r for r in rules if r.id in self.s.overrides]
        elif f != "all":
            rules = [r for r in rules if rule_kind(r) == f or (f == "block" and rule_kind(r) == "perm")]
        self.p_rules = rules
        ids = [r.id for r in rules]
        if keep in ids:
            self.p_sel = ids.index(keep)
        else:
            self.p_sel, self.p_off = 0, 0
        n = len(rules)
        changed = len(self.s.overrides)
        self.p_count.configure(text=f"{n} RULE{'S' if n != 1 else ''}" +
                               (f"   ·   {changed} CHANGED BY YOU" if changed else ""))
        self._p_render()
        self._p_editor()

    def _p_render(self) -> None:
        px = self.px
        if self.p_sel < self.p_off:
            self.p_off = self.p_sel
        elif self.p_sel >= self.p_off + LIST_ROWS:
            self.p_off = self.p_sel - LIST_ROWS + 1
        vis = self.p_rules[self.p_off:self.p_off + LIST_ROWS]
        for i, row in enumerate(self.p_rows):
            if i >= len(vis):
                for k in ("code", "name", "dot"):
                    row[k].configure(text="", bg=BG)
                row["pill"].configure(text="", bg=BG)
                row["f"].configure(bg=BG)
                row["bar"].configure(bg=BG)
                continue
            r = vis[i]
            sel = self.p_off + i == self.p_sel
            bg = BG_SEL if sel else BG
            text, pfg, pbg = pill_of(r.default_action)
            row["f"].configure(bg=bg)
            row["bar"].configure(bg=ACCENT if sel else bg)
            row["dot"].configure(text="●" if r.id in self.s.overrides else "", bg=bg)
            row["code"].configure(text=r.code(self.code_style()), bg=bg)
            row["name"].configure(text=fit_text(r.name, self.f.text, px(176)), bg=bg,
                                  fg=FG if sel else FG2)
            row["pill"].configure(text=text, fg=pfg, bg=pbg)
        # scroll thumb
        c = self.p_scroll
        c.delete("all")
        n = len(self.p_rules)
        h = max(1, c.winfo_height())
        if n > LIST_ROWS:
            top = h * self.p_off / n
            size = max(px(20), h * LIST_ROWS / n)
            c.create_rectangle(0, top, px(4), min(h, top + size), fill=BG_KEY, outline="")

    def _p_click(self, i: int) -> None:
        idx = self.p_off + i
        if idx < len(self.p_rules):
            self.p_sel = idx
            self._p_render()
            self._p_editor()

    def _p_wheel(self, e) -> None:
        n = len(self.p_rules)
        step = -3 if e.delta > 0 else 3
        self.p_off = max(0, min(max(0, n - LIST_ROWS), self.p_off + step))
        self.p_sel = min(max(self.p_sel, self.p_off), self.p_off + LIST_ROWS - 1, max(0, n - 1))
        self._p_render()
        self._p_editor()

    def _p_move(self, d: int) -> str:
        if self.p_rules:
            self.p_sel = max(0, min(len(self.p_rules) - 1, self.p_sel + d))
            self._p_render()
            self._p_editor()
        return "break"

    def _p_rule(self) -> Optional[Rule]:
        return self.p_rules[self.p_sel] if 0 <= self.p_sel < len(self.p_rules) else None

    def _p_editor(self) -> None:
        px = self.px
        ed = self.p_ed
        for w in ed.winfo_children():
            w.destroy()
        r = self._p_rule()
        if r is None:
            label(ed, "No rule matches.", self.f.row, fg=FG3).pack(anchor="w")
            return
        wrap = max(px(300), ed.winfo_width() - px(10)) if ed.winfo_width() > 50 else px(470)
        head = tk.Frame(ed, bg=BG)
        head.pack(fill="x")
        label(head, r.code(self.code_style()), self.f.mono_l, fg=ACCENT).pack(side="left")
        if r.id in self.s.overrides:
            label(head, "CHANGED", self.f.pill, fg=ACCENT, bg=ACCENT_BG, padx=px(7), pady=px(2)).pack(
                side="left", padx=(px(12), 0))
        label(ed, r.name, self.f.title, anchor="w", justify="left", wraplength=wrap).pack(anchor="w",
                                                                                       pady=(px(4), 0))
        self.caption(ed, "Rulebook says", 14)
        label(ed, r.punishment or "-", self.f.text, fg=FG2, anchor="w", justify="left",
              wraplength=wrap).pack(anchor="w")
        self.caption(ed, "Default punishment in the bar", 18)
        kind = rule_kind(r)
        a = r.default_action
        amount = a.amount if a is not None and a.needs_amount else None
        self.p_kind = kind
        Segmented(ed, KIND_OPTIONS, kind, lambda v: self._p_pick_kind(r, v), self.f, px,
                  colors=KIND_COLORS, columns=3).pack(anchor="w")
        if kind in ("jail", "mute", "block"):
            amt_row = tk.Frame(ed, bg=BG)
            amt_row.pack(anchor="w", pady=(px(8), 0))
            unit = "minutes" if kind in ("jail", "mute") else "days"
            self.p_amount = self.number(amt_row, amount or SUGGEST[kind], 1, 99999,
                                        lambda v: self._p_save(r, self.p_kind, v), width=6, key="pamount")
            label(amt_row, unit, self.f.text, fg=FG2).pack(side="left", padx=(px(8), 0))
            chips = tk.Frame(ed, bg=BG)
            chips.pack(anchor="w", pady=(px(6), 0))
            for p in PRESETS[kind]:
                chip = label(chips, str(p), self.f.mono_s, fg=FG if p == amount else FG2,
                             bg=ACCENT_BG if p == amount else BG_ROW, padx=px(8), pady=px(4), cursor="hand2")
                chip.pack(side="left", padx=(0, px(4)))
                chip.bind("<Button-1>", lambda e, p=p: self._p_save(r, self.p_kind, p, redraw=True))
        self.caption(ed, "Types into the chat", 18)
        prev = tk.Frame(ed, bg=BG_ROW)
        prev.pack(fill="x")
        self.p_prev = label(prev, self.example(r, kind, amount), self.f.mono, fg=FG if kind != "manual" else FG2,
                            bg=BG_ROW, anchor="w", justify="left", wraplength=wrap - px(24))
        self.p_prev.pack(anchor="w", padx=px(12), pady=px(9))
        acts = tk.Frame(ed, bg=BG)
        acts.pack(fill="x", pady=(px(14), 0))
        reset = self.p_reset_btn = Button(acts, "Reset to rulebook", lambda: self._p_reset(r), self.f, px)
        reset.pack(side="left")
        reset.enable(r.id in self.s.overrides)
        self.p_status = label(ed, "Changes apply to the bar immediately" if r.id not in self.s.overrides else
                              f"Rulebook default: {r.base_default.label if r.base_default else '-'}",
                              self.f.small, fg=FG3, anchor="w", justify="left", wraplength=wrap)
        self.p_status.pack(anchor="w", pady=(px(6), 0))
        others = [x.label + ("" if x.sendable else " (by hand)") for x in r.actions[1:]] if r.actions else []
        if others:
            self.caption(ed, "Also offered in the bar (↑↓)", 18)
            label(ed, "  ·  ".join(others), self.f.small, fg=FG2, anchor="w", justify="left",
                  wraplength=wrap).pack(anchor="w")
        if r.text:
            self.caption(ed, "Rule", 18)
            body = r.text if len(r.text) <= 300 else r.text[:297].rstrip() + "…"
            label(ed, body, self.f.small, fg=FG3, anchor="w", justify="left", wraplength=wrap).pack(anchor="w")

    def _p_pick_kind(self, r: Rule, kind: str) -> None:
        self.p_kind = kind
        amount = None
        if kind in SUGGEST:
            base = next((a for a in r.base_actions if (a.kind == kind or (kind == "block" and a.kind == "block"))
                         and a.amount and not a.perm), None)
            cur = r.default_action
            if base is not None:
                amount = base.amount
            elif cur is not None and cur.amount and (
                    (kind in ("jail", "mute") and cur.kind in ("jail", "mute")) or (kind == cur.kind == "block")):
                amount = cur.amount
            else:
                amount = SUGGEST[kind]
        self._p_save(r, kind, amount, redraw=True)

    def _p_save(self, r: Rule, kind: str, amount: Optional[int], redraw: bool = False) -> None:
        base = r.base_default
        base_kind = "manual" if base is None or not base.sendable else ("perm" if base.perm else base.kind)
        needs = kind in ("jail", "mute", "block")
        same = kind == base_kind and (not needs or (base is not None and amount == base.amount))
        if same:
            self.s.overrides.pop(r.id, None)
        else:
            self.s.overrides[r.id] = {"kind": kind, "amount": amount if needs else None}
        self.changed()
        if redraw:
            self._p_refresh(keep=r.id)
            return
        # typing a time: keep the field (and its cursor), refresh everything around it
        self._p_render()
        if self.alive() and self.p_prev.winfo_exists():
            self.p_prev.configure(text=self.example(r, kind, amount))
            self.p_reset_btn.enable(r.id in self.s.overrides)
            self.p_status.configure(text="Saved - the bar uses it now" if not same else
                                    "Same as the rulebook", fg=GOOD if not same else FG3)

    def _p_reset(self, r: Rule) -> None:
        self.s.overrides.pop(r.id, None)
        self.changed()
        self._p_refresh(keep=r.id)

    # ================================================================== Quick list
    def _page_quick(self) -> None:
        px = self.px
        self.q_list = tk.Frame(self.page, bg=BG)
        self.q_list.pack(fill="x")
        add = tk.Frame(self.page, bg=BG)
        add.pack(fill="x", pady=(px(8), 0))
        self.caption(add, "Add a rule", 10)
        self.q_add = tk.StringVar()
        e = entry(add, self.q_add, self.f.row, 34, px)
        e.pack(anchor="w", ipady=px(6), ipadx=px(8))
        self.placeholder(e, self.q_add, "Search  -  pg, ub driving, 5.12")
        self.q_sugg = tk.Frame(add, bg=BG)
        self.q_sugg.pack(fill="x", pady=(px(6), 0))
        self.q_add.trace_add("write", lambda *_: self.later("qsugg", 120, self._q_suggest))
        e.bind("<Return>", lambda ev: self._q_add_first())
        foot = tk.Frame(self.page, bg=BG)
        foot.pack(fill="x", side="bottom")
        Button(foot, "Back to the default list", self._q_reset, self.f, px).pack(side="left")
        self._q_render()

    def _q_render(self) -> None:
        px = self.px
        for w in self.q_list.winfo_children():
            w.destroy()
        favs = self.app.engine.favorites(self.s.favorites)
        for i, r in enumerate(favs):
            row = tk.Frame(self.q_list, bg=BG)
            row.pack(fill="x")
            tk.Frame(row, bg=BG_ROW, height=px(1)).pack(fill="x")
            line = tk.Frame(row, bg=BG)
            line.pack(fill="x", pady=px(6))
            label(line, f"Shift+{i + 1}", self.f.mono_s, fg=FG2, bg=BG_KEY, padx=px(7), pady=px(3)).pack(
                side="left")
            label(line, r.code(self.code_style()), self.f.mono, fg=ACCENT, width=10, anchor="w").pack(
                side="left", padx=(px(14), 0))
            label(line, fit_text(r.name, self.f.row, px(330)), self.f.row, anchor="w").pack(side="left")
            ctl = tk.Frame(line, bg=BG)
            ctl.pack(side="right")
            text, pfg, pbg = pill_of(r.default_action)
            label(ctl, text, self.f.pill, fg=pfg, bg=pbg, padx=px(7), pady=px(2)).pack(side="left",
                                                                                      padx=(0, px(14)))
            up = Button(ctl, "↑", lambda i=i: self._q_move(i, -1), self.f, px, kind="ghost")
            up.pack(side="left")
            up.enable(i > 0)
            dn = Button(ctl, "↓", lambda i=i: self._q_move(i, 1), self.f, px, kind="ghost")
            dn.pack(side="left")
            dn.enable(i < len(favs) - 1)
            Button(ctl, "Remove", lambda rid=r.id: self._q_remove(rid), self.f, px, kind="ghost").pack(side="left")
        if not favs:
            label(self.q_list, "The list is empty - add rules below.", self.f.row, fg=FG3).pack(anchor="w")
        if len(favs) >= 9:
            label(self.q_list, "The list is full (9). Remove one to add another.", self.f.small, fg=FG3).pack(
                anchor="w", pady=(px(6), 0))

    def _q_suggest(self) -> None:
        px = self.px
        if not self.alive() or not hasattr(self, "q_sugg") or not self.q_sugg.winfo_exists():
            return
        for w in self.q_sugg.winfo_children():
            w.destroy()
        q = self.q_add.get().strip()
        if not q:
            return
        have = set(self.s.favorites)
        for m in self.app.engine.search(q, limit=6).items[:6]:
            r = m.rule
            row = tk.Frame(self.q_sugg, bg=BG_ROW, cursor="hand2")
            row.pack(fill="x", pady=(0, px(2)))
            code = label(row, r.code(self.code_style()), self.f.mono, fg=ACCENT, bg=BG_ROW, width=10, anchor="w")
            code.pack(side="left", padx=(px(10), 0), pady=px(6))
            name = label(row, fit_text(r.name, self.f.text, px(360)), self.f.text, bg=BG_ROW, anchor="w")
            name.pack(side="left")
            tag = label(row, "in the list" if r.id in have else "+ add", self.f.small,
                        fg=FG3 if r.id in have else ACCENT, bg=BG_ROW)
            tag.pack(side="right", padx=px(12))
            for w in (row, code, name, tag):
                w.bind("<Button-1>", lambda e, rid=r.id: self._q_add_id(rid))

    def _q_add_first(self) -> None:
        q = self.q_add.get().strip()
        if q:
            items = self.app.engine.search(q, limit=1).items
            if items:
                self._q_add_id(items[0].rule.id)

    def _q_add_id(self, rid: str) -> None:
        if rid in self.s.favorites:
            return
        if len(self.s.favorites) >= 9:
            self.app.toast("The quick list holds 9 rules - remove one first", "bad", 2500)
            return
        self.s.favorites.append(rid)
        self.changed()
        self.q_add.set("")
        self._q_render()

    def _q_move(self, i: int, d: int) -> None:
        f = self.s.favorites
        j = i + d
        if 0 <= j < len(f):
            f[i], f[j] = f[j], f[i]
            self.changed()
            self._q_render()

    def _q_remove(self, rid: str) -> None:
        if rid in self.s.favorites and len(self.s.favorites) > 1:
            self.s.favorites.remove(rid)
            self.changed()
            self._q_render()

    def _q_reset(self) -> None:
        self.s.favorites = list(DEFAULT_FAVORITES)
        self.changed()
        self._q_render()

    # ================================================================== Shortcuts
    def _page_shortcuts(self) -> None:
        px = self.px
        self.k_fields = {}
        for key, title, hint in (
                ("open_hotkey", "Open the bar", "Works anywhere, also while the game is in front."),
                ("hide_hotkey", "Hide the bar", "Esc also hides it. You go back to where you were."),
                ("chat_key", "Chat key in the game", "The key that opens the chat in FiveM (T by default).")):
            ctl = self.row(self.page, title, hint)
            fld = label(ctl, "", self.f.mono, fg=FG, bg=BG_FIELD, width=18, padx=px(10), pady=px(6),
                        cursor="hand2", highlightthickness=px(1), highlightbackground=BORDER)
            fld.pack(side="left")
            fld.bind("<Button-1>", lambda e, k=key: self._start_capture(k))
            Button(ctl, "Change", lambda k=key: self._start_capture(k), self.f, px).pack(side="left",
                                                                                        padx=(px(8), 0))
            self.k_fields[key] = fld
        self.k_msg = label(self.page, "", self.f.small, fg=FG3, anchor="w")
        self.k_msg.pack(anchor="w", pady=(px(10), 0))
        self.caption(self.page, "Inside the bar", 18)
        grid = tk.Frame(self.page, bg=BG)
        grid.pack(anchor="w")
        keys = [("1 - 8", "open a result"), ("Shift + 1 - 8", "punish right away (asks only for the ID)"),
                ("↑ ↓  Enter", "move, open"), ("Shift + Enter", "punish the selected rule"),
                ("Tab", "next field (ID, time, 1/0)"), ("↑ ↓  in a rule", "switch punishment"),
                ("Esc", "back, then hide"), ("Ctrl + ,", "this window")]
        for i, (k, what) in enumerate(keys):
            label(grid, k, self.f.mono_s, fg=FG, bg=BG_KEY, padx=px(8), pady=px(3)).grid(
                row=i, column=0, sticky="w", pady=px(3))
            label(grid, what, self.f.text, fg=FG2).grid(row=i, column=1, sticky="w", padx=(px(14), 0))
        self._k_paint()

    def _k_paint(self) -> None:
        errs = self.app.hotkey_errors or {}
        for key, fld in self.k_fields.items():
            if self.capturing == key:
                fld.configure(text="press the keys ...", fg=ACCENT, highlightbackground=ACCENT)
                continue
            val = getattr(self.s, key)
            err = errs.get("open" if key == "open_hotkey" else "hide") if key != "chat_key" else None
            fld.configure(text=val.upper() if key == "chat_key" else val, fg=BAD if err else FG,
                          highlightbackground=BAD if err else BORDER)
        bad = [e for e in (errs.get("open"), errs.get("hide")) if e]
        if bad and not self.capturing:
            self.k_msg.configure(text=bad[0], fg=BAD)

    def _start_capture(self, key: str) -> None:
        if self.capturing:
            self._end_capture(None)
        self.capturing = key
        if key != "chat_key":
            self.app.hotkeys.suspend(True)
        self.k_msg.configure(text="Press the new key" + ("s together, e.g. Alt+Q or F10" if key != "chat_key"
                                                         else ", e.g. T or Y") + ".  Esc cancels.", fg=ACCENT)
        self.win.bind("<KeyPress>", self._capture_key)
        self.win.focus_force()
        self._k_paint()

    def _capture_key(self, e):
        ks = e.keysym
        if ks in MODIFIER_KEYSYMS:
            return "break"
        if ks == "Escape":
            self._end_capture(None)
            return "break"
        if self.capturing == "chat_key":
            name = chr(e.keycode).lower() if (0x30 <= e.keycode <= 0x39 or 0x41 <= e.keycode <= 0x5A) else \
                ks.lower()
            self._end_capture(name)
            return "break"
        name = KEYSYM_NAMES.get(ks) or (ks.upper() if len(ks) == 1 else ks)
        if 0x30 <= e.keycode <= 0x39 or 0x41 <= e.keycode <= 0x5A:
            name = chr(e.keycode)
        mods = [m for m, vk in (("Ctrl", winapi.VK_CONTROL), ("Alt", winapi.VK_MENU), ("Shift", winapi.VK_SHIFT))
                if winapi.key_down(vk)]
        if winapi.key_down(winapi.VK_LWIN) or winapi.key_down(winapi.VK_RWIN):
            mods.append("Win")
        self._end_capture("+".join(mods + [name]))
        return "break"

    def _end_capture(self, value: Optional[str]) -> None:
        key = self.capturing
        self.capturing = None
        try:
            self.win.unbind("<KeyPress>")
        except tk.TclError:
            pass
        if key is None:
            return
        msg, color = "", FG3
        if value and key == "chat_key":
            if value in winapi.SCAN and len(value) <= 3:
                self.s.chat_key = value
                self.changed(False)
                msg, color = f"Chat key: {value.upper()}", GOOD
            else:
                msg, color = "Use a letter, a digit or F1-F12", BAD
        elif value:
            try:
                pretty = parse_hotkey(value)[2]
            except ValueError as exc:
                msg, color = str(exc), BAD
            else:
                other = self.s.hide_hotkey if key == "open_hotkey" else self.s.open_hotkey
                if other.lower() == pretty.lower():
                    msg, color = "Open and hide need different shortcuts", BAD
                else:
                    old = getattr(self.s, key)
                    setattr(self.s, key, pretty)
                    errs = self.app.bind_hotkeys()
                    mine = errs.get("open" if key == "open_hotkey" else "hide")
                    if mine:
                        setattr(self.s, key, old)
                        self.app.bind_hotkeys()
                        msg, color = mine, BAD
                    else:
                        self.changed(False)
                        msg, color = f"{pretty} saved", GOOD
                    value = "bound"
        if key != "chat_key" and value != "bound":
            self.app.hotkeys.suspend(False)
        if self.alive() and hasattr(self, "k_fields") and self.k_fields and \
                next(iter(self.k_fields.values())).winfo_exists():
            self._k_paint()
            self.k_msg.configure(text=msg, fg=color)

    # ================================================================== Commands
    def _page_commands(self) -> None:
        px = self.px
        ctl = self.row(self.page, "Mode", "Live types the real /commands. Test types the same text without the "
                                          "/ so it only shows up as a chat message.")
        Segmented(ctl, [("live", "Live  /commands"), ("test", "Test  no /")],
                  "test" if self.s.test_mode else "live", self._c_mode, self.f, px,
                  colors={"live": (GOOD, "#17261c"), "test": (ACCENT, ACCENT_BG)}).pack()
        self.caption(self.page, "Command formats", 18)
        label(self.page, "{id} player   ·   {time} minutes or days   ·   {flag} 1 / 0   ·   "
                         "{reason} rule code", self.f.small, fg=FG3).pack(anchor="w", pady=(0, px(4)))
        self.c_rows = {}
        sample = self.app.book.get("GR 6.2")
        for kind, title in (("jail", "Jail"), ("warn", "Warn"), ("block", "Ban"), ("mute", "Mute")):
            line = tk.Frame(self.page, bg=BG)
            line.pack(fill="x", pady=px(3))
            label(line, title, self.f.row, width=6, anchor="w").pack(side="left")
            var = tk.StringVar(value=self.s.commands.get(kind, DEFAULT_TEMPLATES[kind]))
            e = entry(line, var, self.f.mono, 36, px)
            e.pack(side="left", ipady=px(4), ipadx=px(6))
            ex = label(line, "", self.f.mono_s, fg=FG3, anchor="w")
            ex.pack(side="left", padx=(px(12), 0))
            self.c_rows[kind] = (var, e, ex)
            var.trace_add("write", lambda *_a, k=kind: self._c_template(k))
        self._c_examples(sample)
        Button(self.page, "Reset the formats", self._c_reset, self.f, px).pack(anchor="w", pady=(px(8), 0))
        ctl = self.row(self.page, "Jail  [1/0]", "The 1/0 value /jail sends.")
        Segmented(ctl, [("1", "1"), ("0", "0")], self.s.jail_flag, lambda v: self._c_set("jail_flag", v),
                  self.f, px).pack()
        ctl = self.row(self.page, "Warn  [1/0]", "The 1/0 value /warn sends.")
        Segmented(ctl, [("1", "1"), ("0", "0")], self.s.warn_flag, lambda v: self._c_set("warn_flag", v),
                  self.f, px).pack()
        ctl = self.row(self.page, "Permanent ban", "The days a permanent ban sends.")
        self.number(ctl, self.s.perm_days, 1, 99999, lambda v: self._c_set("perm_days", v), key="perm")
        label(ctl, "days", self.f.text, fg=FG2).pack(side="left", padx=(px(8), 0))
        ctl = self.row(self.page, "Reason format", "How the rule is written in the reason.")
        Segmented(ctl, [(k, v) for k, v in STYLE_EXAMPLE.items()], self.s.reason_style,
                  lambda v: self._c_set("reason_style", v), self.f, px).pack()

    def _c_examples(self, sample: Optional[Rule]) -> None:
        if sample is None:
            return
        for kind, (var, e, ex) in self.c_rows.items():
            err = check_template(var.get())
            if err:
                ex.configure(text=err, fg=BAD)
                e.configure(highlightbackground=BAD, highlightcolor=BAD)
                continue
            e.configure(highlightbackground=BORDER, highlightcolor=ACCENT)
            amount = {"jail": 60, "block": 7, "mute": 30}.get(kind)
            ex.configure(text=self.example(sample, kind, amount).replace("[id]", "12"), fg=FG3)

    def _c_template(self, kind: str) -> None:
        var = self.c_rows[kind][0]
        if not check_template(var.get()):
            self.s.commands[kind] = var.get().strip().lstrip("/")
            self.later(f"tpl-{kind}", 300, lambda: self.changed(False))
        self._c_examples(self.app.book.get("GR 6.2"))

    def _c_reset(self) -> None:
        self.s.commands = dict(DEFAULT_TEMPLATES)
        self.changed(False)
        for kind, (var, _e, _ex) in self.c_rows.items():
            var.set(DEFAULT_TEMPLATES[kind])

    def _c_mode(self, v: str) -> None:
        self.s.test_mode = v == "test"
        self.changed(False)
        self._c_examples(self.app.book.get("GR 6.2"))

    def _c_set(self, key: str, value) -> None:
        setattr(self.s, key, value)
        self.changed()
        if hasattr(self, "c_rows") and self.page_name == "commands":
            self._c_examples(self.app.book.get("GR 6.2"))

    # ================================================================== Sending
    def _page_sending(self) -> None:
        px = self.px
        ctl = self.row(self.page, "Typing method", "Keystrokes types the command letter by letter. Clipboard "
                                                   "pastes it with Ctrl+V (your clipboard is put back).")
        Segmented(ctl, [("type", "Keystrokes"), ("paste", "Clipboard paste")], self.s.send_method,
                  lambda v: self._c_set("send_method", v), self.f, px).pack()
        for key, title, hint, lo, hi in (
                ("chat_open_ms", "Wait after opening the chat", "Raise it if the first letters go missing.",
                 50, 3000),
                ("between_ms", "Pause between commands", "When you punish several IDs at once.", 200, 10000),
                ("settle_ms", "Wait after switching to the game", "Raise it if the game needs longer to react.",
                 0, 3000)):
            ctl = self.row(self.page, title, hint)
            self.number(ctl, getattr(self.s, key), lo, hi, lambda v, k=key: self._c_set(k, v), key=key)
            label(ctl, "ms", self.f.text, fg=FG2).pack(side="left", padx=(px(8), 0))
        for key, title, hint in (
                ("fix_mouse", "Give the game its mouse", "After switching, the cursor goes into the game and "
                                                         "one middle click hands the mouse back to it."),
                ("return_focus", "Back to the game when the bar hides", "Esc / the hide key puts you back "
                                                                         "where you were."),
                ("confirm_bans", "Ask twice before big bans", "Permanent bans, bans of 30+ days and 3+ IDs at "
                                                              "once need a second Enter.")):
            ctl = self.row(self.page, title, hint)
            Toggle(ctl, bool(getattr(self.s, key)), lambda v, k=key: self._c_set(k, v), px).pack()

    # ================================================================== General
    def _page_general(self) -> None:
        px = self.px
        ctl = self.row(self.page, "Start with Windows", "Admin Bar starts quietly when you sign in, ready for "
                                                        f"{self.s.open_hotkey}.")
        Toggle(ctl, autostart_enabled(), self._g_autostart, px).pack()
        g = self.app.tracker.game
        ctl = self.row(self.page, "Game", f"{g.label}: {g.process or g.title}" if g else
                       "Not running. It is found again automatically when it starts or restarts.")
        label(ctl, "FOUND" if g else "WAITING", self.f.pill, fg=GOOD if g else FG3,
              bg="#17261c" if g else BG_ROW, padx=px(7), pady=px(2)).pack()
        ctl = self.row(self.page, "Your files", "Settings, the punishment log and the app log.")
        Button(ctl, "Open folder", lambda: open_path(data_dir()), self.f, px).pack(side="left")
        Button(ctl, "Open the punishment log", lambda: open_path(logbook.ensure_file()), self.f, px).pack(
            side="left", padx=(px(8), 0))
        ctl = self.row(self.page, "Reset everything", "All settings, punishments and the quick list go back to "
                                                      "the defaults.")
        self.g_reset = Button(ctl, "Reset", self._g_reset, self.f, px, kind="danger")
        self.g_reset.pack()
        self._g_armed = False
        ctl = self.row(self.page, f"Quit {APP_NAME}", "Stops the bar until you start it again from the desktop "
                                                      "or Start menu. Remove it in Windows Settings > Apps.")
        Button(ctl, "Quit", self.app.quit, self.f, px, kind="danger").pack()

    def _g_autostart(self, on: bool) -> None:
        if not set_autostart(on):
            self.app.toast("Windows refused the change", "bad", 2500)

    def _g_reset(self) -> None:
        if not self._g_armed:
            self._g_armed = True
            self.g_reset.configure(text="Click again to reset")
            return
        fresh = Settings()
        for name in fresh.__dataclass_fields__:
            setattr(self.s, name, getattr(fresh, name))
        self.changed()
        self.app.bind_hotkeys()
        self.go("general")
        self.app.toast("Everything is back to the defaults", "good", 2500)


def open_path(p: Path) -> None:
    try:
        if not p.exists() and p.suffix:
            p.write_text("", encoding="utf-8")
        os.startfile(str(p))  # type: ignore[attr-defined]
    except OSError:
        subprocess.Popen(["explorer", str(p if p.is_dir() else p.parent)])


def dark_title_bar(hwnd: int) -> None:
    try:
        dwm = ctypes.WinDLL("dwmapi")
        on = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer / older Windows 10)
            if dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(on), 4) == 0:
                break
    except (OSError, AttributeError):
        pass
