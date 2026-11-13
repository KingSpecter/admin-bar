"""Typing a command into the game chat.

    1. wait until you let go of Enter / Shift / Ctrl / Alt (a held key would turn T into Alt+T)
    2. refuse a frozen game, or a game running as administrator when this app is not (Windows would
       silently drop every key)
    3. bring the game to the front (tried twice), give it a moment, and give it the mouse back if the
       switch left it loose (cursor into the window, one middle click) - the game is fully "in" before typing
    4. per command: chat key -> wait -> Ctrl+A (anything already in the chat box is replaced) -> the text
       -> Enter; the game must still be in front before every step
"""
from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional

from . import winapi
from .game import Game

log = logging.getLogger(__name__)


class SendError(Exception):
    def __init__(self, code: str, message: str, sent: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.sent = sent


HELD = (winapi.VK_SHIFT, winapi.VK_CONTROL, winapi.VK_MENU, winapi.VK_LWIN, winapi.VK_RWIN, winapi.VK_RETURN)


def wait_keys_released(timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while winapi.held_keys(HELD) and time.monotonic() < deadline:
        time.sleep(0.02)
    if winapi.held_keys(winapi.MODIFIERS):
        winapi.release_modifiers()
        time.sleep(0.05)


def check_target(game: Game) -> None:
    if not winapi.is_window(game.hwnd):
        raise SendError("nogame", "The game window is gone (closed or restarting).")
    if winapi.is_hung(game.hwnd):
        raise SendError("hung", "The game is not responding (loading or frozen). Try again in a moment.")
    if not winapi.self_elevated() and winapi.process_elevated(game.pid) is True:
        raise SendError("elevated", "The game runs as administrator, so Windows blocks the keys. "
                                    "Start Admin Bar as administrator too.")


def fix_mouse(game: Game) -> None:
    """After a focus switch FiveM can leave the mouse free (camera dead until you click). Put the cursor in
    the window and click the middle button once - a button the game does not shoot or aim with."""
    try:
        if winapi.foreground() != game.hwnd or winapi.game_holds_mouse(game.hwnd) is not False:
            return
        rect = winapi.client_rect(game.hwnd)
        if not rect:
            return
        # the cursor may still sit where the bar was: put it in the middle of the game first
        winapi.set_cursor_pos((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        for _ in range(4):
            time.sleep(0.05)
            if winapi.game_holds_mouse(game.hwnd):
                return
        winapi.middle_click()
        time.sleep(0.1)
        log.info("mouse given back to the game (held=%s)", winapi.game_holds_mouse(game.hwnd))
    except Exception:
        log.debug("mouse fix failed", exc_info=True)


def focus_game(game: Game, settle_ms: int) -> None:
    check_target(game)
    if not winapi.bring_to_front(game.hwnd):
        time.sleep(0.25)
        if not winapi.bring_to_front(game.hwnd):
            raise SendError("focus", "Windows did not let the game come to the front. Click the game once "
                                     "and try again.")
    time.sleep(settle_ms / 1000.0)
    if winapi.is_minimized(game.hwnd):
        raise SendError("fullscreen", "The game minimised itself (exclusive fullscreen). Set the game to "
                                      "Borderless / Windowed.")


def send_lines(lines: List[str], game: Game, settings, progress: Optional[Callable[[int], None]] = None) -> int:
    """Type every line into the game chat. Returns how many were sent; raises SendError (with .sent)."""
    s = settings
    wait_keys_released()
    focus_game(game, s.settle_ms)
    if s.fix_mouse:
        fix_mouse(game)
    chat_scan = winapi.scan_of(s.chat_key)
    ctrl, a_key, v_key = winapi.SCAN["ctrl"], winapi.SCAN["a"], winapi.SCAN["v"]
    saved_clip: Optional[str] = None
    if s.send_method == "paste":
        saved_clip = winapi.get_clipboard_text()
    sent = 0

    def still_front(step: str) -> None:
        if winapi.foreground() != game.hwnd:
            raise SendError("lostfocus", f"The game lost focus while {step}. Nothing more was sent.", sent)

    try:
        for i, line in enumerate(lines):
            if i:
                time.sleep(s.between_ms / 1000.0)
            still_front("opening the chat")
            winapi.tap_scan(chat_scan)
            time.sleep(s.chat_open_ms / 1000.0)
            still_front("typing")
            winapi.chord_scan(ctrl, a_key)
            time.sleep(0.03)
            if s.send_method == "paste":
                if not winapi.set_clipboard_text(line):
                    raise SendError("clipboard", "Could not use the clipboard.", sent)
                winapi.chord_scan(ctrl, v_key)
                time.sleep(0.12)
            else:
                winapi.type_unicode(line, s.key_delay_ms / 1000.0)
                time.sleep(0.08)
            still_front("typing")
            winapi.tap_scan(winapi.SCAN["enter"])
            sent += 1
            log.info("sent: %s", line)
            if progress:
                progress(sent)
        time.sleep(0.15)
    except winapi.InputError as exc:
        raise SendError("input", f"Windows refused the keys ({exc}).", sent) from exc
    finally:
        if saved_clip is not None:
            time.sleep(0.1)
            winapi.set_clipboard_text(saved_clip)
    return sent


def return_to(hwnd: int, game: Optional[Game], fix: bool) -> None:
    """Give the window you came from its focus back (and the game its mouse)."""
    if not winapi.is_window(hwnd):
        return
    wait_keys_released(1.0)
    if winapi.bring_to_front(hwnd) and fix and game is not None and hwnd == game.hwnd:
        time.sleep(0.15)
        fix_mouse(game)
