"""User settings: %APPDATA%\\AdminBar\\settings.json. Unknown keys are dropped, bad values fall back to the
default, a corrupt file is kept as settings.bad.json and replaced by the defaults."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Dict, List

log = logging.getLogger(__name__)

APP_DIR_NAME = "AdminBar"
CONFIG_VERSION = 2
DEFAULT_FAVORITES = ["GR 6.2", "GR 6.1", "GR 5.20", "GR 5.2", "ZR 1.1", "GR 6.8", "GR 6.6", "GR 3.1"]
REASON_STYLES = ("dotted", "plain", "dotted_name", "plain_name")
SEND_METHODS = ("type", "paste")
OVERRIDE_KINDS = ("jail", "warn", "block", "perm", "mute", "manual")


def data_dir() -> Path:
    override = os.environ.get("ADMINBAR_HOME")
    p = Path(override) if override else Path(os.environ.get("APPDATA") or str(Path.home())) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_templates() -> Dict[str, str]:
    from .rules import DEFAULT_TEMPLATES
    return dict(DEFAULT_TEMPLATES)


@dataclass
class Settings:
    config_version: int = CONFIG_VERSION
    open_hotkey: str = "Alt+Q"
    hide_hotkey: str = "Alt+W"
    test_mode: bool = False             # True: commands are sent without the leading "/" (plain chat text)
    jail_flag: str = "1"
    warn_flag: str = "1"
    perm_days: int = 9999               # what a permanent ban sends as [days]
    reason_style: str = "dotted"        # dotted "G.R 6.2", plain "GR 6.2", +_name adds the short name
    commands: Dict[str, str] = field(default_factory=default_templates)
    overrides: Dict[str, dict] = field(default_factory=dict)    # rule id -> {"kind": ..., "amount": ...}
    chat_key: str = "t"
    send_method: str = "type"           # type: Unicode keystrokes, paste: clipboard + Ctrl+V
    chat_open_ms: int = 350
    key_delay_ms: int = 4
    between_ms: int = 1100              # pause between two commands (several IDs)
    settle_ms: int = 300                # after the game comes to the front
    fix_mouse: bool = True              # give the game its mouse before typing / after returning to it
    return_focus: bool = True           # Esc / hide puts you back in the game
    confirm_bans: bool = True           # bans of 30+ days and permanent bans need a second Enter
    favorites: List[str] = field(default_factory=lambda: list(DEFAULT_FAVORITES))
    game_process: str = r"^(FiveM_b\d+_GTAProcess|FiveM_GTAProcess|GTA5|GTA5_Enhanced)\.exe$"
    game_class: str = "grcWindow"

    # ------------------------------------------------------------------ io
    @staticmethod
    def path() -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        s = cls()
        if not p.exists():
            s.save()
            return s
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            log.warning("settings unreadable (%s) - defaults used", exc)
            try:
                p.replace(p.with_name("settings.bad.json"))
            except OSError:
                pass
            s.save()
            return s
        defaults = cls()
        for f in fields(cls):
            if f.name in raw:
                setattr(s, f.name, _coerce(raw[f.name], getattr(defaults, f.name)))
        old = raw.get("config_version", 1)
        if not isinstance(old, int) or old < 2:
            # 1.0 shipped in test mode for the first in-game test; 1.1 is live
            s.test_mode = False
        s.config_version = CONFIG_VERSION
        s.validate()
        if old != CONFIG_VERSION:
            s.save()
        return s

    def save(self) -> None:
        p = self.path()
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as exc:
            log.error("could not save settings: %s", exc)

    def validate(self) -> None:
        from .rules import DEFAULT_TEMPLATES, check_template
        from .winapi import SCAN
        d = Settings()
        if self.jail_flag not in ("0", "1"):
            self.jail_flag = d.jail_flag
        if self.warn_flag not in ("0", "1"):
            self.warn_flag = d.warn_flag
        if self.reason_style not in REASON_STYLES:
            self.reason_style = d.reason_style
        if self.send_method not in SEND_METHODS:
            self.send_method = d.send_method
        self.chat_key = (self.chat_key or d.chat_key).strip().lower()
        if self.chat_key not in SCAN:
            self.chat_key = d.chat_key
        self.perm_days = _clamp(self.perm_days, 1, 99999, d.perm_days)
        self.chat_open_ms = _clamp(self.chat_open_ms, 50, 3000, d.chat_open_ms)
        self.key_delay_ms = _clamp(self.key_delay_ms, 0, 100, d.key_delay_ms)
        self.between_ms = _clamp(self.between_ms, 200, 10000, d.between_ms)
        self.settle_ms = _clamp(self.settle_ms, 0, 3000, d.settle_ms)
        cmds = {}
        for kind, tpl in DEFAULT_TEMPLATES.items():
            mine = str(self.commands.get(kind, "")).strip().lstrip("/")
            cmds[kind] = mine if mine and not check_template(mine) else tpl
        self.commands = cmds
        ovs = {}
        for rid, ov in self.overrides.items():
            if not isinstance(ov, dict) or ov.get("kind") not in OVERRIDE_KINDS:
                continue
            amount = ov.get("amount")
            amount = _clamp(amount, 1, 99999, 0) if amount not in (None, "") else None
            ovs[str(rid).upper()] = {"kind": ov["kind"], "amount": amount or None}
        self.overrides = ovs
        favs = []
        for x in self.favorites:
            x = str(x).strip().upper()
            if x and x not in favs:
                favs.append(x)
        self.favorites = favs[:9] or list(DEFAULT_FAVORITES)
        if not self.game_process.strip():
            self.game_process = d.game_process


def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _coerce(value, default):
    """Keep the default's type; a value of the wrong type is ignored."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        return [str(x) for x in value] if isinstance(value, list) else default
    if isinstance(default, dict):
        return dict(value) if isinstance(value, dict) else default
    return default


# ----------------------------------------------------------------------------- start with Windows

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AdminBar"


def launcher_command() -> str:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.exists():
        exe = pyw
    script = Path(__file__).resolve().parents[1] / "Admin Bar.pyw"
    return f'"{exe}" "{script}" --background'


def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> bool:
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, launcher_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log.error("autostart change failed: %s", exc)
        return False
