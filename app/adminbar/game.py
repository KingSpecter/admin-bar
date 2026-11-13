"""Finds the game window and keeps finding it: a game restart, a crash, a window that is rebuilt after a
resolution change or a second launch are all picked up within about a second.

The game is the largest visible window of class ``grcWindow`` owned by a GTA V / FiveM game process. When
Windows will not name the process (an anti-cheat can block that query) the window class alone decides.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from . import winapi

log = logging.getLogger(__name__)

POLL_S = 1.0


@dataclass(frozen=True)
class Game:
    hwnd: int
    pid: int
    process: str
    title: str

    @property
    def label(self) -> str:
        p = self.process.lower()
        if p.startswith("fivem"):
            return "FiveM"
        if "ragemp" in self.title.lower() or "rage multiplayer" in self.title.lower():
            return "RAGE MP"
        return "GTA V" if p else (self.title or "Game")


def find_game(process_regex: str, window_class: str) -> Optional[Game]:
    try:
        rx = re.compile(process_regex, re.I)
    except re.error:
        rx = re.compile(r"^(FiveM_b\d+_GTAProcess|FiveM_GTAProcess|GTA5)\.exe$", re.I)
    best = None
    best_area = -1
    for hwnd in winapi.top_level_windows():
        try:
            if not winapi.is_visible(hwnd):
                continue
            if window_class and winapi.window_class(hwnd) != window_class:
                continue
            pid = winapi.window_pid(hwnd)
            name = winapi.process_image_name(pid)
            if name and not rx.match(name):
                continue
            rect = winapi.client_rect(hwnd)
            area = (rect[2] - rect[0]) * (rect[3] - rect[1]) if rect else 0
            if area > best_area:
                best_area = area
                best = Game(hwnd, pid, name, winapi.window_text(hwnd))
        except OSError:
            continue
    return best


class GameTracker:
    """Polls in the background. ``on_change(game, why)`` runs on the tracker thread with why in
    found | restarted | window-changed | lost."""

    def __init__(self, get_settings: Callable[[], object],
                 on_change: Optional[Callable[[Optional[Game], str], None]] = None) -> None:
        self._get_settings = get_settings
        self._on_change = on_change
        self._game: Optional[Game] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="game-tracker", daemon=True)
        self.restarts = 0
        self._tick = 0

    def start(self) -> None:
        self._refresh()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    @property
    def game(self) -> Optional[Game]:
        with self._lock:
            return self._game

    def current(self) -> Optional[Game]:
        """The game right now (checked, not cached): used right before sending."""
        g = self.game
        if g is not None and winapi.is_window(g.hwnd) and winapi.window_pid(g.hwnd) == g.pid:
            return g
        return self._refresh()

    def poke(self) -> None:
        self._wake.set()

    def _refresh(self) -> Optional[Game]:
        s = self._get_settings()
        try:
            found = find_game(getattr(s, "game_process", ""), getattr(s, "game_class", "grcWindow"))
        except Exception:  # never let a desktop oddity kill the tracker
            log.exception("game lookup failed")
            found = None
        with self._lock:
            old = self._game
            self._game = found
        why = ""
        if old is None and found is not None:
            why = "found"
        elif old is not None and found is None:
            why = "lost"
        elif old is not None and found is not None:
            if found.pid != old.pid:
                why = "restarted"
                self.restarts += 1
            elif found.hwnd != old.hwnd:
                why = "window-changed"
        if why:
            log.info("game %s: %s", why, found or old)
            if self._on_change:
                try:
                    self._on_change(found, why)
                except Exception:
                    log.exception("game change callback failed")
        return found

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(POLL_S)
            self._wake.clear()
            if self._stop.is_set():
                break
            self._tick += 1
            g = self.game
            if g is not None and winapi.is_window(g.hwnd) and winapi.window_pid(g.hwnd) == g.pid:
                # still alive: a full scan only every few seconds (a second game window, a relaunch)
                if self._tick % 5:
                    continue
            self._refresh()
