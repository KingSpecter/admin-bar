"""Start-up: one instance only, logging, crash reporting.

    Admin Bar.pyw                 start (or, when it already runs, open its window)
    Admin Bar.pyw --background    start quietly (Windows sign-in)
    Admin Bar.pyw --settings      same as no argument
    Admin Bar.pyw --quit          stop the running Admin Bar
    Admin Bar.pyw --log           open the punishment log in Notepad
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading

from . import APP_NAME, VERSION, winapi

MUTEX = "Local\\AdminBar.Instance"


def setup_logging() -> None:
    from .settings import data_dir
    handler = logging.handlers.RotatingFileHandler(data_dir() / "adminbar.log", maxBytes=1_000_000, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    def excepthook(t, v, tb):
        logging.getLogger("crash").critical("unhandled error", exc_info=(t, v, tb))

    sys.excepthook = excepthook
    threading.excepthook = lambda a: logging.getLogger("crash").critical(
        "unhandled error in thread %s", a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def selfcheck() -> int:
    """Run by the installer on a new copy before it replaces the old one: everything loads, the rules parse,
    Tk's files are there. Opens no window, writes nothing, does not touch a running Admin Bar."""
    try:
        from pathlib import Path
        import _tkinter  # noqa: F401 - loads tcl86t.dll / tk86t.dll
        import tkinter
        lib = Path(tkinter.Tcl().eval("info library"))
        for f in (lib / "init.tcl", lib.parent / "tk8.6" / "tk.tcl", lib.parent / "tk8.6" / "ttk" / "ttk.tcl"):
            if not f.exists():
                raise RuntimeError(f"missing {f}")
        from . import config, game, hotkeys, logbook, sender, settings, theme, tray, ui  # noqa: F401
        from .rules import RuleBook
        from .search import SearchEngine
        book = RuleBook.load()
        if len(book.rules) < 100 or not SearchEngine(book).search("rdm").items:
            raise RuntimeError("the rules data is incomplete")
        if not (Path(__file__).parent / "data" / "icon.ico").exists():
            raise RuntimeError("missing icon.ico")
    except Exception as exc:  # noqa: BLE001 - reported to the installer
        print(f"selfcheck failed: {exc!r}")
        return 1
    print(f"{APP_NAME} {VERSION} selfcheck ok")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selfcheck" in argv:
        return selfcheck()
    from .ui import QUIT_EVENT, SHOW_EVENT
    if "--log" in argv:
        import os
        from .logbook import ensure_file
        try:
            os.startfile(str(ensure_file()))  # type: ignore[attr-defined]
        except OSError as exc:
            winapi.message_box(f"Could not open the punishment log:\n{exc}", APP_NAME, error=True)
        return 0
    if "--quit" in argv:
        if not winapi.signal_event(QUIT_EVENT) and "--silent" not in argv:
            winapi.message_box(f"{APP_NAME} is not running.", APP_NAME)
        return 0
    mutex, exists = winapi.create_mutex(MUTEX)
    if exists:
        if "--background" not in argv:
            winapi.signal_event(SHOW_EVENT)  # already running: open its window
        return 0
    winapi.enable_dpi_awareness()
    try:  # its own taskbar identity and icon, not Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AdminBar.App")
    except (OSError, AttributeError):
        pass
    try:
        setup_logging()
    except OSError:
        logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("adminbar")
    log.info("%s %s starting (python %s)", APP_NAME, VERSION, sys.version.split()[0])
    try:
        from .rules import RuleBook
        from .settings import Settings
        from .ui import App
        settings = Settings.load()
        book = RuleBook.load()
        # started by Windows at sign-in: stay quiet; started by you: open the window
        App(settings, book, background="--background" in argv).run()
    except Exception as exc:
        log.critical("could not start", exc_info=True)
        winapi.message_box(f"{APP_NAME} could not start:\n\n{exc}\n\nDetails: %APPDATA%\\AdminBar\\adminbar.log",
                           APP_NAME, error=True)
        return 1
    finally:
        log.info("stopped")
        if mutex:
            winapi.kernel32.CloseHandle(mutex)
    return 0
