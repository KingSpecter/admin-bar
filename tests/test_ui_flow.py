"""Keyboard flows through the real bar and config window (Tk), with sending, focus and the clipboard stubbed:
nothing reaches a game and no window takes the focus."""
import os
import tempfile
from types import SimpleNamespace

import pytest

os.environ["ADMINBAR_HOME"] = tempfile.mkdtemp(prefix="adminbar-ui-")

import tkinter as tk  # noqa: E402

from adminbar import ui, winapi  # noqa: E402
from adminbar.rules import RuleBook  # noqa: E402
from adminbar.settings import Settings  # noqa: E402


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("ADMINBAR_HOME", tempfile.mkdtemp(prefix="adminbar-ui-"))
    monkeypatch.setattr(tk.Misc, "focus_force", lambda self: None)
    monkeypatch.setattr(tk.Wm, "deiconify", lambda self: None)
    monkeypatch.setattr(winapi, "bring_to_front", lambda *a, **k: False)
    monkeypatch.setattr(winapi, "set_clipboard_text", lambda t: True)
    monkeypatch.setattr("adminbar.config.set_autostart", lambda on: True)
    a = ui.App(Settings(), RuleBook.load())
    a.sent = []
    a._send = lambda lines, snap, meta=None: a.sent.append(list(lines))
    a.bind_hotkeys = lambda: {}
    a.hotkeys.suspend = lambda on: None
    a.view_search(clear=True)
    a.root.update()
    yield a
    a.root.destroy()


def settle(a):
    a.root.update()
    if a._search_job:
        a.root.after_cancel(a._search_job)
        a._run_search()
    a.root.update()


CHARS = {"period": ".", "space": " "}


def key(a, widget, keysym, keycode=0, shift=False, ctrl=False):
    """What Tk does for a key press on that widget: run its <KeyPress> handler, then (unless the handler said
    'break') the default insert. The bar stays hidden, so Tk itself would not route keys to it."""
    state = (1 if shift else 0) | (4 if ctrl else 0)
    char = CHARS.get(keysym, keysym if len(keysym) == 1 else "")
    ev = SimpleNamespace(keysym=keysym, keycode=keycode, state=state, widget=widget, char=char, delta=0)
    handler = {a.qe: a._search_key, a.e_id: a._detail_key, a.e_amt: a._detail_key, a.e_flag: a._detail_key}[widget]
    if handler(ev) != "break" and char and isinstance(widget, tk.Entry):
        widget.insert("insert", char)
    settle(a)


def typ(a, widget, text):
    for ch in text:
        widget.insert("insert", ch)
    settle(a)


def punish(a, query, ids, enter_twice=False):
    a.view_search(clear=True)
    settle(a)
    typ(a, a.qe, query)
    key(a, a.qe, "Return")
    typ(a, a.e_id, ids)
    key(a, a.e_id, "Return")
    if enter_twice:
        key(a, a.e_id, "Return")


# ---------------------------------------------------------------- the bar
def test_shift_number_on_empty_bar_is_quick_punish(app):
    key(app, app.qe, "exclam", 0x31, shift=True)          # Shift+1 -> RDM, ID prompt
    assert app.view == "detail" and app.cur_rule.id == "GR 6.2" and app.quick_mode
    typ(app, app.e_id, "12")
    key(app, app.e_id, "Return")
    assert app.sent == [["/jail 12 60 1 G.R 6.2"]]


def test_number_picks_after_a_word_but_types_inside_a_rule_number(app):
    typ(app, app.qe, "vdm")
    key(app, app.qe, "1", 0x31)
    assert app.view == "detail" and app.cur_rule.id == "GR 6.1" and not app.quick_mode
    key(app, app.e_id, "Escape")
    assert app.view == "search" and app.qvar.get() == "vdm"
    app.qvar.set("")
    settle(app)
    for ch, code in (("6", 0x36), ("period", 0xBE), ("2", 0x32)):
        key(app, app.qe, ch, code)
    assert app.view == "search" and app.qvar.get() == "6.2"
    assert {i.rule.id for i in app.items} == {"GR 6.2", "SR 6.2"}
    app.qvar.set("")
    for ch, code in (("g", 0x47), ("r", 0x52), ("space", 0x20), ("5", 0x35)):
        key(app, app.qe, ch, code)
    assert app.view == "search" and app.qvar.get() == "gr 5" and app.items[0].rule.id == "GR 5.1"


def test_permanent_ban_needs_two_enters(app):
    key(app, app.qe, "asterisk", 0x38, shift=True)        # Shift+8 -> cheats
    assert app.cur_rule.id == "GR 3.1"
    typ(app, app.e_id, "45")
    key(app, app.e_id, "Return")
    assert app.sent == [] and "Enter again" in app.d_msg.cget("text")
    key(app, app.e_id, "Return")
    assert app.sent == [["/block 45 9999 G.R 3.1"]]


def test_ooc_insult_mutes_first_and_ban_is_one_arrow_away(app):
    typ(app, app.qe, "gr 2.1")
    key(app, app.qe, "Return")
    assert app._cur_act().kind == "mute" and app.amtvar.get() == "180"
    typ(app, app.e_id, "7")
    key(app, app.e_id, "Return")
    assert app.sent == [["/mute 7 180 G.R 2.1"]]
    key(app, app.e_id, "Down")                             # the ban option
    assert app._cur_act().kind == "block"
    app.amtvar.set("9")
    settle(app)
    key(app, app.e_id, "Return")
    assert len(app.sent) == 1 and "at most 7" in app.d_msg.cget("text")
    app.amtvar.set("3")
    settle(app)
    key(app, app.e_id, "Return")
    assert app.sent[-1] == ["/block 7 3 G.R 2.1"]


def test_several_ids_and_flag(app):
    typ(app, app.qe, "fear rp")
    key(app, app.qe, "Return")
    typ(app, app.e_id, "3, 4,4")
    app.flagvar.set("0")
    settle(app)
    key(app, app.e_id, "Return")
    assert app.sent == [["/warn 3 0 G.R 6.8", "/warn 4 0 G.R 6.8"]]


def test_three_ids_ask_first(app):
    punish(app, "rdm", "1 2 3")
    assert app.sent == []
    key(app, app.e_id, "Return")
    assert len(app.sent[0]) == 3


def test_id_field_keeps_digits_only(app):
    typ(app, app.qe, "rdm")
    key(app, app.qe, "Return")
    typ(app, app.e_id, "1a2-3٤")
    assert app.idvar.get() == "1234"


def test_raw_commands(app):
    typ(app, app.qe, "unjail 12 wrong id")
    assert app.items[0].kind == "raw" and len(app.items) == 1
    key(app, app.qe, "Return")
    app.qvar.set("")
    typ(app, app.qe, "/mute 5 30 spam")
    key(app, app.qe, "Return")
    assert app.sent == [["/unjail 12 wrong id"], ["/mute 5 30 spam"]]


def test_test_mode_drops_the_slash(app):
    app.s.test_mode = True
    punish(app, "cl", "8")
    assert app.sent == [["warn 8 1 G.R 5.2"]]


def test_arabic_layout_is_converted_in_the_box(app):
    typ(app, app.qe, "قية")                   # r d m typed on an Arabic layout
    assert app.qvar.get() == "rdm" and app.items[0].rule.id == "GR 6.2"


def test_escape_clears_first(app):
    typ(app, app.qe, "rdm")
    key(app, app.qe, "Escape")
    assert app.qvar.get() == ""


# ---------------------------------------------------------------- the config window drives the bar
@pytest.fixture()
def cfg(app):
    app.open_config("punish")
    app.root.update()
    return app.config_win


def pick_rule(cfg, query):
    cfg.p_q.set(query)
    cfg._p_refresh()
    return cfg._p_rule()


def test_config_opens_every_page(cfg):
    from adminbar.config import PAGES
    for key_, *_ in PAGES:
        cfg.go(key_)
        cfg.win.update()
    assert cfg.alive()


def test_change_rdm_to_warn_then_reset(app, cfg):
    r = pick_rule(cfg, "rdm")
    assert r.id == "GR 6.2"
    cfg._p_pick_kind(r, "warn")
    assert app.s.overrides["GR 6.2"] == {"kind": "warn", "amount": None}
    assert Settings.load().overrides["GR 6.2"]["kind"] == "warn"           # saved to disk
    punish(app, "rdm", "5")
    assert app.sent[-1] == ["/warn 5 1 G.R 6.2"]
    cfg._p_reset(app.book.get("GR 6.2"))
    assert "GR 6.2" not in app.s.overrides
    punish(app, "rdm", "5")
    assert app.sent[-1] == ["/jail 5 60 1 G.R 6.2"]


def test_change_time_and_kind(app, cfg):
    r = pick_rule(cfg, "vdm")
    cfg._p_save(r, "jail", 90)                     # typing a new time
    punish(app, "vdm", "2")
    assert app.sent[-1] == ["/jail 2 90 1 G.R 6.1"]
    cfg._p_pick_kind(app.book.get("GR 6.1"), "mute")
    punish(app, "vdm", "2")
    assert app.sent[-1][0].startswith("/mute 2 ")
    cfg._p_pick_kind(app.book.get("GR 6.1"), "perm")
    punish(app, "vdm", "2", enter_twice=True)
    assert app.sent[-1] == ["/block 2 9999 G.R 6.1"]
    cfg._p_pick_kind(app.book.get("GR 6.1"), "manual")
    punish(app, "vdm", "2")
    assert app.sent[-1] == ["/block 2 9999 G.R 6.1"] and "by hand" in app.d_msg.cget("text")


def test_picking_the_rulebook_value_removes_the_override(app, cfg):
    r = pick_rule(cfg, "car ram")
    cfg._p_save(r, "jail", 30)
    assert "GR 5.20" in app.s.overrides
    cfg._p_save(app.book.get("GR 5.20"), "jail", 45)
    assert "GR 5.20" not in app.s.overrides


def test_changed_filter(cfg, app):
    cfg._p_save(pick_rule(cfg, "pg"), "jail", 20)
    cfg.p_q.set("")
    cfg._p_set_filter("changed")
    assert [r.id for r in cfg.p_rules] == ["GR 7.1"]


def test_quick_list_edits_reach_the_bar(app, cfg):
    cfg.go("quick")
    cfg._q_add_id("GR 7.1")
    assert app.s.favorites[-1] == "GR 7.1"
    cfg._q_move(len(app.s.favorites) - 1, -1)
    cfg._q_remove("GR 6.2")
    app.view_search(clear=True)
    settle(app)
    assert app.items[0].rule.id == "GR 6.1" and "GR 6.2" not in [i.rule.id for i in app.items]
    for rid in ("GR 6.4", "GR 6.5", "GR 6.7"):
        cfg._q_add_id(rid)
    assert len(app.s.favorites) == 9


def test_command_format_and_mode(app, cfg):
    cfg.go("commands")
    var = cfg.c_rows["jail"][0]
    var.set("jail {id} {time} {reason}")
    cfg._c_template("jail")
    app.root.update()
    punish(app, "rdm", "4")
    assert app.sent[-1] == ["/jail 4 60 G.R 6.2"]
    var.set("jail {who}")                          # invalid: not saved
    cfg._c_template("jail")
    assert app.s.commands["jail"] == "jail {id} {time} {reason}"
    cfg._c_mode("test")
    punish(app, "rdm", "4")
    assert app.sent[-1] == ["jail 4 60 G.R 6.2"]
    cfg._c_set("reason_style", "plain_name")
    punish(app, "rdm", "4")
    assert app.sent[-1] == ["jail 4 60 GR 6.2 RDM"]


def test_hotkey_capture_rules(app, cfg):
    cfg.go("shortcuts")
    cfg._start_capture("open_hotkey")
    cfg._end_capture("Alt+W")                      # same as hide
    assert app.s.open_hotkey == "Alt+Q" and "different" in cfg.k_msg.cget("text")
    cfg._start_capture("open_hotkey")
    cfg._end_capture("Q")                          # would block typing
    assert app.s.open_hotkey == "Alt+Q"
    cfg._start_capture("open_hotkey")
    cfg._end_capture("Ctrl+Space")
    assert app.s.open_hotkey == "Ctrl+Space"
    cfg._start_capture("chat_key")
    cfg._end_capture("y")
    assert app.s.chat_key == "y"


def test_reset_everything(app, cfg):
    cfg._p_save(pick_rule(cfg, "rdm"), "warn", None)
    cfg.go("general")
    cfg._g_reset()
    assert "GR 6.2" in app.s.overrides                 # first click only arms it
    cfg._g_reset()
    assert app.s.overrides == {} and app.s.favorites[0] == "GR 6.2"
