"""The punishment log: one line per command the bar typed into the game.

    %APPDATA%\\AdminBar\\punishments.log      (plain text, tab separated, opens in Notepad / Excel)

    2026-11-12 23:41:05   LIVE   12   G.R 6.2   RDM (Random Deathmatch)   Jail 60 min   /jail 12 60 1 G.R 6.2
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .settings import data_dir

log = logging.getLogger(__name__)
HEADER = "# Admin Bar punishment log\ttime\tmode\tplayer\trule\trule name\tpunishment\tcommand\n"
_lock = threading.Lock()


@dataclass
class Entry:
    time: str
    mode: str          # LIVE | TEST
    player: str
    rule: str          # "G.R 6.2" or "-" for a typed command
    name: str
    what: str          # "Jail 60 min", "unjail" ...
    command: str


def path() -> Path:
    return data_dir() / "punishments.log"


def _clean(s: str) -> str:
    return " ".join(str(s).replace("\t", " ").split())


def record(mode: str, player: str, rule: str, name: str, what: str, command: str,
           when: Optional[datetime] = None) -> None:
    line = "\t".join(_clean(x) for x in ((when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"), mode,
                                         player or "-", rule or "-", name or "-", what or "-", command))
    p = path()
    with _lock:
        try:
            new = not p.exists() or p.stat().st_size == 0
            with open(p, "a", encoding="utf-8", newline="\n") as f:
                if new:
                    f.write(HEADER)
                f.write(line + "\n")
        except OSError as exc:
            log.error("could not write the punishment log: %s", exc)


def read(limit: int = 1000) -> List[Entry]:
    """The newest entries first."""
    p = path()
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 512 * 1024))  # the tail is plenty
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: List[Entry] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        out.append(Entry(*parts))
    out.reverse()
    return out[:limit]


def ensure_file() -> Path:
    p = path()
    if not p.exists():
        with _lock:
            p.write_text(HEADER, encoding="utf-8")
    return p
