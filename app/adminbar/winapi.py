"""Win32 through ctypes: windows, focus, keyboard injection, the cursor, the clipboard, process info.

Nothing here keeps state; every function is safe to call from any thread.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time
from typing import Callable, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
Rect = Tuple[int, int, int, int]  # left, top, right, bottom

# ----------------------------------------------------------------------------- prototypes
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = (_WNDENUMPROC, wt.LPARAM)
user32.EnumWindows.restype = wt.BOOL
user32.IsWindow.argtypes = (wt.HWND,)
user32.IsWindowVisible.argtypes = (wt.HWND,)
user32.IsIconic.argtypes = (wt.HWND,)
user32.IsHungAppWindow.argtypes = (wt.HWND,)
user32.GetWindowTextLengthW.argtypes = (wt.HWND,)
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = (wt.HWND,)
user32.BringWindowToTop.argtypes = (wt.HWND,)
user32.ShowWindow.argtypes = (wt.HWND, ctypes.c_int)
user32.SwitchToThisWindow.argtypes = (wt.HWND, wt.BOOL)
user32.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
user32.AttachThreadInput.argtypes = (wt.DWORD, wt.DWORD, wt.BOOL)
user32.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
user32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
user32.ClientToScreen.argtypes = (wt.HWND, ctypes.POINTER(wt.POINT))
user32.GetParent.argtypes = (wt.HWND,)
user32.GetParent.restype = wt.HWND
user32.GetAncestor.argtypes = (wt.HWND, wt.UINT)
user32.GetAncestor.restype = wt.HWND
user32.GetWindowLongW.argtypes = (wt.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = wt.LONG
user32.SetWindowLongW.argtypes = (wt.HWND, ctypes.c_int, wt.LONG)
user32.SetWindowLongW.restype = wt.LONG
user32.MonitorFromWindow.argtypes = (wt.HWND, wt.DWORD)
user32.MonitorFromWindow.restype = wt.HANDLE
user32.MonitorFromPoint.argtypes = (wt.POINT, wt.DWORD)
user32.MonitorFromPoint.restype = wt.HANDLE
user32.GetCursorPos.argtypes = (ctypes.POINTER(wt.POINT),)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.GetClipCursor.argtypes = (ctypes.POINTER(wt.RECT),)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
user32.MapVirtualKeyW.restype = wt.UINT
user32.OpenClipboard.argtypes = (wt.HWND,)
user32.GetClipboardData.argtypes = (wt.UINT,)
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = (wt.UINT, wt.HANDLE)
user32.SetClipboardData.restype = wt.HANDLE
user32.IsClipboardFormatAvailable.argtypes = (wt.UINT,)
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = (wt.HANDLE,)
kernel32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
kernel32.GlobalAlloc.argtypes = (wt.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = (wt.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wt.HGLOBAL,)
kernel32.GlobalFree.argtypes = (wt.HGLOBAL,)
advapi32.OpenProcessToken.argtypes = (wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE))
advapi32.GetTokenInformation.argtypes = (wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD))


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("ptScreenPos", wt.POINT)]


user32.GetMonitorInfoW.argtypes = (wt.HANDLE, ctypes.POINTER(MONITORINFO))
user32.GetCursorInfo.argtypes = (ctypes.POINTER(CURSORINFO),)

# ----------------------------------------------------------------------------- DPI


def enable_dpi_awareness() -> str:
    """Make window coordinates real pixels (call before the first window is created)."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except (AttributeError, OSError):
        return "none"


# ----------------------------------------------------------------------------- windows


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsIconic(hwnd))


def is_hung(hwnd: int) -> bool:
    try:
        return bool(hwnd) and bool(user32.IsHungAppWindow(hwnd))
    except OSError:
        return False


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def top_level_windows() -> List[int]:
    out: List[int] = []

    def _cb(hwnd, _lp):
        out.append(int(hwnd))
        return True

    user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return out


def is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def window_rect(hwnd: int) -> Optional[Rect]:
    r = wt.RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def client_rect(hwnd: int) -> Optional[Rect]:
    r = wt.RECT()
    if not hwnd or not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wt.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return pt.x, pt.y, pt.x + (r.right - r.left), pt.y + (r.bottom - r.top)


def toplevel_of(widget_id: int) -> int:
    """The real top-level HWND that owns a Tk widget's window."""
    h = user32.GetAncestor(widget_id, 2)  # GA_ROOT
    return int(h or user32.GetParent(widget_id) or widget_id)


def work_area_for(hwnd: int = 0, point: Optional[Tuple[int, int]] = None) -> Rect:
    """The work area (screen minus taskbar) of the monitor holding a window or a point."""
    if hwnd:
        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    else:
        x, y = point or cursor_pos() or (0, 0)
        mon = user32.MonitorFromPoint(wt.POINT(x, y), 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def monitor_rect_for(hwnd: int) -> Optional[Rect]:
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcMonitor
        return r.left, r.top, r.right, r.bottom
    return None


GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008


def add_ex_style(hwnd: int, add: int, remove: int = 0) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style | add) & ~remove)


HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_FRAMECHANGED = 0x1, 0x2, 0x10, 0x40, 0x20


def set_topmost(hwnd: int, activate: bool = False) -> None:
    flags = SWP_NOSIZE | SWP_NOMOVE | (0 if activate else SWP_NOACTIVATE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)


def round_corners(hwnd: int) -> bool:
    """Windows 11 rounded corners (DWMWA_WINDOW_CORNER_PREFERENCE = ROUND). No-op on Windows 10."""
    try:
        dwm = ctypes.WinDLL("dwmapi")
        pref = ctypes.c_int(2)
        return dwm.DwmSetWindowAttribute(wt.HWND(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref)) == 0
    except (OSError, AttributeError):
        return False


# ----------------------------------------------------------------------------- focus

SW_RESTORE = 9


def _front(hwnd: int) -> bool:
    return foreground() == int(hwnd)


def bring_to_front(hwnd: int, sleep: Callable[[float], None] = time.sleep, settle: float = 0.12) -> bool:
    """Put ``hwnd`` in the foreground. Windows only lets the process that got the last input do that, so the
    call climbs a ladder of tricks: plain call, attach to the foreground thread, an ALT tap, Alt+Tab's own
    SwitchToThisWindow, a topmost flip. Returns True once the window is in front."""
    hwnd = int(hwnd or 0)
    if not is_window(hwnd):
        return False
    if is_minimized(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        sleep(0.15)
    if _front(hwnd):
        return True

    def settled() -> bool:
        deadline = time.monotonic() + settle
        while True:
            if _front(hwnd):
                return True
            if time.monotonic() >= deadline:
                return False
            sleep(0.02)

    user32.SetForegroundWindow(hwnd)
    if settled():
        return True
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(tid and tid != me and user32.AttachThreadInput(tid, me, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if settled():
            return True
        tap_vk(VK_MENU)  # the classic foreground unlock
        user32.SetForegroundWindow(hwnd)
        if settled():
            return True
    finally:
        if attached:
            user32.AttachThreadInput(tid, me, False)
    try:
        user32.SwitchToThisWindow(hwnd, True)
        if settled():
            return True
    except OSError:
        pass
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    user32.SetForegroundWindow(hwnd)
    return settled()


# ----------------------------------------------------------------------------- input

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x20, 0x40
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_RETURN = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x0D
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28), "INPUT layout is wrong"
user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT


class InputError(RuntimeError):
    pass


def _send(events: List[INPUT]) -> None:
    if not events:
        return
    arr = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise InputError(f"SendInput delivered {sent} of {len(events)} events (error {ctypes.get_last_error()})")


def _key(scan: int = 0, vk: int = 0, flags: int = 0) -> INPUT:
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


# US-position scan codes: games read keys by position, whatever the keyboard layout says
SCAN = {
    **{c: s for c, s in zip("qwertyuiop", range(0x10, 0x1A))},
    **{c: s for c, s in zip("asdfghjkl", range(0x1E, 0x27))},
    **{c: s for c, s in zip("zxcvbnm", range(0x2C, 0x33))},
    **{c: s for c, s in zip("1234567890", range(0x02, 0x0C))},
    "enter": 0x1C, "esc": 0x01, "backspace": 0x0E, "tab": 0x0F, "space": 0x39, "ctrl": 0x1D, "shift": 0x2A,
    "alt": 0x38, "`": 0x29, "-": 0x0C, "=": 0x0D, "[": 0x1A, "]": 0x1B, ";": 0x27, "'": 0x28, ",": 0x33,
    ".": 0x34, "/": 0x35, "\\": 0x2B,
    **{f"f{n}": s for n, s in zip(range(1, 11), range(0x3B, 0x45))}, "f11": 0x57, "f12": 0x58,
}


def scan_of(name: str) -> int:
    s = SCAN.get(name.strip().lower())
    if s is None:
        raise ValueError(f"unknown key {name!r}")
    return s


def tap_scan(scan: int, hold: float = 0.03, extended: bool = False) -> None:
    ext = KEYEVENTF_EXTENDEDKEY if extended else 0
    _send([_key(scan, 0, KEYEVENTF_SCANCODE | ext)])
    time.sleep(hold)
    _send([_key(scan, 0, KEYEVENTF_SCANCODE | ext | KEYEVENTF_KEYUP)])


def chord_scan(mod_scan: int, scan: int, hold: float = 0.03) -> None:
    """mod down, key down/up, mod up - e.g. Ctrl+A / Ctrl+V by scan code."""
    _send([_key(mod_scan, 0, KEYEVENTF_SCANCODE)])
    time.sleep(0.02)
    try:
        tap_scan(scan, hold)
    finally:
        time.sleep(0.02)
        _send([_key(mod_scan, 0, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)])


def tap_vk(vk: int) -> None:
    try:
        _send([_key(0, vk, 0), _key(0, vk, KEYEVENTF_KEYUP)])
    except InputError as exc:
        log.debug("vk tap failed: %s", exc)


def type_unicode(text: str, delay: float = 0.004) -> None:
    """Type text as Unicode characters (independent of the keyboard layout and of Shift/Caps Lock)."""
    for ch in text:
        units = ch.encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        _send([_key(c, 0, KEYEVENTF_UNICODE) for c in codes] +
              [_key(c, 0, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP) for c in codes])
        if delay:
            time.sleep(delay)


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


MODIFIERS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)


def held_keys(vks: Iterable[int]) -> List[int]:
    return [vk for vk in vks if key_down(vk)]


def release_modifiers() -> None:
    """Send key-ups for modifiers Windows still thinks are down (a stuck Alt/Shift breaks the chat typing)."""
    for vk in (VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN):
        if key_down(vk):
            try:
                _send([_key(0, vk, KEYEVENTF_KEYUP)])
            except InputError:
                pass


def middle_click() -> None:
    a, b = INPUT(), INPUT()
    a.type = b.type = INPUT_MOUSE
    a.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MIDDLEDOWN, 0, 0)
    b.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MIDDLEUP, 0, 0)
    _send([a])
    time.sleep(0.04)
    _send([b])


# ----------------------------------------------------------------------------- cursor


def cursor_pos() -> Optional[Tuple[int, int]]:
    pt = wt.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return pt.x, pt.y


def set_cursor_pos(x: int, y: int) -> bool:
    return bool(user32.SetCursorPos(int(x), int(y)))


def cursor_visible() -> Optional[bool]:
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return None
    return bool(ci.flags & 1)


def clip_rect() -> Optional[Rect]:
    r = wt.RECT()
    if not user32.GetClipCursor(ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def inside(inner: Optional[Rect], outer: Optional[Rect], tol: int = 2) -> bool:
    if not inner or not outer:
        return False
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and
            inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def game_holds_mouse(hwnd: int) -> Optional[bool]:
    """True when the game has captured the mouse (cursor hidden and clipped to its window)."""
    if foreground() != hwnd:
        return None
    vis = cursor_visible()
    if vis is None:
        return None
    return (not vis) and inside(clip_rect(), window_rect(hwnd))


# ----------------------------------------------------------------------------- processes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008


def process_image_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(h)


def _token_elevated(proc_handle: int) -> Optional[bool]:
    tok = wt.HANDLE()
    if not advapi32.OpenProcessToken(proc_handle, TOKEN_QUERY, ctypes.byref(tok)):
        return None
    try:
        val, size = wt.DWORD(0), wt.DWORD(0)
        if not advapi32.GetTokenInformation(tok, 20, ctypes.byref(val), 4, ctypes.byref(size)):  # TokenElevation
            return None
        return bool(val.value)
    finally:
        kernel32.CloseHandle(tok)


def self_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def process_elevated(pid: int) -> Optional[bool]:
    """True / False, or None when Windows will not tell (an anti-cheat can block the query)."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        return _token_elevated(h)
    finally:
        kernel32.CloseHandle(h)


# ----------------------------------------------------------------------------- clipboard

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _open_clipboard(tries: int = 10) -> bool:
    for _ in range(tries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard_text() -> Optional[str]:
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT) or not _open_clipboard():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(p, ctypes.addressof(data), size)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True  # the system owns h now
    finally:
        user32.CloseClipboard()


# ----------------------------------------------------------------------------- single instance

ERROR_ALREADY_EXISTS = 183
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR)
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.OpenEventW.argtypes = (wt.DWORD, wt.BOOL, wt.LPCWSTR)
kernel32.OpenEventW.restype = wt.HANDLE
kernel32.SetEvent.argtypes = (wt.HANDLE,)
kernel32.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)


def create_mutex(name: str) -> Tuple[int, bool]:
    """(handle, already_existed)."""
    h = kernel32.CreateMutexW(None, False, name)
    return int(h or 0), ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def create_event(name: str) -> int:
    return int(kernel32.CreateEventW(None, False, False, name) or 0)  # auto-reset


def signal_event(name: str) -> bool:
    h = kernel32.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
    if not h:
        return False
    try:
        return bool(kernel32.SetEvent(h))
    finally:
        kernel32.CloseHandle(h)


kernel32.OpenMutexW.argtypes = (wt.DWORD, wt.BOOL, wt.LPCWSTR)
kernel32.OpenMutexW.restype = wt.HANDLE


def mutex_exists(name: str) -> bool:
    h = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


def message_box(text: str, title: str, error: bool = False) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    except OSError:
        pass
