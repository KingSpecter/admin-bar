"""1.2: the punishment log, the taskbar icon's menu, the launcher arguments, the installer layout."""
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

import tkinter as tk

from adminbar import logbook, main as app_main, ui, winapi
from adminbar.game import Game
from adminbar.rules import RuleBook
from adminbar.settings import Settings

APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture()
def home(monkeypatch):
    d = tempfile.mkdtemp(prefix="adminbar-v12-")
    monkeypatch.setenv("ADMINBAR_HOME", d)
    return Path(d)


# ---------------------------------------------------------------- the punishment log
def test_log_roundtrip(home):
    assert logbook.read() == []
    logbook.record("LIVE", "12", "G.R 6.2", "RDM (Random\tDeathmatch)", "Jail 60 min", "/jail 12 60 1 G.R 6.2")
    logbook.record("TEST", "7", "-", "typed command", "unjail", "unjail 7 oops")
    text = logbook.path().read_text(encoding="utf-8")
    assert text.startswith("# Admin Bar punishment log") and text.count("\n") == 3
    rows = logbook.read()
    assert [r.player for r in rows] == ["7", "12"]                     # newest first
    assert rows[1].name == "RDM (Random Deathmatch)" and rows[1].command == "/jail 12 60 1 G.R 6.2"


@pytest.fixture()
def app(home, monkeypatch):
    monkeypatch.setattr(tk.Misc, "focus_force", lambda self: None)
    monkeypatch.setattr(tk.Wm, "deiconify", lambda self: None)
    monkeypatch.setattr(winapi, "bring_to_front", lambda *a, **k: False)
    monkeypatch.setattr(winapi, "set_clipboard_text", lambda t: True)
    a = ui.App(Settings(), RuleBook.load())
    a.bind_hotkeys = lambda: {}
    a.root.update()
    yield a
    a.root.destroy()


def wait_queue(a, timeout=3.0):
    end = time.time() + timeout
    while a.q.empty() and time.time() < end:
        time.sleep(0.02)
    while not a.q.empty():
        kind, data = a.q.get_nowait()
        a._handle(kind, data)


def test_sending_writes_the_log(app, monkeypatch):
    monkeypatch.setattr(ui, "send_lines", lambda lines, game, s: len(lines))
    app.tracker.current = lambda: Game(1, 2, "FiveM_GTAProcess.exe", "FiveM")
    app.open_rule(app.book.get("GR 6.2"), quick=True)
    app.idvar.set("12, 13")
    app.amtvar.set("45")
    app.root.update()
    app._submit()
    wait_queue(app)
    rows = logbook.read()
    assert [(r.player, r.rule, r.what, r.mode) for r in rows] == [("13", "G.R 6.2", "Jail 45 min", "LIVE"),
                                                                    ("12", "G.R 6.2", "Jail 45 min", "LIVE")]
    assert rows[0].command == "/jail 13 45 1 G.R 6.2"


def test_partial_send_logs_only_what_went_out(app, monkeypatch):
    from adminbar.sender import SendError

    def boom(lines, game, s):
        raise SendError("lostfocus", "lost", sent=1)

    monkeypatch.setattr(ui, "send_lines", boom)
    monkeypatch.setattr(app, "show", lambda reset=True: None)
    app.tracker.current = lambda: Game(1, 2, "x.exe", "FiveM")
    app.open_rule(app.book.get("GR 6.8"), quick=True)
    app.idvar.set("1 2")
    app.root.update()
    app._submit()
    wait_queue(app)
    assert [r.player for r in logbook.read()] == ["1"]


def test_log_page_filters(app):
    for pid, rule in (("5", "G.R 6.2"), ("6", "G.R 6.1"), ("55", "G.R 6.2")):
        logbook.record("LIVE", pid, rule, "x", "Jail 60 min", f"/jail {pid} 60 1 {rule}")
    app.open_config("log")
    cw = app.config_win
    assert len(cw.l_items) == 3
    cw.l_q.set("6.1")
    cw._l_filter()
    assert [x.player for x in cw.l_items] == ["6"]
    cw.l_q.set("5 6.2")
    cw._l_filter()
    assert sorted(x.player for x in cw.l_items) == ["5", "55"]
    cw.go("home")
    app.root.update()


# ---------------------------------------------------------------- the taskbar icon
def test_tray_menu_and_commands(app, monkeypatch):
    state = {"auto": False}
    monkeypatch.setattr(ui, "autostart_enabled", lambda: state["auto"])
    monkeypatch.setattr(ui, "set_autostart", lambda on: state.update(auto=on) or True)
    items = [i for i in app._tray_menu() if i]
    assert [i[0] for i in items] == ["bar", "config", "log", "autostart", "test", "quit"]
    app._tray_command("autostart")
    assert state["auto"] is True and dict((i[0], i[2]) for i in app._tray_menu() if i)["autostart"] is True
    app._tray_command("test")
    assert app.s.test_mode is True and Settings.load().test_mode is True
    app._tray_command("test")
    assert app.s.test_mode is False
    app._tray_command("log")
    assert app.config_win.page_name == "log"
    app._tray_command("config")
    assert app.config_win.alive()


# ---------------------------------------------------------------- launcher arguments
def test_log_argument_opens_the_file(home, monkeypatch):
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    assert app_main.main(["--log"]) == 0
    assert opened and opened[0].endswith("punishments.log")


def test_quit_when_not_running_says_so(monkeypatch):
    said = []
    monkeypatch.setattr(winapi, "signal_event", lambda name: False)
    monkeypatch.setattr(winapi, "message_box", lambda text, title, error=False: said.append(text))
    assert app_main.main(["--quit"]) == 0
    assert said and "not running" in said[0]


def test_second_start_opens_the_window(monkeypatch):
    sent = []
    monkeypatch.setattr(winapi, "create_mutex", lambda name: (1, True))
    monkeypatch.setattr(winapi, "signal_event", lambda name: sent.append(name) or True)
    assert app_main.main([]) == 0 and sent == ["Local\\AdminBar.Show"]
    sent.clear()
    assert app_main.main(["--background"]) == 0 and sent == []     # sign-in start: stay quiet


# ---------------------------------------------------------------- the installer
def test_install_shortcuts_are_named_clearly():
    sys.path.insert(0, str(APP_DIR / "adminbar"))
    import install
    names = [n for n, _a, _t in install.FOLDER_SHORTCUTS]
    assert names == ["Start Admin Bar", "Stop Admin Bar", "Admin Bar settings", "Punishment log",
                     "Uninstall Admin Bar"]
