"""The rulebook (data/rules.json) and what each punishment turns into in game.

A rule's punishment text is read into ``Action`` options, in the order the rule lists them:

    jail   /jail [id] [minutes] [1/0] [reason]     "Refinement 60 Minutes", "Jail 30 Minutes"
    warn   /warn [id] [1/0] [reason]               "Warn", "Warning" (to the player)
    block  /block [id] [days] [reason]             "Ban 7 days", "Ban up to 7 days", "Permanent Ban"
    mute   /mute [id] [minutes] [reason]           "Mute 30 Minutes"
    manual nothing to send                          wipes, org warnings, disqualifications, Discord ...

The command shapes are templates the admin can edit (Settings.commands). The admin can also replace any
rule's default punishment (Settings.overrides): the custom one becomes the first option and the default,
the rulebook's own options stay one arrow key away.

Anything a command cannot do (an account wipe next to a ban, "repeated offenses ...") becomes an ``extra``
note shown under the rule.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DATA = Path(__file__).resolve().parent / "data" / "rules.json"

SENDABLE = ("jail", "warn", "block", "mute")
DEFAULT_TEMPLATES: Dict[str, str] = {
    "jail": "jail {id} {time} {flag} {reason}",
    "warn": "warn {id} {flag} {reason}",
    "block": "block {id} {time} {reason}",
    "mute": "mute {id} {time} {reason}",
}
PLACEHOLDERS = ("{id}", "{time}", "{flag}", "{reason}")


@dataclass(frozen=True)
class Action:
    kind: str                       # jail | warn | block | mute | manual
    label: str                      # "Jail 60 min", "Ban up to 7 days", "Mute 30 min"
    amount: Optional[int] = None    # minutes (jail, mute) or days (block)
    max_amount: Optional[int] = None
    perm: bool = False
    note: str = ""                  # "IC events", "repeat offense" ...
    custom: bool = False            # set by the admin in the config panel

    @property
    def sendable(self) -> bool:
        return self.kind in SENDABLE

    @property
    def unit(self) -> str:
        if self.kind == "block" and not self.perm:
            return "Days"
        return "Minutes" if self.kind in ("jail", "mute") else ""

    @property
    def needs_amount(self) -> bool:
        return self.kind in ("jail", "mute") or (self.kind == "block" and not self.perm)

    @property
    def needs_flag(self) -> bool:
        return self.kind in ("jail", "warn")


def action_label(kind: str, amount: Optional[int] = None, perm: bool = False) -> str:
    if kind == "jail":
        return f"Jail {amount} min" if amount else "Jail"
    if kind == "warn":
        return "Warn"
    if kind == "block":
        return "Permanent ban" if perm else (f"Ban {amount} days" if amount else "Ban")
    if kind == "mute":
        return f"Mute {amount} min" if amount else "Mute"
    return "By hand (no command)"


def make_action(kind: str, amount: Optional[int] = None, custom: bool = False) -> Action:
    """'perm' is a block without days; amounts below 1 are dropped."""
    perm = kind == "perm"
    k = "block" if perm else kind
    amt = amount if (amount and amount > 0 and k in ("jail", "block", "mute") and not perm) else None
    return Action(k, action_label(k, amt, perm), amt, perm=perm, custom=custom)


@dataclass
class Rule:
    id: str
    book: str
    num: str
    name: str
    short: str
    text: str
    punishment: str
    exception: str = ""
    note: str = ""
    section: str = ""
    keywords: List[str] = field(default_factory=list)
    see_also: List[str] = field(default_factory=list)
    context: Dict[str, str] = field(default_factory=dict)
    actions: List[Action] = field(default_factory=list)
    extras: List[str] = field(default_factory=list)
    base_actions: List[Action] = field(default_factory=list)   # what the rulebook says

    @property
    def sendable(self) -> List[Action]:
        return [a for a in self.actions if a.sendable]

    @property
    def overridden(self) -> bool:
        return bool(self.actions) and self.actions[0].custom

    @property
    def default_action(self) -> Optional[Action]:
        if self.overridden:
            return self.actions[0]
        s = self.sendable
        return s[0] if s else (self.actions[0] if self.actions else None)

    @property
    def base_default(self) -> Optional[Action]:
        s = [a for a in self.base_actions if a.sendable]
        return s[0] if s else (self.base_actions[0] if self.base_actions else None)

    def code(self, style: str = "dotted") -> str:
        """The rule as it goes into the reason: "G.R 6.2", "GR 6.2", "G.R 6.2 RDM" ..."""
        book = ".".join(self.book) if style.startswith("dotted") else self.book
        s = f"{book} {self.num}"
        if style.endswith("_name") and self.short:
            s += f" {self.short}"
        return s


@dataclass
class Book:
    prefix: str
    title: str


class RuleBook:
    def __init__(self, books: List[Book], rules: List[Rule]) -> None:
        self.books = books
        self.rules = rules
        self.by_id = {r.id: r for r in rules}

    @classmethod
    def load(cls, path: Path = DATA) -> "RuleBook":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        books = [Book(b["prefix"], b["title"]) for b in data["books"]]
        rules = []
        for r in data["rules"]:
            rule = Rule(
                id=r["id"], book=r["book"], num=r["num"], name=r["name"], short=r.get("short") or r["name"],
                text=r.get("text", ""), punishment=r.get("punishment", ""), exception=r.get("exception", ""),
                note=r.get("note", ""), section=r.get("section", ""), keywords=list(r.get("keywords", [])),
                see_also=list(r.get("see_also", [])), context=dict(r.get("context", {})),
            )
            rule.base_actions, rule.extras = parse_punishment(rule.id, rule.book, rule.punishment)
            rule.actions = list(rule.base_actions)
            rules.append(rule)
        return cls(books, rules)

    def get(self, rule_id: str) -> Optional[Rule]:
        return self.by_id.get(normalize_id(rule_id))

    def book_title(self, prefix: str) -> str:
        return next((b.title for b in self.books if b.prefix == prefix), prefix)

    def apply_overrides(self, overrides: Dict[str, dict]) -> None:
        """Put the admin's own default punishments in front (or restore the rulebook's)."""
        for r in self.rules:
            ov = overrides.get(r.id)
            if not ov:
                r.actions = list(r.base_actions)
                continue
            a = make_action(ov.get("kind", ""), ov.get("amount"), custom=True)
            same = (a.kind, a.amount, a.perm)
            r.actions = [a] + [b for b in r.base_actions if (b.kind, b.amount, b.perm) != same]


def normalize_id(text: str) -> str:
    """'g.r 6.2' / 'gr6.2' / ' GR  6.2 ' -> 'GR 6.2'."""
    s = re.sub(r"\s+", " ", text.strip().upper())
    m = re.match(r"^((?:[A-Z]\.?){2,3})\s*(.+)$", s)
    if not m:
        return s
    return f"{m.group(1).replace('.', '')} {m.group(2).strip()}"


# ----------------------------------------------------------------------------- punishment parsing

# rules whose punishment text needs a human reading (checked against the source wording)
OVERRIDES: Dict[str, Tuple[List[Action], List[str]]] = {
    "GR 3.2": ([Action("warn", "Warn")], ["Report every exploitable bug you find"]),
    "GR 3.3": ([Action("manual", "Remove the gains")], ["Money / inventory wipe, then punish by severity"]),
    "GR 6.9": ([Action("jail", "Kick + Jail 30 min", 30), Action("warn", "Warn", note="repeated offense")],
               ["Repeated offenses: a warning"]),
    "ER 1.3": ([Action("warn", "Warn", note="IC events"), Action("block", "Ban 14 days", 14, note="OOC events")],
               ["Bans are increased on repeated offences"]),
    "AR 1.12": ([Action("block", "Permanent blacklist", perm=True)], []),
    "AR 1.13": ([Action("warn", "Warn both accounts", note="send it for both IDs")], []),
    "CR T11": ([Action("jail", "Jail 60 min", 60)], ["Based on the violated server rule"]),
    "SR 3.5": ([Action("jail", "Jail 60 min", 60)], ["Plus the FailRP / FearRP punishment"]),
    "SR 1.12": ([Action("manual", "Verbal warning to the org")], ["Up to a formal warning"]),
    "SR 3.2": ([Action("manual", "Verbal warning to the org")], ["Up to a warning"]),
    "SR 2.5": ([Action("manual", "Verbal to the org")], ["Up to a warn to the org"]),
    "SR 1.13": ([Action("warn", "Warn the player")], ["Also warn the org"]),
    "CR 1.14": ([Action("warn", "Warn the hostage taker")], ["Also a Gang/Cartel warn"]),
    "ER 1.8": ([Action("warn", "Warn each player", note="everyone who took part")], []),
    "ER 1.15": ([Action("jail", "Jail 20 min", 20)], ["Whole gang breaking it: a Gang Warning"]),
    "ER 1.6": ([Action("manual", "Blocked by script")], ["Getting round the block = exploit (GR 3.2 / GR 3.3)"]),
    "ER 1.10": ([Action("manual", "Blocked by script")], ["Getting round the block = exploit (GR 3.2 / GR 3.3)"]),
    "GR 5.19": ([Action("manual", "Admin discretion")], []),
}

_ORG = re.compile(r"\b(org|organi[sz]ation|gang|clan|cartel|family)\b", re.I)
_SPLIT = re.compile(r"\s+/\s+|\s+or\s+|\s*\|\s*", re.I)
_ALSO = re.compile(r"\s*\+\s*|\s+and\s+(?=(?:account|wipe|loss|deletion|a wipe))|(?<=wipe)\s+and\s+", re.I)


def _cap(s: str) -> str:
    s = s.strip().rstrip(".").strip()
    return s[:1].upper() + s[1:] if s else s


def _piece(text: str, book: str) -> Tuple[Optional[Action], List[str]]:
    """One option of a punishment ('Ban up to 7 days', 'Refinement 60 Minutes', 'Warn to the org')."""
    t = text.strip().rstrip(".")
    low = t.lower()
    extras: List[str] = []
    m = re.search(r"\(([^)]*)\)", t)
    note = m.group(1).strip() if m else ""
    if note:
        t = (t[:m.start()] + t[m.end():]).strip()
        low = t.lower()
    if book == "DR":
        lab = "No punishment listed" if low == "not listed" else _cap(t)
        return Action("manual", f"{lab} (Discord)"), extras
    if "permanent" in low or "blacklist" in low:
        return Action("block", "Permanent ban", perm=True), extras
    m = re.search(r"ban\b[^0-9]*?(?:up to\s*)?(?:(\d+)\s*-\s*)?(\d+)\s*days?", low) or \
        re.search(r"()(\d+)\s*days?\s*ban", low)
    if m:
        days = int(m.group(2))
        low_d = int(m.group(1)) if m.group(1) else None
        if "up to" in low:
            return Action("block", f"Ban up to {days} days", days, max_amount=days, note=note), extras
        if low_d is not None:
            return Action("block", f"Ban {low_d}-{days} days", days, max_amount=days, note=note), extras
        return Action("block", f"Ban {days} days", days, note=note), extras
    m = re.search(r"(?:refinement|jail)\s*(\d+)", low)
    if m:
        mins = int(m.group(1))
        return Action("jail", f"Jail {mins} min", mins, note=note), extras
    m = re.search(r"\bmute\b\s*(\d+)?", low)
    if m:
        mins = int(m.group(1)) if m.group(1) else None
        return Action("mute", action_label("mute", mins), mins, note=note), extras
    if re.search(r"\bwarn(ing)?\b", low) and "verbal" not in low:
        if _ORG.search(low) and "player" not in low:
            return Action("manual", _cap(t)), extras
        return Action("warn", "Warn", note=note), extras
    if low in ("n/a", ""):
        return Action("manual", "Not a punishable rule"), extras
    if low == "not listed":
        return Action("manual", "No punishment listed - staff discretion"), extras
    return Action("manual", _cap(t)), extras


def parse_punishment(rule_id: str, book: str, text: str) -> Tuple[List[Action], List[str]]:
    if rule_id in OVERRIDES:
        acts, extras = OVERRIDES[rule_id]
        return list(acts), list(extras)
    actions: List[Action] = []
    extras: List[str] = []
    for option in _SPLIT.split(text.strip()):
        parts = [p for p in _ALSO.split(option) if p.strip()]
        if not parts:
            continue
        acts = []
        for part in parts:
            act, more = _piece(part, book)
            extras += more
            if act is not None:
                acts.append((act, part))
        if not acts:
            continue
        main = next((a for a in acts if a[0].sendable), acts[0])
        actions.append(main[0])
        # "Ban 60 days and Account Wipe": the ban is the option, the wipe is done by hand next to it
        extras += [a.label if a.sendable else _cap(p) for a, p in acts if a is not main[0]]
    seen = set()
    uniq = []
    for a in actions:
        key = (a.kind, a.label, a.amount, a.perm)
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    return uniq or [Action("manual", _cap(text) or "No punishment listed")], extras


# ----------------------------------------------------------------------------- commands


def check_template(text: str) -> str:
    """'' when a command template is usable, otherwise what is wrong with it."""
    t = text.strip().lstrip("/")
    if not t:
        return "Empty"
    if "{id}" not in t:
        return "Needs {id}"
    for p in re.findall(r"\{[^}]*\}", t):
        if p not in PLACEHOLDERS:
            return f"Unknown {p} (use {', '.join(PLACEHOLDERS)})"
    return ""


def format_command(action: Action, player_id: str, reason: str, amount: Optional[int], flag: str,
                   perm_days: int, templates: Optional[Dict[str, str]] = None) -> str:
    """The command without the leading slash, from the admin's template for that kind."""
    if not action.sendable:
        raise ValueError(f"{action.kind} has no command")
    tpl = (templates or {}).get(action.kind) or DEFAULT_TEMPLATES[action.kind]
    if check_template(tpl):
        tpl = DEFAULT_TEMPLATES[action.kind]
    if action.kind == "block" and action.perm:
        time_ = perm_days
    else:
        time_ = amount if amount is not None else action.amount
    out = tpl.strip().lstrip("/")
    for key, val in (("{id}", player_id), ("{time}", "" if time_ is None else str(time_)), ("{flag}", flag),
                     ("{reason}", reason)):
        out = out.replace(key, val)
    return " ".join(out.split())


RAW_COMMANDS = {
    # name: (argument pattern after the id, help)
    "jail": (r"\d+\s+[01]\s+\S.*", "jail [id] [minutes] [1/0] [reason]"),
    "unjail": (r"\S.*", "unjail [id] [reason]"),
    "warn": (r"[01]\s+\S.*", "warn [id] [1/0] [reason]"),
    "unwarn": (r"\S.*", "unwarn [id] [reason]"),
    "block": (r"\d+\s+\S.*", "block [id] [days] [reason]"),
    "unblock": (r"\S.*", "unblock [id] [reason]"),
    "mute": (r"\d+\s+\S.*", "mute [id] [minutes] [reason]"),
    "unmute": (r"\S.*", "unmute [id] [reason]"),
}


def parse_raw(text: str) -> Optional[Tuple[str, str, bool]]:
    """A typed command ('/unjail 12 wrong id') -> (command name, command without slash, complete?).
    None when the text is not a command."""
    s = " ".join(text.strip().split())
    if s.startswith("/"):
        s = s[1:]
    m = re.match(r"^(unjail|unwarn|unblock|unmute|jail|warn|block|mute)\b\s*(.*)$", s, re.I)
    if not m:
        return None
    name, rest = m.group(1).lower(), m.group(2)
    if not text.strip().startswith("/") and not re.match(r"^\d", rest):
        return None  # "warn" alone is a search word, "warn 12 ..." is a command
    pattern, _ = RAW_COMMANDS[name]
    complete = bool(re.match(rf"^\d{{1,7}}\s+{pattern}$", rest))
    return name, f"{name} {rest}".strip(), complete


def finalize(command: str, test_mode: bool) -> str:
    """Live: '/jail ...'. Test mode: the same text without the slash (it lands in chat as a message)."""
    c = command.strip().lstrip("/")
    return c if test_mode else "/" + c


def sanitize_reason(reason: str) -> str:
    return " ".join(reason.replace("\r", " ").replace("\n", " ").split())[:120]
