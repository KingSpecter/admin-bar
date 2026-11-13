"""The bar: a search line at the top-centre of the game's screen, results under it, the punish form and small
toasts. Tk runs on the main thread; hotkeys, the game tracker and the sender live on their own threads and
talk to it through one queue. The config window (config.py) shares the same Tk root and settings."""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from . import APP_NAME, logbook, winapi
from .game import Game, GameTracker
from .hotkeys import HotkeyService
from .rules import (RAW_COMMANDS, Action, Rule, RuleBook, action_label, finalize, format_command, parse_raw,
                    sanitize_reason)
from .search import SearchEngine, transliterate
from .sender import SendError, return_to, send_lines
from .settings import Settings, autostart_enabled, set_autostart
from .tray import TrayIcon
from .theme import (ACCENT, ACCENT_BG, BAD, BG, BG_FIELD, BG_KEY, BG_ROW, BG_SEL, BORDER, FG, FG2, FG3, GOOD,
                    Fonts, fit_text, pill_of)

log = logging.getLogger(__name__)

ICON = Path(__file__).resolve().parent / "data" / "icon.ico"
SHOW_EVENT = "Local\\AdminBar.Show"
QUIT_EVENT = "Local\\AdminBar.Quit"
ALPHA = 0.95
ROWS = 8
WIDTH = 700

COMMAND_WORDS = ("jail", "unjail", "warn", "unwarn", "block", "unblock", "ban", "mute", "unmute", "kick", "perm",
                 "refinement")


def digit_selects(query: str, n: int, visible: int) -> bool:
    """A digit typed in the search line: open result n, or part of the text ('6.2', 'gr 5', 'unjail 12')?
    Empty line -> text (rule numbers); after a word ('rdm', 'fear rp ') -> pick."""
    s = query.strip().lower()
    if not s or n > visible:
        return False
    if re.search(r"[\d.,]$", query):
        return False  # in the middle of a number
    if re.fullmatch(r"(c\.?c\.?r|[gacezsldr]\.?r|t|r)\.?", s) or \
            re.search(r"\b(c\.?c\.?r|[gacezsldr]\.?r)\.?\s*(t|r)?$", s):
        return False  # a rule number is being typed: 'gr ', 'g.r', 'cr t', 't'
    first = s.split()[0].lstrip("/")
    if first in COMMAND_WORDS or s.startswith("/"):
        return False  # 'unjail 12 ...', 'jail 60'
    return bool(re.search(r"[a-z؀-ۿ]", s))


@dataclass
class Item:
    kind: str                     # rule | raw
    rule: Optional[Rule] = None
    raw: Optional[Tuple[str, str, bool]] = None
    tag: str = ""                 # finding | allowed | alias | favorite ...


class App:
    def __init__(self, settings: Settings, book: RuleBook, background: bool = True) -> None:
        self.s = settings
        self.book = book
        self.book.apply_overrides(self.s.overrides)
        self.engine = SearchEngine(book)
        self.background = background
        self.started_from = winapi.foreground()  # the window that was active before any of ours existed
        self.q: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        try:
            self.root.iconbitmap(default=str(ICON))  # every window of ours carries the logo
        except tk.TclError:
            pass
        self.root.report_callback_exception = self._tk_error
        self.tray: Optional[TrayIcon] = None
        self.k = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.W = self.px(WIDTH)
        self.f = Fonts(self.root)
        self.tracker = GameTracker(lambda: self.s, on_change=lambda g, why: self.q.put(("game", (g, why))))
        self.hotkeys = HotkeyService(lambda name: self.q.put(("hotkey", name)), SHOW_EVENT, QUIT_EVENT)
        self.config_win = None
        self.visible = False
        self.shown_at = 0.0
        self.prev_hwnd = 0
        self.busy = False
        self.view = "search"
        self.items: List[Item] = []
        self.sel = 0
        self.offset = 0
        self.result_title = ""
        self.sec_right_text = ""
        self.cur_rule: Optional[Rule] = None
        self.cur_action = 0
        self.quick_mode = False
        self.confirm_token = ""
        self.hotkey_errors: dict = {}
        self._search_job: Optional[str] = None
        self._height = 0
        self._had_focus = False
        self._build()

    # ================================================================== basics
    def px(self, n: float) -> int:
        return int(round(n * self.k))

    def _tk_error(self, exc, val, tb) -> None:
        log.error("ui error", exc_info=(exc, val, tb))
        try:
            self.toast("Something went wrong - details are in the log", "bad")
        except Exception:
            pass

    def lbl(self, parent, text="", font=None, fg=FG, bg=BG, **kw) -> tk.Label:
        return tk.Label(parent, text=text, font=font or self.f.row, fg=fg, bg=bg, bd=0, **kw)

    def entry(self, parent, var, font, width, bg=BG_FIELD) -> tk.Entry:
        return tk.Entry(parent, textvariable=var, font=font, width=width, bg=bg, fg=FG, bd=0, relief="flat",
                        insertbackground=ACCENT, insertwidth=self.px(2), selectbackground="#3b3e46",
                        selectforeground=FG, highlightthickness=self.px(1), highlightbackground=BORDER,
                        highlightcolor=ACCENT, disabledbackground=bg)

    # ================================================================== build
    def _build(self) -> None:
        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", ALPHA)
        r.configure(bg=BORDER)
        self.inner = tk.Frame(r, bg=BG)
        self.inner.pack(fill="both", expand=True, padx=self.px(1), pady=self.px(1))
        self._build_header()
        tk.Frame(self.inner, bg=BORDER, height=self.px(1)).pack(fill="x")
        self.body = tk.Frame(self.inner, bg=BG)
        self.body.pack(fill="both", expand=True)
        self._build_list()
        self._build_detail()
        self._build_footer()
        self._build_toast()
        r.update_idletasks()
        self.hwnd = winapi.toplevel_of(r.winfo_id())
        winapi.add_ex_style(self.hwnd, winapi.WS_EX_TOOLWINDOW, winapi.WS_EX_APPWINDOW)
        winapi.round_corners(self.hwnd)

    def _build_header(self) -> None:
        h = tk.Frame(self.inner, bg=BG)
        h.pack(fill="x", padx=self.px(14), pady=(self.px(10), self.px(9)))
        c = tk.Canvas(h, width=self.px(20), height=self.px(20), bg=BG, highlightthickness=0, bd=0)
        k = self.k
        c.create_oval(2 * k, 2 * k, 14 * k, 14 * k, outline=FG3, width=max(1, int(2 * k)))
        c.create_line(12.5 * k, 12.5 * k, 18 * k, 18 * k, fill=FG3, width=max(1, int(2 * k)), capstyle="round")
        c.pack(side="left", padx=(0, self.px(10)))
        right = tk.Frame(h, bg=BG)
        right.pack(side="right")
        self.mode_pill = self.lbl(right, "LIVE", self.f.pill, padx=self.px(7), pady=self.px(2))
        self.mode_pill.pack(side="right", padx=(self.px(8), 0))
        self.game_lbl = self.lbl(right, "", self.f.small, fg=FG3)
        self.game_lbl.pack(side="right")
        self.game_dot = tk.Canvas(right, width=self.px(8), height=self.px(8), bg=BG, highlightthickness=0, bd=0)
        self.dot = self.game_dot.create_oval(0, 0, self.px(7), self.px(7), fill=FG3, outline="")
        self.game_dot.pack(side="right", padx=(0, self.px(6)))
        self.qvar = tk.StringVar()
        self.qe = tk.Entry(h, textvariable=self.qvar, font=self.f.query, bg=BG, fg=FG, bd=0, relief="flat",
                           insertbackground=ACCENT, insertwidth=self.px(2), selectbackground="#3b3e46",
                           selectforeground=FG, highlightthickness=0)
        self.qe.pack(side="left", fill="x", expand=True)
        self.place = self.lbl(h, "Search rules  -  rdm, fear rp, 6.2, or what happened", self.f.query, fg=FG3)
        self.place.place(in_=self.qe, x=self.px(1), rely=0.5, anchor="w")
        self.place.bind("<Button-1>", lambda e: self.qe.focus_set())
        self.qvar.trace_add("write", self._on_query)
        self.qe.bind("<KeyPress>", self._search_key)

    def _build_list(self) -> None:
        self.listf = tk.Frame(self.body, bg=BG)
        top = tk.Frame(self.listf, bg=BG)
        top.pack(fill="x", padx=self.px(14), pady=(self.px(8), self.px(4)))
        self.sec_lbl = self.lbl(top, "", self.f.cap, fg=FG3)
        self.sec_lbl.pack(side="left")
        self.sec_right = self.lbl(top, "", self.f.cap, fg=FG3)
        self.sec_right.pack(side="right")
        self.rows = []
        for i in range(ROWS):
            f = tk.Frame(self.listf, bg=BG, height=self.px(36))
            f.pack_propagate(False)
            bar = tk.Frame(f, bg=BG, width=self.px(3))
            bar.pack(side="left", fill="y")
            num = self.lbl(f, str(i + 1), self.f.mono_s, fg=FG2, bg=BG_KEY, width=2)
            num.pack(side="left", padx=(self.px(11), self.px(10)), pady=self.px(9))
            code = self.lbl(f, "", self.f.mono, fg=ACCENT, anchor="w")
            code.pack(side="left")
            pill = self.lbl(f, "", self.f.pill, padx=self.px(7), pady=self.px(2))
            pill.pack(side="right", padx=(self.px(8), self.px(14)))
            name = self.lbl(f, "", self.f.row, anchor="w")
            name.pack(side="left", fill="x", expand=True, padx=(self.px(10), 0))
            row = {"f": f, "bar": bar, "num": num, "code": code, "name": name, "pill": pill}
            for w in (f, num, code, name, pill):
                w.bind("<Button-1>", lambda e, i=i: self._row_click(i, e))
                w.bind("<Motion>", lambda e, i=i: self._row_hover(i))
                w.bind("<MouseWheel>", self._wheel)
            self.rows.append(row)
        self._tail = tk.Frame(self.listf, bg=BG, height=self.px(6))
        self.listf.bind("<MouseWheel>", self._wheel)

    def _build_detail(self) -> None:
        d = self.detf = tk.Frame(self.body, bg=BG)
        pad = self.px(16)
        head = tk.Frame(d, bg=BG)
        head.pack(fill="x", padx=pad, pady=(self.px(12), self.px(2)))
        self.d_code = self.lbl(head, "", self.f.mono_l, fg=ACCENT)
        self.d_code.pack(side="left")
        self.d_pill = self.lbl(head, "", self.f.pill, padx=self.px(7), pady=self.px(2))
        self.d_pill.pack(side="right")
        self.d_name = self.lbl(head, "", self.f.title, anchor="w")
        self.d_name.pack(side="left", padx=(self.px(12), 0), fill="x", expand=True)
        wrap = self.W - 2 * pad
        self.d_text = self.lbl(d, "", self.f.text, fg=FG2, anchor="w", justify="left", wraplength=wrap)
        self.d_exc = self.lbl(d, "", self.f.small, fg="#d8b46e", anchor="w", justify="left", wraplength=wrap)
        self.d_extra = self.lbl(d, "", self.f.small, fg=FG3, anchor="w", justify="left", wraplength=wrap)
        self.d_opts = tk.Frame(d, bg=BG)
        self.d_form = tk.Frame(d, bg=BG)
        self.idvar, self.amtvar, self.flagvar = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.f_id = tk.Frame(self.d_form, bg=BG)
        self.lbl(self.f_id, "PLAYER ID", self.f.cap, fg=FG3).pack(anchor="w")
        self.e_id = self.entry(self.f_id, self.idvar, self.f.mono_l, 16)
        self.e_id.pack(anchor="w", pady=(self.px(3), 0), ipady=self.px(4), ipadx=self.px(6))
        self.f_amt = tk.Frame(self.d_form, bg=BG)
        self.amt_cap = self.lbl(self.f_amt, "MINUTES", self.f.cap, fg=FG3)
        self.amt_cap.pack(anchor="w")
        self.e_amt = self.entry(self.f_amt, self.amtvar, self.f.mono_l, 6)
        self.e_amt.pack(anchor="w", pady=(self.px(3), 0), ipady=self.px(4), ipadx=self.px(6))
        self.f_flag = tk.Frame(self.d_form, bg=BG)
        self.lbl(self.f_flag, "1 / 0", self.f.cap, fg=FG3).pack(anchor="w")
        self.e_flag = self.entry(self.f_flag, self.flagvar, self.f.mono_l, 3)
        self.e_flag.pack(anchor="w", pady=(self.px(3), 0), ipady=self.px(4), ipadx=self.px(6))
        for e in (self.e_id, self.e_amt, self.e_flag):
            e.bind("<KeyPress>", self._detail_key)
        for v in (self.idvar, self.amtvar, self.flagvar):
            v.trace_add("write", self._on_field)
        self.d_prev = tk.Frame(d, bg=BG_ROW)
        self.lbl(self.d_prev, "›", self.f.mono_l, fg=FG3, bg=BG_ROW).pack(side="left",
                                                                            padx=(self.px(10), self.px(6)))
        self.d_prev_mode = self.lbl(self.d_prev, "", self.f.cap, fg=ACCENT, bg=BG_ROW)
        self.d_prev_mode.pack(side="right", padx=self.px(10))
        self.d_prev_txt = self.lbl(self.d_prev, "", self.f.mono, fg=FG, bg=BG_ROW, anchor="w")
        self.d_prev_txt.pack(side="left", fill="x", expand=True, pady=self.px(8))
        self.d_msg = self.lbl(d, "", self.f.small, fg=FG2, anchor="w", justify="left", wraplength=wrap)

    def _build_footer(self) -> None:
        tk.Frame(self.inner, bg=BORDER, height=self.px(1)).pack(fill="x")
        self.foot = tk.Frame(self.inner, bg=BG)
        self.foot.pack(fill="x")
        self.foot_l = self.lbl(self.foot, "", self.f.small, fg=FG3)
        self.foot_l.pack(side="left", padx=self.px(14), pady=self.px(7))
        self.foot_r = self.lbl(self.foot, "Ctrl+,  config", self.f.small, fg=FG3, cursor="hand2")
        self.foot_r.pack(side="right", padx=self.px(14))
        self.foot_r.bind("<Button-1>", lambda e: self.open_config())

    def _build_toast(self) -> None:
        t = self.toast_w = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.attributes("-alpha", 0.0)
        t.configure(bg=BORDER)
        inner = tk.Frame(t, bg=BG)
        inner.pack(fill="both", expand=True, padx=self.px(1), pady=self.px(1))
        self.toast_bar = tk.Frame(inner, bg=ACCENT, width=self.px(3))
        self.toast_bar.pack(side="left", fill="y")
        self.toast_lbl = self.lbl(inner, "", self.f.row, anchor="w", justify="left", wraplength=self.px(560))
        self.toast_lbl.pack(side="left", padx=(self.px(12), self.px(16)), pady=self.px(10))
        t.geometry("+-32000+-32000")
        t.update_idletasks()
        self.toast_hwnd = winapi.toplevel_of(t.winfo_id())
        winapi.add_ex_style(self.toast_hwnd, winapi.WS_EX_NOACTIVATE | winapi.WS_EX_TRANSPARENT |
                            winapi.WS_EX_TOOLWINDOW, winapi.WS_EX_APPWINDOW)
        winapi.round_corners(self.toast_hwnd)
        self._toast_job: Optional[str] = None

    # ================================================================== run
    def _release_startup_focus(self, previous: int) -> None:
        """Tk makes one of our hidden windows the active one at start-up: keys typed right after sign-in would
        vanish into it. Give the focus back to the window that had it (or to the desktop)."""
        fg = winapi.foreground()
        if fg not in (self.hwnd, self.toast_hwnd):
            return
        target = previous if previous and winapi.is_window(previous) and previous not in (self.hwnd,
                                                                                          self.toast_hwnd)             else int(winapi.user32.GetShellWindow() or 0)
        if target:
            winapi.user32.SetForegroundWindow(target)

    def run(self) -> None:
        self.hotkeys.start()
        self.bind_hotkeys()
        self.tracker.start()
        self.tray = TrayIcon(str(ICON), self._tray_tip(), self._tray_menu, lambda c: self.q.put(("tray", c)))
        self.tray.start()
        self._update_status()
        self._render_list()
        self.root.after(30, self._pump)
        self.root.after(400, self._startup)
        self.root.update()
        self._release_startup_focus(self.started_from)
        self.root.mainloop()

    def bind_hotkeys(self) -> dict:
        self.hotkey_errors = self.hotkeys.bind({"open": self.s.open_hotkey, "hide": self.s.hide_hotkey})
        self._refresh_tray()
        return self.hotkey_errors

    # ---- the taskbar icon
    def _tray_tip(self) -> str:
        g = self.tracker.game
        return f"{APP_NAME} - {self.s.open_hotkey} opens the bar" + (f" ({g.label} found)" if g else "")

    def _refresh_tray(self) -> None:
        if self.tray is not None:
            self.tray.set_tooltip(self._tray_tip())

    def _tray_menu(self) -> list:
        """Built on the tray thread each time you right-click (so the ticks are always current)."""
        return [("bar", f"Open the bar\t{self.s.open_hotkey}", False, True),
                ("config", "Settings", False, True),
                ("log", "Punishment log", False, True),
                None,
                ("autostart", "Start with Windows", autostart_enabled(), True),
                ("test", "Test mode (no /)", bool(self.s.test_mode), True),
                None,
                ("quit", f"Quit {APP_NAME}", False, True)]

    def _tray_command(self, cmd: str) -> None:
        if cmd == "bar":
            if not self.busy:
                self.show(reset=not self.visible)
        elif cmd == "config":
            self.open_config()
        elif cmd == "log":
            self.open_config("log")
        elif cmd == "autostart":
            on = not autostart_enabled()
            ok = set_autostart(on)
            self.toast(("Starts with Windows" if on else "Does not start with Windows any more") if ok else
                       "Windows refused the change", "good" if ok else "bad", 2500)
            if self.config_win is not None and self.config_win.alive():
                self.config_win.refresh_page()
        elif cmd == "test":
            self.s.test_mode = not self.s.test_mode
            self.settings_changed()
            self.toast("Test mode: commands are sent without the /" if self.s.test_mode else
                       "Live: commands are sent with the /", "info", 2800)
            if self.config_win is not None and self.config_win.alive():
                self.config_win.refresh_page()
        elif cmd == "quit":
            self.quit()

    def _startup(self) -> None:
        err = self.hotkey_errors.get("open")
        if err or not self.background:
            self.open_config("shortcuts" if err else None)
            if err:
                self.toast(f"Open shortcut not set: {err}. Pick another one in Shortcuts.", "bad", 7000)
            return
        g = self.tracker.game
        where = f" - {g.label} found" if g else " - waiting for the game"
        self.toast(f"{APP_NAME} is running{where}.  {self.s.open_hotkey} opens, {self.s.hide_hotkey} hides.",
                   "info", 4500)

    def quit(self) -> None:
        log.info("quitting")
        try:
            self.tracker.stop()
            self.hotkeys.stop()
            if self.tray is not None:
                self.tray.stop()
        finally:
            self.root.after(50, self.root.destroy)

    def open_config(self, page: Optional[str] = None) -> None:
        if self.visible:
            self.hide(restore=False)
        from .config import ConfigWindow
        if self.config_win is None or not self.config_win.alive():
            self.config_win = ConfigWindow(self)
        self.config_win.show(page)

    def settings_changed(self, rebuild_list: bool = True) -> None:
        """Called by the config window after every change: save, re-apply, refresh the bar."""
        self.s.validate()
        self.s.save()
        self.book.apply_overrides(self.s.overrides)
        self.engine._cache.clear()
        self._update_status()
        self._refresh_tray()
        if rebuild_list and self.view == "search":
            self._run_search()

    def _pump(self) -> None:
        try:
            while True:
                kind, data = self.q.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        if self.visible and not self.busy:
            fg = winapi.foreground()
            if fg == self.hwnd:
                self._had_focus = True
            elif self._had_focus and fg and fg != self.toast_hwnd and time.monotonic() - self.shown_at > 0.6:
                self.hide(restore=False)  # you clicked or alt-tabbed somewhere else
        self.root.after(30, self._pump)

    def _handle(self, kind: str, data) -> None:
        if kind == "hotkey":
            if data == "open":
                if self.busy:
                    self.toast("Still sending ...", "info", 1200)
                elif self.config_win is not None and self.config_win.capturing:
                    pass
                else:
                    self.show(reset=not self.visible)
            elif data == "hide":
                if self.visible:
                    self.hide()
            elif data == "show":       # Admin Bar was started again: open its window
                self.open_config()
            elif data == "quit":
                self.quit()
        elif kind == "tray":
            self._tray_command(data)
        elif kind == "game":
            game, why = data
            self._update_status()
            self._refresh_tray()
            if self.config_win is not None and self.config_win.alive():
                self.config_win.game_changed()
            if not self.visible:
                if why == "found":
                    self.toast(f"{game.label} found - {self.s.open_hotkey} opens the bar", "good", 2600)
                elif why == "restarted":
                    self.toast(f"Game restarted - tracking the new {game.label} window", "good", 2600)
                elif why == "lost":
                    self.toast("Game closed - waiting for it to start again", "info", 2400)
        elif kind == "sent":
            lines, = data
            self.busy = False
            n = len(lines)
            msg = f"Sent  {lines[0]}" if n == 1 else f"Sent {n} commands  {lines[0]}  ..."
            if self.s.test_mode:
                msg += "    (test mode: no /)"
            self.toast(msg, "good", 3200)
            self.qvar.set("")
            if self.config_win is not None and self.config_win.alive():
                self.config_win.log_changed()
        elif kind == "send_error":
            err, snap = data
            self.busy = False
            log.warning("send failed: %s (%s)", err.message, err.code)
            if err.sent:
                self.toast(f"{err.sent} sent, then stopped: {err.message}", "bad", 6000)
            if snap is not None:
                self.show(reset=False)
                self._restore_snapshot(snap)
                self._msg(err.message, BAD)
            else:
                self.toast(err.message, "bad", 6000)

    # ================================================================== show / hide
    def _place(self) -> None:
        g = self.tracker.game
        if g is not None and winapi.is_window(g.hwnd) and not winapi.is_minimized(g.hwnd):
            area = winapi.monitor_rect_for(g.hwnd) or winapi.work_area_for(g.hwnd)
        else:
            area = winapi.work_area_for(0)
        left, top, right, _ = area
        self._x = left + (right - left - self.W) // 2
        self._y = top + self.px(44)

    def _fit(self) -> None:
        self.root.update_idletasks()
        h = self.inner.winfo_reqheight() + 2 * self.px(1)
        if not hasattr(self, "_x"):
            self._place()
        self.root.geometry(f"{self.W}x{h}+{self._x}+{self._y}")
        self._height = h

    def show(self, reset: bool = True) -> None:
        fg = winapi.foreground()
        own = {self.hwnd, self.toast_hwnd}
        if self.config_win is not None and self.config_win.alive():
            own.add(self.config_win.hwnd)
        if self.tray is not None:
            own.add(self.tray.hwnd)
        if fg and fg not in own and not winapi.window_class(fg).startswith(("Shell_", "NotifyIcon")):
            self.prev_hwnd = fg
        if reset:
            self.view_search(clear=True)
        self._place()
        self._update_status()
        self._fit()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.visible = True
        self._had_focus = False
        self.shown_at = time.monotonic()
        self._hide_toast()
        self.root.after(1, self._activate)
        g = self.tracker.game
        if g is not None and fg == g.hwnd:
            self.root.after(500, lambda: self._check_fullscreen(g))

    def _activate(self) -> None:
        winapi.bring_to_front(self.hwnd, settle=0.08)
        self.root.focus_force()
        if self.view == "detail":
            self.e_id.focus_force()
            self.e_id.icursor("end")
        else:
            self.qe.focus_force()
            self.qe.icursor("end")

    def _check_fullscreen(self, g: Game) -> None:
        if winapi.is_minimized(g.hwnd):
            self.toast("The game is in exclusive fullscreen - set it to Borderless so the bar can show over it",
                       "bad", 7000)

    def hide(self, restore: bool = True) -> None:
        if not self.visible:
            return
        self.visible = False
        self.root.withdraw()
        prev = self.prev_hwnd
        if restore and self.s.return_focus and prev and winapi.is_window(prev):
            game = self.tracker.game
            threading.Thread(target=return_to, args=(prev, game, self.s.fix_mouse), daemon=True).start()

    # ================================================================== status
    def _update_status(self) -> None:
        g = self.tracker.game
        if g is not None:
            self.game_dot.itemconfigure(self.dot, fill=GOOD)
            self.game_lbl.configure(text=g.label, fg=FG2)
        else:
            self.game_dot.itemconfigure(self.dot, fill=FG3)
            self.game_lbl.configure(text="no game", fg=FG3)
        if self.s.test_mode:
            self.mode_pill.configure(text="TEST", fg=ACCENT, bg=ACCENT_BG)
        else:
            self.mode_pill.configure(text="LIVE", fg=GOOD, bg="#17261c")

    # ================================================================== search view
    def view_search(self, clear: bool = False) -> None:
        self.view = "search"
        self.quick_mode = False
        self.confirm_token = ""
        self.detf.pack_forget()
        self.listf.pack(fill="both", expand=True)
        if clear:
            if self.qvar.get():
                self.qvar.set("")  # runs the search
            else:
                self._run_search()
        else:
            self._render_list()
        self._footer()
        if self.visible:
            self._fit()
            self.qe.focus_force()

    def _footer(self) -> None:
        if self.view == "search":
            self.foot_l.configure(text="↑↓ Enter open   1-8 open   Shift+1-8 punish   Esc hide")
            return
        a = self._cur_act()
        r = self.cur_rule
        parts = ["Enter send" if a.sendable else "Enter copy reason"]
        if a.needs_amount or a.needs_flag:
            parts.append("Tab next field")
        if r is not None and len(r.actions) > 1:
            parts.append("↑↓ punishment")
        parts.append("Esc back")
        self.foot_l.configure(text="   ".join(parts))

    def _on_query(self, *_a) -> None:
        text = self.qvar.get()
        fixed = transliterate(text)
        if fixed != text:  # an Arabic layout was on: show the English keys
            pos = self.qe.index("insert")
            self.qvar.set(fixed)
            self.qe.icursor(min(pos, len(fixed)))
            return
        if text:
            self.place.place_forget()
        else:
            self.place.place(in_=self.qe, x=self.px(1), rely=0.5, anchor="w")
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(25, self._run_search)

    def _run_search(self) -> None:
        self._search_job = None
        text = self.qvar.get()
        items: List[Item] = []
        title, right = "", ""
        raw = parse_raw(text)
        if not text.strip():
            items = [Item("rule", r, tag="favorite") for r in self.engine.favorites(self.s.favorites)]
            title, right = "QUICK PUNISH", "Shift+number sends at once"
        elif raw:
            items.append(Item("raw", raw=raw))
            title = "COMMAND  ·  ENTER SENDS IT" if raw[2] else f"COMMAND NEEDS: {self._usage(raw[0]).upper()}"
        else:
            res = self.engine.search(text, limit=40)
            items += [Item("rule", m.rule, tag=m.kind) for m in res.items]
            if res.mode in ("situation", "book", "punishment"):
                title = res.title.upper()
            elif res.mode == "id":
                title = "RULE NUMBER"
            elif res.mode == "none":
                title = "NOTHING FOUND - TRY OTHER WORDS, A RULE NUMBER OR SLANG (RDM, FRP, CL ...)"
            else:
                title = f"{len(res.items)} RESULTS" if len(res.items) != 1 else "1 RESULT"
            if res.corrections:
                fixes = ", ".join(f"{a} → {b}" for a, b in list(res.corrections.items())[:3])
                title += f"   ·  READ AS: {fixes}"
        self.items = items
        self.sel = 0
        self.offset = 0
        self.result_title = title
        self.sec_right_text = right
        self._render_list()
        if self.visible:
            self._fit()

    def _render_list(self) -> None:
        self.sec_lbl.configure(text=self.result_title)
        self.sec_right.configure(text=self.sec_right_text)
        vis = self.items[self.offset:self.offset + ROWS]
        code_style = "dotted" if self.s.reason_style.startswith("dotted") else "plain"
        name_w = self.W - self.px(270)
        for i, row in enumerate(self.rows):
            if i >= len(vis):
                row["f"].pack_forget()
                continue
            it = vis[i]
            sel = (self.offset + i) == self.sel
            bg = BG_SEL if sel else BG
            row["f"].configure(bg=bg)
            row["bar"].configure(bg=ACCENT if sel else bg)
            for k in ("code", "name"):
                row[k].configure(bg=bg)
            row["num"].configure(text=str(i + 1), fg=FG if sel else FG2)
            if it.kind == "raw":
                name, cmd, complete = it.raw
                row["code"].configure(text=name if self.s.test_mode else "/" + name, fg=ACCENT, width=10)
                text = cmd if complete else f"{cmd}   (needs: {self._usage(name)})"
                row["name"].configure(text=fit_text(text, self.f.row, name_w), fg=FG if complete else FG2,
                                      font=self.f.row)
                row["pill"].configure(text="SEND" if complete else "INCOMPLETE",
                                      fg=GOOD if complete else FG3, bg="#17261c" if complete else BG_ROW)
            else:
                r = it.rule
                text, pfg, pbg = pill_of(r.default_action, len(r.actions) > 1)
                row["code"].configure(text=r.code(code_style), fg=ACCENT if it.tag != "allowed" else GOOD, width=10)
                name = ("Allowed: " if it.tag == "allowed" else "") + r.name
                font = self.f.row_b if sel else self.f.row
                row["name"].configure(text=fit_text(name, font, name_w), fg=FG, font=font)
                row["pill"].configure(text=text, fg=pfg, bg=pbg)
            row["f"].pack(fill="x")
        self._tail.pack_forget()
        self._tail.pack(fill="x")

    @staticmethod
    def _usage(name: str) -> str:
        return RAW_COMMANDS[name][1]

    def _row_click(self, i: int, e) -> None:
        idx = self.offset + i
        if idx >= len(self.items):
            return
        self.sel = idx
        (self.quick if e.state & 0x1 else self.open_index)(idx)

    def _row_hover(self, i: int) -> None:
        idx = self.offset + i
        if idx != self.sel and idx < len(self.items):
            self.sel = idx
            self._render_list()

    def _wheel(self, e) -> None:
        self._move(-1 if e.delta > 0 else 1)

    def _move(self, d: int) -> None:
        if not self.items:
            return
        self.sel = max(0, min(len(self.items) - 1, self.sel + d))
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + ROWS:
            self.offset = self.sel - ROWS + 1
        self._render_list()

    def _digit_selects(self, n: int) -> bool:
        if self.qe.selection_present():
            return False
        return digit_selects(self.qvar.get(), n, len(self.items[self.offset:self.offset + ROWS]))

    def _search_key(self, e):
        vk = e.keycode
        shift, ctrl = bool(e.state & 0x1), bool(e.state & 0x4)
        alt = bool(e.state & 0x20000) or winapi.key_down(winapi.VK_MENU)
        ks = e.keysym
        digit = vk - 0x30 if 0x31 <= vk <= 0x39 else (vk - 0x60 if 0x61 <= vk <= 0x69 else 0)
        if digit:
            idx = self.offset + digit - 1
            if shift and 0x31 <= vk <= 0x39:
                if idx < len(self.items):
                    self.quick(idx)
                return "break"
            if ctrl or alt:
                if idx < len(self.items):
                    self.open_index(idx)
                return "break"
            if self._digit_selects(digit):
                self.open_index(idx)
                return "break"
            return None
        if ks in ("Down", "Tab") and not (ks == "Tab" and shift):
            self._move(1)
            return "break"
        if ks == "Up" or (ks in ("Tab", "ISO_Left_Tab") and shift):
            self._move(-1)
            return "break"
        if ks == "Next":
            self._move(ROWS)
            return "break"
        if ks == "Prior":
            self._move(-ROWS)
            return "break"
        if ks in ("Return", "KP_Enter"):
            if self.items:
                (self.quick if shift else self.open_index)(self.sel)
            return "break"
        if ks == "Escape":
            if self.qvar.get():
                self.qvar.set("")
            else:
                self.hide()
            return "break"
        if ctrl and vk == 0xBC:  # Ctrl+,
            self.open_config()
            return "break"
        if ctrl and ks == "BackSpace":
            text = self.qvar.get()[: self.qe.index("insert")]
            rest = self.qvar.get()[self.qe.index("insert"):]
            cut = re.sub(r"\S*\s*$", "", text)
            self.qvar.set(cut + rest)
            self.qe.icursor(len(cut))
            return "break"
        if ctrl and ks.lower() == "a":
            self.qe.select_range(0, "end")
            return "break"
        return None

    def open_index(self, idx: int) -> None:
        if not (0 <= idx < len(self.items)):
            return
        it = self.items[idx]
        if it.kind == "raw":
            self._send_raw(it.raw)
        else:
            self.open_rule(it.rule, quick=False)

    def quick(self, idx: int) -> None:
        if not (0 <= idx < len(self.items)):
            return
        it = self.items[idx]
        if it.kind == "raw":
            self._send_raw(it.raw)
        else:
            self.open_rule(it.rule, quick=True)

    def _send_raw(self, raw) -> None:
        name, cmd, complete = raw
        if not complete:
            self.result_title = f"COMMAND NEEDS: {self._usage(name).upper()}"
            self._render_list()
            return
        player = cmd.split()[1] if len(cmd.split()) > 1 else "-"
        self._send([finalize(cmd, self.s.test_mode)], None, [(player, "-", "typed command", name)])

    # ================================================================== detail / punish view
    def _cur_act(self) -> Action:
        r = self.cur_rule
        if r is None or not r.actions:
            return Action("manual", "")
        return r.actions[min(self.cur_action, len(r.actions) - 1)]

    def open_rule(self, rule: Rule, quick: bool = False, action_index: Optional[int] = None) -> None:
        self.view = "detail"
        self.cur_rule = rule
        self.quick_mode = quick
        if action_index is None:
            default = rule.default_action
            action_index = rule.actions.index(default) if default in rule.actions else 0
        self.cur_action = action_index
        self.confirm_token = ""
        self.listf.pack_forget()
        self.detf.pack(fill="both", expand=True)
        self.idvar.set("")
        self._load_action_fields()
        self._render_detail()
        self._fit()
        self.e_id.focus_force()

    def _load_action_fields(self) -> None:
        a = self._cur_act()
        self.amtvar.set(str(a.amount) if a.amount is not None and a.needs_amount else "")
        self.flagvar.set(self.s.jail_flag if a.kind == "jail" else self.s.warn_flag)

    def _render_detail(self) -> None:
        r = self.cur_rule
        if r is None:
            return
        a = self._cur_act()
        style = self.s.reason_style
        self.d_code.configure(text=r.code("dotted" if style.startswith("dotted") else "plain"))
        self.d_name.configure(text=fit_text(r.name, self.f.title, self.W - self.px(300)))
        text, pfg, pbg = pill_of(a)
        self.d_pill.configure(text=text, fg=pfg, bg=pbg)
        for w in (self.d_text, self.d_exc, self.d_extra, self.d_opts, self.d_form, self.d_prev, self.d_msg):
            w.pack_forget()
        pad = self.px(16)
        if not self.quick_mode:
            body = r.text if len(r.text) <= 420 else r.text[:417].rstrip() + "…"
            self.d_text.configure(text=f"“{body}”")
            self.d_text.pack(fill="x", padx=pad, pady=(self.px(6), 0))
            if r.exception:
                self.d_exc.configure(text=f"Exception: {r.exception}")
                self.d_exc.pack(fill="x", padx=pad, pady=(self.px(6), 0))
        extras = list(r.extras)
        if a.note:
            extras.insert(0, a.note[:1].upper() + a.note[1:])
        if a.custom:
            extras.insert(0, f"Your default (rulebook: {r.punishment})")
        if extras:
            self.d_extra.configure(text="Also: " + "  ·  ".join(extras) if not a.custom else
                                   "  ·  ".join(extras))
            self.d_extra.pack(fill="x", padx=pad, pady=(self.px(6), 0))
        for w in self.d_opts.winfo_children():
            w.destroy()
        if len(r.actions) > 1:
            self.lbl(self.d_opts, "PUNISHMENT", self.f.cap, fg=FG3).pack(side="left", padx=(0, self.px(10)))
            for i, act in enumerate(r.actions):
                on = i == self.cur_action
                t = act.label + ("" if act.sendable else "  (by hand)")
                chip = self.lbl(self.d_opts, t, self.f.small, fg=FG if on else FG2, bg=BG_SEL if on else BG_ROW,
                                padx=self.px(9), pady=self.px(3), cursor="hand2")
                chip.configure(highlightthickness=self.px(1), highlightbackground=ACCENT if on else BG_ROW)
                chip.pack(side="left", padx=(0, self.px(6)))
                chip.bind("<Button-1>", lambda e, i=i: self._pick_action(i))
            self.lbl(self.d_opts, "↑↓", self.f.small, fg=FG3).pack(side="left", padx=self.px(4))
            self.d_opts.pack(fill="x", padx=pad, pady=(self.px(10), 0))
        for w in (self.f_id, self.f_amt, self.f_flag):
            w.pack_forget()
        if a.sendable:
            self.f_id.pack(side="left")
            if a.needs_amount:
                cap = a.unit.upper()
                if a.max_amount:
                    cap += f" (MAX {a.max_amount})"
                self.amt_cap.configure(text=cap)
                self.f_amt.pack(side="left", padx=(self.px(14), 0))
            if a.needs_flag:
                self.f_flag.pack(side="left", padx=(self.px(14), 0))
            self.d_form.pack(fill="x", padx=pad, pady=(self.px(12), 0))
        self.d_prev.pack(fill="x", padx=pad, pady=(self.px(12), 0))
        self.d_msg.pack(fill="x", padx=pad, pady=(self.px(6), self.px(10)))
        self._update_preview()
        self._footer()

    def _pick_action(self, i: int) -> None:
        r = self.cur_rule
        if r is None or not (0 <= i < len(r.actions)):
            return
        ids = self.idvar.get()
        self.cur_action = i
        self.confirm_token = ""
        self._load_action_fields()
        self.idvar.set(ids)
        self._render_detail()
        self._fit()
        self.e_id.focus_force()
        self.e_id.icursor("end")

    def _on_field(self, *_a) -> None:
        # keep the fields clean as you type (Arabic digits become 0-9)
        for var, bad, cap in ((self.idvar, r"[^\d, ]", 80), (self.amtvar, r"\D", 5), (self.flagvar, r"[^01]", 1)):
            v = var.get()
            clean = re.sub(bad, "", transliterate(v))
            clean = clean[-cap:] if var is self.flagvar else clean[:cap]
            if clean != v:
                var.set(clean)
                return
        self.confirm_token = ""
        if self.view == "detail":
            self._update_preview()

    def _ids(self) -> Tuple[List[str], str]:
        raw = [x for x in re.split(r"[,\s]+", self.idvar.get().strip()) if x]
        if not raw:
            return [], "Type the player's ID"
        if len(raw) > 10:
            return [], "At most 10 IDs at once"
        seen: List[str] = []
        for x in raw:
            if not x.isdigit() or len(x) > 7:
                return [], f"'{x}' is not a player ID"
            x = str(int(x))
            if x not in seen:
                seen.append(x)
        return seen, ""

    def _build_lines(self) -> Tuple[List[str], str]:
        r = self.cur_rule
        a = self._cur_act()
        if r is None or not a.sendable:
            return [], ""
        ids, err = self._ids()
        reason = sanitize_reason(r.code(self.s.reason_style))
        amount: Optional[int] = None
        if a.needs_amount:
            if not self.amtvar.get().strip():
                return [], f"Type the {a.unit.lower()}"
            amount = int(self.amtvar.get())
            if amount <= 0:
                return [], "Must be at least 1"
            if a.max_amount and amount > a.max_amount:
                return [], f"This rule allows at most {a.max_amount} {a.unit.lower()}"
        flag = self.flagvar.get() or (self.s.jail_flag if a.kind == "jail" else self.s.warn_flag)
        shown_ids = ids or ["[id]"]
        lines = [finalize(format_command(a, pid, reason, amount, flag, self.s.perm_days, self.s.commands),
                          self.s.test_mode) for pid in shown_ids]
        return lines, err

    def _update_preview(self) -> None:
        a = self._cur_act()
        if self.cur_rule is None:
            return
        if not a.sendable:
            self.d_prev_txt.configure(text=f"No in-game command for: {a.label}", fg=FG2)
            self.d_prev_mode.configure(text="ENTER COPIES THE REASON", fg=FG3)
            self._msg("")
            return
        lines, err = self._build_lines()
        if not lines:
            self.d_prev_txt.configure(text="", fg=FG)
        else:
            more = f"   (+{len(lines) - 1} more)" if len(lines) > 1 else ""
            self.d_prev_txt.configure(text=fit_text(lines[0] + more, self.f.mono, self.W - self.px(250)),
                                      fg=FG if not err else FG2)
        if self.s.test_mode:
            self.d_prev_mode.configure(text="TEST  ·  NO /", fg=ACCENT)
        else:
            self.d_prev_mode.configure(text="LIVE", fg=GOOD)
        if err and self.idvar.get().strip():
            self._msg(err, BAD)
        elif self.tracker.game is None:
            self._msg("Game not found - Enter copies the command instead", FG3)
        else:
            self._msg("")

    def _msg(self, text: str, color: str = FG2) -> None:
        self.d_msg.configure(text=text, fg=color)

    def _detail_key(self, e):
        ks = e.keysym
        shift = bool(e.state & 0x1)
        if ks in ("Return", "KP_Enter"):
            self._submit()
            return "break"
        if ks == "Escape":
            self.view_search(clear=False)
            self._fit()
            return "break"
        if ks in ("Up", "Down"):
            r = self.cur_rule
            if r and len(r.actions) > 1:
                self._pick_action((self.cur_action + (1 if ks == "Down" else -1)) % len(r.actions))
            return "break"
        if ks in ("Tab", "ISO_Left_Tab"):
            order = [w for w, f in ((self.e_id, self.f_id), (self.e_amt, self.f_amt), (self.e_flag, self.f_flag))
                     if f.winfo_manager()]
            if e.widget in order and len(order) > 1:
                i = (order.index(e.widget) + (-1 if shift else 1)) % len(order)
                order[i].focus_set()
                order[i].select_range(0, "end")
                order[i].icursor("end")
            return "break"
        if e.widget is self.e_flag and ks == "space":
            self.flagvar.set("0" if self.flagvar.get() == "1" else "1")
            return "break"
        if e.widget is self.e_flag and e.char in ("0", "1"):
            self.flagvar.set(e.char)
            return "break"
        if (e.state & 0x4) and e.keycode == 0xBC:  # Ctrl+,
            self.open_config()
            return "break"
        return None

    def _snapshot(self) -> dict:
        return {"rule": self.cur_rule.id if self.cur_rule else None, "action": self.cur_action,
                "ids": self.idvar.get(), "amount": self.amtvar.get(), "flag": self.flagvar.get(),
                "quick": self.quick_mode}

    def _restore_snapshot(self, snap: dict) -> None:
        rule = self.book.get(snap.get("rule") or "")
        if rule is None:
            self.view_search()
            return
        self.open_rule(rule, quick=snap.get("quick", False), action_index=snap.get("action", 0))
        self.idvar.set(snap.get("ids", ""))
        self.amtvar.set(snap.get("amount", ""))
        self.flagvar.set(snap.get("flag", ""))
        self._fit()

    def _submit(self) -> None:
        r = self.cur_rule
        a = self._cur_act()
        if r is None:
            return
        if not a.sendable:
            reason = r.code(self.s.reason_style)
            ok = winapi.set_clipboard_text(reason)
            self._msg(f"Copied '{reason}' - {a.label} is done by hand" if ok else "Could not copy",
                      GOOD if ok else BAD)
            return
        lines, err = self._build_lines()
        if err:
            self._msg(err, BAD)
            self.e_id.focus_set()
            return
        ids, _ = self._ids()
        big = a.kind == "block" and (a.perm or (self.amtvar.get().isdigit() and int(self.amtvar.get()) >= 30))
        token = "|".join(lines)
        if ((self.s.confirm_bans and big) or len(ids) >= 3) and self.confirm_token != token:
            self.confirm_token = token
            if a.perm:
                what = "PERMANENTLY ban"
            elif a.kind == "block":
                what = f"ban for {self.amtvar.get()} days"
            else:
                what = a.kind
            self._msg(f"Press Enter again to {what}: {', '.join(ids)}", ACCENT)
            return
        self.confirm_token = ""
        amount = int(self.amtvar.get()) if a.needs_amount and self.amtvar.get().isdigit() else None
        what = action_label("perm" if a.perm else a.kind, amount)
        code = r.code(self.s.reason_style)
        self._send(lines, self._snapshot(), [(pid, code, r.name, what) for pid in ids])

    # ================================================================== sending
    def _send(self, lines: List[str], snap: Optional[dict], meta: Optional[list] = None) -> None:
        """meta: per line (player, rule code, rule name, punishment) for the punishment log."""
        if self.busy:
            return
        game = self.tracker.current()
        if game is None:
            ok = winapi.set_clipboard_text("\n".join(lines))
            text = "Game not found - command copied to the clipboard" if ok else "Game not found"
            if self.view == "detail":
                self._msg(text, BAD)
            else:
                self.toast(text, "bad", 4000)
            return
        self.busy = True
        self.prev_hwnd = 0  # we end in the game
        self.visible = False
        self.root.withdraw()
        self._hide_toast()
        self.root.update_idletasks()  # the bar is really gone before the game is touched
        settings = self.s
        mode = "TEST" if self.s.test_mode else "LIVE"
        info = list(meta or [])

        def write_log(count: int) -> None:
            for i, line in enumerate(lines[:count]):
                player, code, name, what = info[i] if i < len(info) else ("-", "-", "-", "-")
                logbook.record(mode, player, code, name, what, line)

        def work() -> None:
            try:
                write_log(send_lines(lines, game, settings))
                self.q.put(("sent", (lines,)))
            except SendError as exc:
                write_log(exc.sent)
                self.q.put(("send_error", (exc, snap)))
            except Exception as exc:  # noqa: BLE001 - reported to the user, logged in full
                log.exception("send crashed")
                self.q.put(("send_error", (SendError("crash", f"Sending failed: {exc}"), snap)))

        threading.Thread(target=work, name="sender", daemon=True).start()

    # ================================================================== toast
    def toast(self, text: str, tone: str = "info", ms: int = 2600) -> None:
        color = {"good": GOOD, "bad": BAD}.get(tone, ACCENT)
        self.toast_bar.configure(bg=color)
        self.toast_lbl.configure(text=text)
        t = self.toast_w
        t.update_idletasks()
        w = min(self.px(620), t.winfo_reqwidth())
        h = t.winfo_reqheight()
        self._place()
        x = self._x + (self.W - w) // 2
        y = self._y + (self._height + self.px(10) if self.visible else 0)
        t.geometry(f"{w}x{h}+{x}+{y}")
        t.attributes("-alpha", 0.96)
        winapi.set_topmost(self.toast_hwnd)
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(ms, self._hide_toast)

    def _hide_toast(self) -> None:
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = None
        self.toast_w.attributes("-alpha", 0.0)
        self.toast_w.geometry("+-32000+-32000")
