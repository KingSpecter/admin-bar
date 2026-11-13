"""The icon in the taskbar's notification area (bottom right), like every other running app.

    left click   open the Admin Bar window
    right click  menu: open the bar, settings, punishment log, start with Windows, test mode, quit

Plain Win32 (Shell_NotifyIcon) on its own thread with its own message loop. The icon comes back by itself
when Explorer restarts (TaskbarCreated) and keeps retrying while the taskbar is not ready yet (sign-in).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from typing import Callable, List, Optional, Tuple

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WM_DESTROY, WM_CLOSE, WM_TIMER, WM_NULL = 0x0002, 0x0010, 0x0113, 0x0000
WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x007B, 0x0203
WM_TRAY = 0x8000 + 5
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_NONOTIFY = 0x100, 0x2, 0x80
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
SM_CXSMICON, SM_CYSMICON = 49, 50
RETRY_TIMER = 7


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


user32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEXW),)
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = (wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
user32.CreateWindowExW.restype = wt.HWND
user32.DestroyWindow.argtypes = (wt.HWND,)
user32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.GetMessageW.argtypes = (ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
user32.TranslateMessage.argtypes = (ctypes.POINTER(wt.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wt.MSG),)
user32.RegisterWindowMessageW.argtypes = (wt.LPCWSTR,)
user32.RegisterWindowMessageW.restype = wt.UINT
user32.LoadImageW.argtypes = (wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT)
user32.LoadImageW.restype = wt.HANDLE
user32.DestroyIcon.argtypes = (wt.HICON,)
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = (wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
user32.SetMenuDefaultItem.argtypes = (wt.HMENU, wt.UINT, wt.UINT)
user32.TrackPopupMenu.argtypes = (wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID)
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = (wt.HMENU,)
user32.SetForegroundWindow.argtypes = (wt.HWND,)
user32.GetCursorPos.argtypes = (ctypes.POINTER(wt.POINT),)
user32.SetTimer.argtypes = (wt.HWND, ctypes.c_size_t, wt.UINT, wt.LPVOID)
user32.KillTimer.argtypes = (wt.HWND, ctypes.c_size_t)
shell32.Shell_NotifyIconW.argtypes = (wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
shell32.Shell_NotifyIconW.restype = wt.BOOL
kernel32.GetModuleHandleW.argtypes = (wt.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wt.HMODULE

MenuItem = Optional[Tuple[str, str, bool, bool]]  # (command, text, checked, enabled) or None = separator


class TrayIcon:
    def __init__(self, icon_path: str, tooltip: str, menu: Callable[[], List[MenuItem]],
                 on_command: Callable[[str], None], default: str = "config") -> None:
        self.icon_path = icon_path
        self.tooltip = tooltip
        self.menu = menu
        self.on_command = on_command
        self.default = default
        self.hwnd = 0
        self.hicon = 0
        self.added = False
        self._tries = 0
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)

    # ------------------------------------------------------------------ public (any thread)
    def start(self) -> None:
        self._thread.start()
        self._ready.wait(3.0)

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._thread.join(2.0)

    def set_tooltip(self, text: str) -> None:
        self.tooltip = text
        if self.added:
            self._notify(NIM_MODIFY)

    # ------------------------------------------------------------------ thread
    def _data(self) -> NOTIFYICONDATAW:
        d = NOTIFYICONDATAW()
        d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        d.hWnd = self.hwnd
        d.uID = 1
        d.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        d.uCallbackMessage = WM_TRAY
        d.hIcon = self.hicon
        d.szTip = self.tooltip[:127]
        return d

    def _notify(self, op: int) -> bool:
        d = self._data()
        return bool(shell32.Shell_NotifyIconW(op, ctypes.byref(d)))

    def _add(self) -> None:
        self.added = self._notify(NIM_ADD) or self._notify(NIM_MODIFY)
        if self.added:
            user32.KillTimer(self.hwnd, RETRY_TIMER)
            log.info("tray icon shown")
        elif self._tries < 60:  # the taskbar is not there yet (sign-in): try again every 2 s for 2 minutes
            self._tries += 1
            user32.SetTimer(self.hwnd, RETRY_TIMER, 2000, None)

    def _run(self) -> None:
        try:
            hinst = kernel32.GetModuleHandleW(None)
            cls = WNDCLASSEXW()
            cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
            cls.lpfnWndProc = self._proc
            cls.hInstance = hinst
            cls.lpszClassName = "AdminBarTrayWindow"
            user32.RegisterClassExW(ctypes.byref(cls))  # fails harmlessly if already registered
            self.hwnd = user32.CreateWindowExW(0, "AdminBarTrayWindow", "Admin Bar", 0, 0, 0, 0, 0,
                                               None, None, hinst, None) or 0
            self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            cx, cy = user32.GetSystemMetrics(SM_CXSMICON), user32.GetSystemMetrics(SM_CYSMICON)
            self.hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE) or \
                user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE) or 0
            if not self.hwnd:
                log.error("tray window could not be created")
                return
            self._add()
        finally:
            self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self.hicon:
            user32.DestroyIcon(self.hicon)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    self._fire(self.default)
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                return 0
            if msg == getattr(self, "taskbar_created", -1):  # Explorer restarted: the icon is gone, add it again
                self.added = False
                self._tries = 0
                self._add()
                return 0
            if msg == WM_TIMER and wparam == RETRY_TIMER:
                self._add()
                return 0
            if msg == WM_CLOSE:
                if self.added:
                    self._notify(NIM_DELETE)
                    self.added = False
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("tray message failed")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _fire(self, command: str) -> None:
        try:
            self.on_command(command)
        except Exception:
            log.exception("tray command failed")

    def _show_menu(self) -> None:
        items = self.menu()
        menu = user32.CreatePopupMenu()
        ids = {}
        for i, item in enumerate(items, start=1):
            if item is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            command, text, checked, enabled = item
            flags = MF_STRING | (MF_CHECKED if checked else 0) | (0 if enabled else MF_GRAYED)
            user32.AppendMenuW(menu, flags, i, text)
            ids[i] = command
            if command == self.default:
                user32.SetMenuDefaultItem(menu, i, 0)
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)  # so the menu closes when you click elsewhere
        chosen = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY, pt.x, pt.y, 0,
                                       self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if chosen in ids:
            self._fire(ids[chosen])
