"""Global hotkeys (RegisterHotKey) on their own thread with a Win32 message loop.

RegisterHotKey is not a keyboard hook: Windows never drops it when the game restarts, it works while a game
is in front and it costs nothing. The same thread also waits on the named events a second launch uses to
say "show yourself" / "quit".
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import queue
import threading
from typing import Callable, Dict, Optional, Tuple

from . import winapi

log = logging.getLogger(__name__)

user32 = winapi.user32
kernel32 = winapi.kernel32

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_APP, WM_QUIT = 0x0312, 0x8000, 0x0012
PM_REMOVE, QS_ALLINPUT = 0x1, 0x04FF
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102

user32.RegisterHotKey.argtypes = (wt.HWND, ctypes.c_int, wt.UINT, wt.UINT)
user32.UnregisterHotKey.argtypes = (wt.HWND, ctypes.c_int)
user32.PostThreadMessageW.argtypes = (wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.PeekMessageW.argtypes = (ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT)
user32.MsgWaitForMultipleObjects.argtypes = (wt.DWORD, ctypes.POINTER(wt.HANDLE), wt.BOOL, wt.DWORD, wt.DWORD)
user32.MsgWaitForMultipleObjects.restype = wt.DWORD

KEYS: Dict[str, int] = {
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"F{n}": 0x6F + n for n in range(1, 25)},
    **{f"NUM{d}": 0x60 + d for d in range(10)},
    "SPACE": 0x20, "TAB": 0x09, "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "PAUSE": 0x13, "SCROLLLOCK": 0x91, "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
ALIASES = {"INS": "INSERT", "DEL": "DELETE", "PGUP": "PAGEUP", "PGDN": "PAGEDOWN", "PAGE UP": "PAGEUP",
           "PAGE DOWN": "PAGEDOWN", "SCROLL": "SCROLLLOCK", "~": "`", "GRAVE": "`", "BACKQUOTE": "`",
           "NUMPAD0": "NUM0", "NUMPAD1": "NUM1", "NUMPAD2": "NUM2", "NUMPAD3": "NUM3", "NUMPAD4": "NUM4",
           "NUMPAD5": "NUM5", "NUMPAD6": "NUM6", "NUMPAD7": "NUM7", "NUMPAD8": "NUM8", "NUMPAD9": "NUM9"}
MODS = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT, "WIN": MOD_WIN}
PRETTY = {"SPACE": "Space", "TAB": "Tab", "INSERT": "Insert", "DELETE": "Delete", "HOME": "Home", "END": "End",
          "PAGEUP": "PageUp", "PAGEDOWN": "PageDown", "UP": "Up", "DOWN": "Down", "LEFT": "Left",
          "RIGHT": "Right", "PAUSE": "Pause", "SCROLLLOCK": "ScrollLock",
          **{f"NUM{d}": f"Num{d}" for d in range(10)}}
MOD_ORDER = [("Ctrl", MOD_CONTROL), ("Alt", MOD_ALT), ("Shift", MOD_SHIFT), ("Win", MOD_WIN)]
ALONE_OK = {"INSERT", "DELETE", "HOME", "END", "PAGEUP", "PAGEDOWN", "PAUSE", "SCROLLLOCK"} | \
    {f"F{n}" for n in range(1, 25)}
RESERVED = {(MOD_ALT, 0x09), (MOD_ALT, 0x73), (MOD_ALT, 0x20), (MOD_CONTROL | MOD_ALT, 0x2E),
            (MOD_WIN, ord("L")), (MOD_WIN, ord("D")), (MOD_WIN, ord("R")), (MOD_WIN, ord("E")),
            (MOD_WIN, 0x09)}


def parse_hotkey(text: str) -> Tuple[int, int, str]:
    """'alt+q' -> (MOD_ALT, VK_Q, 'Alt+Q'). Raises ValueError with a message a person can read."""
    parts = [p.strip() for p in (text or "").replace(" + ", "+").split("+")]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("Empty shortcut")
    mods = 0
    key: Optional[str] = None
    for p in parts:
        up = p.upper()
        if up in MODS:
            mods |= MODS[up]
            continue
        up = ALIASES.get(up, up)
        if up not in KEYS:
            raise ValueError(f"Unknown key '{p}'")
        if key is not None:
            raise ValueError("Use one key plus modifiers (Ctrl / Alt / Shift)")
        key = up
    if key is None:
        raise ValueError("Add a key to the modifier (e.g. Alt+Q)")
    if not mods and key not in ALONE_OK:
        raise ValueError(f"'{key}' alone would block typing - add Ctrl, Alt or Shift")
    if mods == MOD_SHIFT and key not in ALONE_OK:
        raise ValueError("Shift + a letter types a capital - add Ctrl or Alt")
    vk = KEYS[key]
    if (mods, vk) in RESERVED:
        raise ValueError("Windows uses that shortcut itself")
    name = "+".join([n for n, m in MOD_ORDER if mods & m] + [PRETTY.get(key, key)])
    return mods, vk, name


def normalize(text: str) -> str:
    return parse_hotkey(text)[2]


class HotkeyService:
    """Hotkeys + the cross-process show/quit events, all on one thread.

    ``on_event(name)`` is called on that thread with a binding name ('open', 'hide') or 'show' / 'quit'."""

    def __init__(self, on_event: Callable[[str], None], show_event: str, quit_event: str) -> None:
        self._on_event = on_event
        self._show_name, self._quit_name = show_event, quit_event
        self._requests: "queue.Queue[Tuple[str, object, threading.Event, dict]]" = queue.Queue()
        self._ready = threading.Event()
        self._tid = 0
        self._ids: Dict[int, str] = {}
        self._bound: Dict[str, Tuple[int, int]] = {}
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(3.0)

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _call(self, op: str, arg: object, timeout: float = 3.0) -> dict:
        done = threading.Event()
        out: dict = {}
        self._requests.put((op, arg, done, out))
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_APP, 0, 0)
        done.wait(timeout)
        return out

    def bind(self, bindings: Dict[str, str]) -> Dict[str, Optional[str]]:
        """Replace all bindings. Returns {name: None (ok) or an error message}."""
        return self._call("bind", dict(bindings)).get("result", {k: "hotkey thread not running" for k in bindings})

    def suspend(self, on: bool) -> None:
        """Release every hotkey (while a new shortcut is being recorded) / take them back."""
        self._call("suspend", on)

    # ------------------------------------------------------------------ thread
    def _register_all(self, bindings: Dict[str, str]) -> Dict[str, Optional[str]]:
        self._unregister_all()
        result: Dict[str, Optional[str]] = {}
        used: Dict[Tuple[int, int], str] = {}
        for i, (name, text) in enumerate(bindings.items(), start=1):
            try:
                mods, vk, pretty = parse_hotkey(text)
            except ValueError as exc:
                result[name] = str(exc)
                continue
            if (mods, vk) in used:
                result[name] = f"Same shortcut as '{used[(mods, vk)]}'"
                continue
            if not user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                result[name] = f"{pretty} is already used by another program"
                continue
            used[(mods, vk)] = name
            self._ids[i] = name
            self._bound[name] = (mods, vk)
            result[name] = None
        self._last = dict(bindings)
        return result

    def _unregister_all(self) -> None:
        for i in list(self._ids):
            user32.UnregisterHotKey(None, i)
        self._ids.clear()
        self._bound.clear()

    def _handle_requests(self) -> None:
        while True:
            try:
                op, arg, done, out = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                if op == "bind":
                    out["result"] = self._register_all(arg)  # type: ignore[arg-type]
                elif op == "suspend":
                    if arg:
                        self._unregister_all()
                    else:
                        self._register_all(getattr(self, "_last", {}))
            except Exception:
                log.exception("hotkey request %s failed", op)
            finally:
                done.set()

    def _emit(self, name: str) -> None:
        try:
            self._on_event(name)
        except Exception:
            log.exception("hotkey handler failed")

    def _run(self) -> None:
        msg = wt.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create this thread's message queue
        self._tid = kernel32.GetCurrentThreadId()
        show_h = winapi.create_event(self._show_name)
        quit_h = winapi.create_event(self._quit_name)
        handles = (wt.HANDLE * 2)(show_h, quit_h)
        self._ready.set()
        try:
            while True:
                r = user32.MsgWaitForMultipleObjects(2, handles, False, 1000, QS_ALLINPUT)
                if r == WAIT_OBJECT_0:
                    self._emit("show")
                elif r == WAIT_OBJECT_0 + 1:
                    self._emit("quit")
                elif r == WAIT_OBJECT_0 + 2:
                    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                        if msg.message == WM_QUIT:
                            return
                        if msg.message == WM_HOTKEY:
                            name = self._ids.get(int(msg.wParam))
                            if name:
                                self._emit(name)
                        elif msg.message == WM_APP:
                            self._handle_requests()
                self._handle_requests()  # a request posted before the queue existed
        finally:
            self._unregister_all()
            for h in (show_h, quit_h):
                if h:
                    kernel32.CloseHandle(h)
