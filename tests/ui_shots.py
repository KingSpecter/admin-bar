"""Developer check: render the bar's views OFF-SCREEN (never activated, the game keeps focus) and save PNGs.

    python -I adminbar/tests/ui_shots.py <out_dir>
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
os.environ["ADMINBAR_HOME"] = tempfile.mkdtemp(prefix="adminbar-test-")

from adminbar import winapi  # noqa: E402

winapi.enable_dpi_awareness()

import tkinter as tk  # noqa: E402

from adminbar.rules import RuleBook  # noqa: E402
from adminbar.settings import Settings  # noqa: E402
from adminbar import ui  # noqa: E402
from adminbar.ui import App  # noqa: E402

# never take the focus from whatever is in front (a game may be running)
tk.Misc.focus_force = lambda self: None
winapi.bring_to_front = lambda *a, **k: False
ui.winapi.bring_to_front = winapi.bring_to_front

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
X, Y = -4000, 100


def png(path, w, h, bgra):
    rows = b"".join(b"\x00" + bytes(bgra[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    # BGRA -> RGBA
    arr = bytearray(rows)
    for y in range(h):
        base = y * (w * 4 + 1) + 1
        for x in range(w):
            i = base + x * 4
            arr[i], arr[i + 2] = arr[i + 2], arr[i]
            arr[i + 3] = 255

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(bytes(arr), 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(data)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(hwnd, path):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)
    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = (ctypes.c_ubyte * (w * h * 4))()
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    png(path, w, h, buf)
    print("saved", path, w, h)


def main():
    s = Settings.load()
    fg_before = winapi.foreground()
    app = App(s, RuleBook.load())
    app.root.update()
    app._release_startup_focus(fg_before)
    app.tracker.start()
    root = app.root
    hwnd = app.hwnd
    winapi.add_ex_style(hwnd, winapi.WS_EX_NOACTIVATE)

    def show_offscreen():
        app.visible = False  # keep the focus watchdog out of it
        app._x, app._y = X, Y
        app._update_status()
        app._fit()
        root.update_idletasks()
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        user32.SetWindowPos(hwnd, 0, X, Y, app.W, app._height, 0x10 | 0x4)  # NOACTIVATE | NOZORDER
        for _ in range(20):
            root.update()
            time.sleep(0.02)

    steps = [
        ("01-empty", lambda: app.view_search(clear=True)),
        ("02-rdm", lambda: app.qvar.set("rdm")),
        ("03-typo", lambda: app.qvar.set("fera rp")),
        ("04-half", lambda: app.qvar.set("comb")),
        ("05-situation", lambda: app.qvar.set("he ran me over then shot me without rp and logged off")),
        ("06-id", lambda: app.qvar.set("g.r 6")),
        ("07-cr", lambda: app.qvar.set("cr")),
        ("08-raw", lambda: app.qvar.set("unjail 12 wrong id")),
        ("09-detail-rdm", lambda: (app.qvar.set("rdm"), root.update(), app._run_search(), app.open_index(0),
                                   app.idvar.set("12"))),
        ("10-quick-cheats", lambda: (app.view_search(clear=True), root.update(), app._run_search(), app.quick(7),
                                     app.idvar.set("45"))),
        ("11-detail-options", lambda: (app.view_search(clear=True), app.qvar.set("gr 2.1"), root.update(),
                                       app._run_search(), app.open_index(0), app.idvar.set("7, 9"))),
        ("12-detail-manual", lambda: (app.view_search(clear=True), app.qvar.set("meta"), root.update(),
                                      app._run_search(), app.open_index(0))),
        ("14-nothing", lambda: (app.view_search(clear=True), app.qvar.set("xqzzv"))),
    ]
    for name, fn in steps:
        fn()
        root.update()
        if app._search_job:
            root.after_cancel(app._search_job)
            app._run_search()
        show_offscreen()
        grab(hwnd, OUT / f"{name}.png")
    # the config window, every page (shown without activation, off-screen)
    from adminbar.config import PAGES, ConfigWindow
    from adminbar import logbook
    for pid, rule, name, what in (("12", "G.R 6.2", "RDM (Random Deathmatch)", "Jail 60 min"),
                                  ("48", "G.R 6.8", "FearRP (fear for your life)", "Warn"),
                                  ("7", "G.R 2.1", "OOC insults", "Mute 180 min"),
                                  ("103", "G.R 3.1", "Cheats / advantage software", "Permanent ban")):
        logbook.record("LIVE", pid, rule, name, what, f"/x {pid} {rule}")
    cw = ConfigWindow(app)
    app.config_win = cw
    whwnd = cw.hwnd
    winapi.add_ex_style(whwnd, winapi.WS_EX_NOACTIVATE)
    cw.win.attributes("-alpha", 0.999)  # layered: Windows paints it even off-screen (screenshots only)
    cw.win.geometry(f"+{X}+{Y}")
    cw.win.deiconify()  # we are not the foreground process: Windows will not activate it
    user32.SetWindowPos(whwnd, 0, X, Y, 0, 0, 0x1 | 0x10 | 0x4)
    print("config shown; foreground unchanged:", winapi.foreground() == fg_before)
    for i, (page, *_r) in enumerate(PAGES):
        cw.go(page)
        if page == "punish":
            cw.p_q.set("ooc")
            cw._p_refresh()
        for _ in range(15):
            root.update()
            time.sleep(0.02)
        grab(whwnd, OUT / f"20-config-{i}-{page}.png")
    cw.go("punish")
    cw.p_q.set("rdm")
    cw._p_refresh()
    cw._p_pick_kind(app.book.get("GR 6.2"), "warn")
    for _ in range(15):
        root.update()
        time.sleep(0.02)
    grab(whwnd, OUT / "27-config-rdm-changed.png")
    cw._p_reset(app.book.get("GR 6.2"))
    app.toast("Sent  /jail 12 60 1 G.R 6.2", "good", 5000)
    root.update()
    user32.SetWindowPos(app.toast_hwnd, 0, X, Y + 600, 0, 0, 0x1 | 0x10 | 0x4)
    root.update()
    time.sleep(0.1)
    grab(app.toast_hwnd, OUT / "15-toast.png")
    fg_after = winapi.foreground()
    print("foreground unchanged:", fg_before == fg_after, fg_before, fg_after)
    app.tracker.stop()
    root.destroy()


if __name__ == "__main__":
    main()
