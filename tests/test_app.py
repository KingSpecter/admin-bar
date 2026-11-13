import json
import tempfile

import pytest

from conftest import BOOK

from adminbar import hotkeys, sender, winapi
from adminbar.game import Game
from adminbar.rules import Action, finalize, format_command, normalize_id, parse_punishment, parse_raw
from adminbar.ui import digit_selects


# ---------------------------------------------------------------- commands
def cmd(rule_id, pid="12", amount=None, flag="1", style="dotted", perm_days=9999, test=True, action=None):
    r = BOOK.get(rule_id)
    a = action or r.default_action
    return finalize(format_command(a, pid, r.code(style), amount, flag, perm_days), test)


def test_command_shapes():
    assert cmd("GR 6.2") == "jail 12 60 1 G.R 6.2"
    assert cmd("GR 6.2", test=False) == "/jail 12 60 1 G.R 6.2"
    assert cmd("GR 6.8") == "warn 12 1 G.R 6.8"
    assert cmd("GR 6.8", flag="0") == "warn 12 0 G.R 6.8"
    assert cmd("GR 2.2") == "block 12 7 G.R 2.2"
    assert cmd("GR 3.1") == "block 12 9999 G.R 3.1"
    assert cmd("GR 3.1", perm_days=0) == "block 12 0 G.R 3.1"
    assert cmd("GR 5.20", amount=30) == "jail 12 30 1 G.R 5.20"
    assert cmd("CCR 1.5", action=Action("warn", "Warn")) == "warn 12 1 C.C.R 1.5"


@pytest.mark.parametrize("style,expected", [
    ("dotted", "G.R 6.2"), ("plain", "GR 6.2"), ("dotted_name", "G.R 6.2 RDM"), ("plain_name", "GR 6.2 RDM"),
])
def test_reason_styles(style, expected):
    assert BOOK.get("GR 6.2").code(style) == expected


def test_top_rules_have_the_right_command():
    want = {"GR 6.2": ("jail", 60), "GR 6.1": ("jail", 60), "GR 5.20": ("jail", 45), "GR 5.2": ("warn", None),
            "ZR 1.1": ("warn", None), "GR 6.8": ("warn", None), "GR 6.6": ("warn", None), "GR 3.1": ("block", None),
            "GR 6.4": ("jail", 25), "GR 7.1": ("warn", None), "GR 6.5": ("jail", 30)}
    for rid, (kind, amount) in want.items():
        a = BOOK.get(rid).default_action
        assert (a.kind, a.amount) == (kind, amount), rid
    assert BOOK.get("GR 3.1").default_action.perm


def test_every_rule_parses():
    for r in BOOK.rules:
        assert r.actions, r.id
        for a in r.actions:
            assert a.kind in ("jail", "warn", "block", "mute", "manual")
            if a.kind == "jail":
                assert a.amount and a.amount > 0, r.id
            if a.kind == "block" and not a.perm:
                assert a.amount and a.amount > 0, r.id


@pytest.mark.parametrize("text,kinds", [
    ("Mute 180 minutes or Ban up to 7 days (based on severity)", [("mute", 180), ("block", 7)]),
    ("Mute 30 Minutes", [("mute", 30)]),
    ("Ban 60 days and Account Wipe", [("block", 60)]),
    ("Account Wipe and 10 Day Ban", [("block", 10)]),
    ("Verbal to Gang / Refinement 20 Minutes", [("manual", None), ("jail", 20)]),
    ("Warn to the org", [("manual", None)]),
    ("Refinement 60 Minutes / Gang Warn", [("jail", 60), ("manual", None)]),
    ("30 day ban", [("block", 30)]),
    ("14 Day Ban.", [("block", 14)]),
    ("Ban 1-7 days (based on severity)", [("block", 7)]),
    ("Permanent Ban", [("block", None)]),
])
def test_punishment_parser(text, kinds):
    acts, _ = parse_punishment("X 1", "GR", text)
    assert [(a.kind, a.amount) for a in acts] == kinds


def test_discord_rules_never_send():
    for r in BOOK.rules:
        if r.book == "DR":
            assert not r.sendable, r.id


def test_extras_kept():
    assert "Account Wipe" in BOOK.get("AR 1.6").extras
    assert BOOK.get("GR 4.3").default_action.max_amount == 7


@pytest.mark.parametrize("text,expected", [
    ("/unjail 12 wrong id", ("unjail", "unjail 12 wrong id", True)),
    ("unjail 12 wrong id", ("unjail", "unjail 12 wrong id", True)),
    ("unwarn 5 G.R 6.8", ("unwarn", "unwarn 5 G.R 6.8", True)),
    ("unblock 33 appeal accepted", ("unblock", "unblock 33 appeal accepted", True)),
    ("jail 12 60 1 G.R 6.2", ("jail", "jail 12 60 1 G.R 6.2", True)),
    ("warn 12 1 G.R 6.8", ("warn", "warn 12 1 G.R 6.8", True)),
    ("block 12 7 G.R 2.2", ("block", "block 12 7 G.R 2.2", True)),
    ("unjail 12", ("unjail", "unjail 12", False)),
    ("jail 12 60", ("jail", "jail 12 60", False)),
    ("  /unjail   12   oops  ", ("unjail", "unjail 12 oops", True)),
])
def test_raw_commands(text, expected):
    assert parse_raw(text) == expected


@pytest.mark.parametrize("text", ["warn", "jail", "warning", "rdm", "jail time for rdm", "block list"])
def test_words_are_not_commands(text):
    assert parse_raw(text) is None


def test_finalize():
    assert finalize("/jail 1 2 1 x", True) == "jail 1 2 1 x"
    assert finalize("jail 1 2 1 x", False) == "/jail 1 2 1 x"


def test_normalize_id():
    assert normalize_id("g.r 6.2") == "GR 6.2"
    assert normalize_id("gr6.2") == "GR 6.2"
    assert normalize_id(" CCR  1.5 ") == "CCR 1.5"
    assert BOOK.get("c.r t4").id == "CR T4"


# ---------------------------------------------------------------- the digit rule
@pytest.mark.parametrize("query,n,visible,expected", [
    ("rdm", 1, 3, True), ("fear rp ", 2, 5, True), ("comb", 1, 1, True),
    ("", 1, 8, False),            # empty: typing a rule number
    ("6", 2, 8, False), ("6.", 2, 8, False), ("gr", 6, 8, False), ("gr ", 6, 8, False),
    ("g.r", 6, 8, False), ("cr t", 4, 8, False), ("t", 4, 8, False), ("ccr ", 1, 8, False),
    ("unjail ", 1, 1, False), ("jail ", 6, 3, False), ("/warn", 1, 1, False),
    ("rdm", 5, 3, False),         # no 5th result
    ("قية", 1, 2, True),
])
def test_digit_selects(query, n, visible, expected):
    assert digit_selects(query, n, visible) is expected


# ---------------------------------------------------------------- hotkeys
@pytest.mark.parametrize("text,pretty", [
    ("alt+q", "Alt+Q"), ("Ctrl + Shift + F", "Ctrl+Shift+F"), ("F10", "F10"), ("ctrl+space", "Ctrl+Space"),
    ("insert", "Insert"), ("Alt+`", "Alt+`"), ("shift+alt+1", "Alt+Shift+1"), ("ctrl+num5", "Ctrl+Num5"),
])
def test_hotkey_parse(text, pretty):
    assert hotkeys.parse_hotkey(text)[2] == pretty


@pytest.mark.parametrize("text", ["", "q", "Ctrl", "Alt+Tab", "Alt+F4", "Shift+Q", "Ctrl+Alt+Delete", "Alt+Q+W",
                                  "Alt+Banana", "space", "1"])
def test_hotkey_rejects(text):
    with pytest.raises(ValueError):
        hotkeys.parse_hotkey(text)


# ---------------------------------------------------------------- settings
def test_settings_recover(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("ADMINBAR_HOME", d)
    from adminbar.settings import Settings
    s = Settings.load()  # creates the file
    assert not s.test_mode and s.open_hotkey == "Alt+Q"
    p = Settings.path()
    p.write_text("{not json", encoding="utf-8")
    s = Settings.load()
    assert s.open_hotkey == "Alt+Q" and (p.parent / "settings.bad.json").exists()
    p.write_text(json.dumps({"config_version": 2, "jail_flag": "7", "perm_days": "abc", "chat_open_ms": 99999,
                             "test_mode": "yes",
                             "favorites": [], "unknown": 1, "reason_style": "weird", "chat_key": "??"}),
                 encoding="utf-8")
    s = Settings.load()
    assert s.jail_flag == "1" and s.perm_days == 9999 and s.chat_open_ms == 3000 and s.test_mode is False
    assert s.favorites and s.reason_style == "dotted" and s.chat_key == "t"


# ---------------------------------------------------------------- the sender (Windows faked: nothing is typed)
class FakeWin:
    def __init__(self, front=100, cursor_visible=False, elevated=False, hung=False, lose_focus_after=None):
        self.events = []
        self.front = front
        self.cv = cursor_visible
        self.elevated = elevated
        self.hung = hung
        self.lose_after = lose_focus_after
        self.taps = 0

    def install(self, mp):
        mp.setattr(winapi, "held_keys", lambda vks: [])
        mp.setattr(winapi, "is_window", lambda h: True)
        mp.setattr(winapi, "is_hung", lambda h: self.hung)
        mp.setattr(winapi, "is_minimized", lambda h: False)
        mp.setattr(winapi, "self_elevated", lambda: False)
        mp.setattr(winapi, "process_elevated", lambda pid: self.elevated)
        mp.setattr(winapi, "bring_to_front", lambda h, **k: self.events.append(("front", h)) or True)
        mp.setattr(winapi, "foreground", self._fg)
        mp.setattr(winapi, "client_rect", lambda h: (0, 0, 1920, 1080))
        mp.setattr(winapi, "cursor_pos", lambda: (500, 500))
        mp.setattr(winapi, "cursor_visible", lambda: self.cv)
        mp.setattr(winapi, "tap_scan", lambda s, *a, **k: self._tap(s))
        mp.setattr(winapi, "chord_scan", lambda m, s, *a, **k: self.events.append(("chord", m, s)))
        mp.setattr(winapi, "type_unicode", lambda t, d=0: self.events.append(("type", t)))
        mp.setattr(winapi, "game_holds_mouse", lambda h: True)
        mp.setattr(sender.time, "sleep", lambda s: None)

    def _fg(self):
        if self.lose_after is not None and self.taps >= self.lose_after:
            return 999
        return self.front

    def _tap(self, s):
        self.taps += 1
        self.events.append(("tap", s))


def settings():
    from adminbar.settings import Settings
    return Settings()


GAME = Game(100, 1, "FiveM_b3258_GTAProcess.exe", "FiveM")


def test_send_sequence(monkeypatch):
    w = FakeWin()
    w.install(monkeypatch)
    n = sender.send_lines(["jail 12 60 1 G.R 6.2", "jail 13 60 1 G.R 6.2"], GAME, settings())
    assert n == 2
    t, enter, ctrl, a = winapi.SCAN["t"], winapi.SCAN["enter"], winapi.SCAN["ctrl"], winapi.SCAN["a"]
    assert w.events == [("front", 100),
                        ("tap", t), ("chord", ctrl, a), ("type", "jail 12 60 1 G.R 6.2"), ("tap", enter),
                        ("tap", t), ("chord", ctrl, a), ("type", "jail 13 60 1 G.R 6.2"), ("tap", enter)]


def test_send_never_refuses_a_visible_cursor(monkeypatch):
    # 1.0 refused when the game showed a cursor (often the bar itself): now it just switches and types
    w = FakeWin(cursor_visible=True)
    w.install(monkeypatch)
    assert sender.send_lines(["warn 1 1 G.R 6.8"], GAME, settings()) == 1


def test_mouse_is_handed_back_before_typing(monkeypatch):
    w = FakeWin()
    w.install(monkeypatch)
    held = iter([False, False, False, False, False, False, True])
    monkeypatch.setattr(winapi, "game_holds_mouse", lambda h: next(held, True))
    monkeypatch.setattr(winapi, "set_cursor_pos", lambda x, y: w.events.append(("cursor", x, y)))
    monkeypatch.setattr(winapi, "middle_click", lambda: w.events.append(("click",)))
    sender.send_lines(["warn 1 1 G.R 6.8"], GAME, settings())
    kinds = [e[0] for e in w.events]
    assert kinds[:4] == ["front", "cursor", "click", "tap"], kinds


def test_send_refuses_elevated_and_hung(monkeypatch):
    FakeWin(elevated=True).install(monkeypatch)
    with pytest.raises(sender.SendError) as e:
        sender.send_lines(["x"], GAME, settings())
    assert e.value.code == "elevated"
    FakeWin(hung=True).install(monkeypatch)
    with pytest.raises(sender.SendError) as e:
        sender.send_lines(["x"], GAME, settings())
    assert e.value.code == "hung"


def test_send_stops_when_focus_is_lost(monkeypatch):
    w = FakeWin(lose_focus_after=3)  # after the first command's chat key + Enter + next chat key
    w.install(monkeypatch)
    with pytest.raises(sender.SendError) as e:
        sender.send_lines(["a", "b", "c"], GAME, settings())
    assert e.value.code == "lostfocus" and e.value.sent == 1


# ---------------------------------------------------------------- settings migration, overrides, formats
def test_old_test_mode_settings_go_live(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("ADMINBAR_HOME", d)
    from adminbar.settings import Settings
    Settings.path().write_text(json.dumps({"test_mode": True, "check_menu": True, "open_hotkey": "Alt+E"}),
                               encoding="utf-8")
    s = Settings.load()
    assert s.test_mode is False and s.open_hotkey == "Alt+E" and s.config_version == 2
    assert json.loads(Settings.path().read_text(encoding="utf-8"))["test_mode"] is False
    s.test_mode = True  # chosen again after the migration: kept
    s.save()
    assert Settings.load().test_mode is True


def test_ooc_insult_defaults_to_mute():
    a = BOOK.get("GR 2.1").default_action
    assert (a.kind, a.amount) == ("mute", 180)
    assert cmd("GR 2.1", test=False) == "/mute 12 180 G.R 2.1"
    assert cmd("GR 6.7") == "mute 12 30 G.R 6.7"


def test_overrides_apply_and_reset():
    from adminbar.rules import RuleBook
    book = RuleBook.load()
    book.apply_overrides({"GR 6.2": {"kind": "warn", "amount": None}, "GR 2.1": {"kind": "block", "amount": 3},
                          "ZR 1.1": {"kind": "perm"}, "GR 6.6": {"kind": "manual"}})
    r = book.get("GR 6.2")
    assert r.overridden and r.default_action.kind == "warn"
    assert [a.kind for a in r.actions] == ["warn", "jail"]          # the rulebook's jail stays one arrow away
    a = book.get("GR 2.1").default_action
    assert (a.kind, a.amount, a.custom) == ("block", 3, True)
    assert book.get("ZR 1.1").default_action.perm
    assert not book.get("GR 6.6").default_action.sendable           # 'by hand' really is the default
    book.apply_overrides({})
    assert book.get("GR 6.2").default_action.kind == "jail" and not book.get("GR 6.2").overridden


def test_settings_validate_overrides_and_templates(monkeypatch):
    monkeypatch.setenv("ADMINBAR_HOME", tempfile.mkdtemp())
    from adminbar.settings import Settings
    s = Settings(overrides={"gr 6.2": {"kind": "warn"}, "GR 6.1": {"kind": "nonsense"}, "GR 5.2": "x",
                            "GR 5.20": {"kind": "jail", "amount": "15"}},
                 commands={"jail": "/jail {id} {time} {reason}", "warn": "warn {player}", "mute": ""})
    s.validate()
    assert s.overrides == {"GR 6.2": {"kind": "warn", "amount": None}, "GR 5.20": {"kind": "jail", "amount": 15}}
    assert s.commands["jail"] == "jail {id} {time} {reason}"
    assert s.commands["warn"] == "warn {id} {flag} {reason}" and s.commands["mute"] == "mute {id} {time} {reason}"


def test_templates_shape_the_command():
    from adminbar.rules import check_template, make_action
    t = {"jail": "jail {id} {time} {reason}", "mute": "mute {id} {reason} {time}", "block": "ban {id} {time} {reason}"}
    assert format_command(make_action("jail", 30), "5", "G.R 6.2", None, "1", 9999, t) == "jail 5 30 G.R 6.2"
    assert format_command(make_action("mute", 15), "5", "G.R 6.7", None, "1", 9999, t) == "mute 5 G.R 6.7 15"
    assert format_command(make_action("perm"), "5", "G.R 3.1", None, "1", 0, t) == "ban 5 0 G.R 3.1"
    assert check_template("jail {id}") == "" and check_template("jail") and check_template("x {id} {who}")


@pytest.mark.parametrize("text,expected", [
    ("/mute 12 30 spam", ("mute", "mute 12 30 spam", True)),
    ("unmute 12 appeal", ("unmute", "unmute 12 appeal", True)),
    ("mute 12", ("mute", "mute 12", False)),
])
def test_mute_raw(text, expected):
    assert parse_raw(text) == expected
