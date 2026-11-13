"""Rule search: forgiving, ranked, fast enough to run on every keystroke.

    engine = SearchEngine(rulebook)
    res = engine.search("fera rp")      # -> res.items[0].rule.id == "GR 6.8"

What it understands
  * rule numbers in any spelling: "gr 6.2", "G.R 6.2", "gr6.2", "gr 62", "6,2", "6.2" (every book), "t4", "cr t4"
  * the slang admins use: rdm, vdm, cr (car ramming), cl (combat log), gz, frp, pg, ub, ubd, meta, nlr ...
  * typos: keyboard-aware (adjacent keys, swapped letters, doubled letters cost less): "fera rp", "rdn",
    "metagamign", "combat loging", "ghost peeek"
  * half-typed words: the last word is completed while you type ("comb" -> combat log)
  * an Arabic keyboard layout left on: "قيئ" is read as the keys it was typed on ("rdm")
  * whole sentences / pasted reports: every rule break found is listed first ("he rdm'd me then logged off")

Ranking: whole-query slang match > keyword phrases > words in the rule's name > keywords > rule text; the
rules admins hand out most (RDM, VDM, CR, CL, GZ, FearRP, FailRP, cheats, then UB, PG, meta ...) win ties.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .rules import Rule, RuleBook

# ----------------------------------------------------------------------------- what admins type

ALIASES: Dict[str, List[str]] = {
    "GR 6.2": ["rdm", "rdmed", "rdming", "random deathmatch", "random dm", "random kill", "kos", "rdm d"],
    "GR 6.1": ["vdm", "vdmed", "vdming", "vehicle deathmatch", "vehicle dm", "car dm", "ran over", "run over",
               "ran me over", "car kill"],
    "GR 5.20": ["cr", "car ram", "car ramming", "ramming", "ram", "rammed", "rammer", "ram car"],
    "GR 5.2": ["cl", "combat log", "combat logging", "combat logged", "combat logger", "logged off", "logged out",
               "log off", "alt f4", "f8 quit", "quit", "disconnect", "dc", "left the game", "left the server",
               "leaving rp", "left rp"],
    "ZR 1.1": ["gz", "sz", "shooting in gz", "shot in gz", "gz shooting", "killed in gz", "kill in gz",
               "shooting in green zone", "green zone shooting", "safe zone shooting", "shooting in safe zone",
               "shooting in sz", "gz kill", "gz rdm"],
    "GR 6.8": ["fear rp", "fearrp", "frp", "no fear rp", "nvl", "no value of life", "fear", "fear roleplay",
               "no fear"],
    "GR 6.6": ["fail rp", "failrp", "frp", "fail", "failing rp", "fail roleplay", "fail role play"],
    "GR 3.1": ["cheats", "cheat", "cheater", "cheating", "hacks", "hack", "hacker", "hacking", "hacked game",
               "aimbot", "aim bot", "esp", "wallhack", "wall hack", "mod menu", "modmenu", "modder", "modding",
               "godmode", "god mode", "noclip", "no clip", "speedhack", "speed hack", "executor", "injector",
               "lua executor", "trigger bot", "triggerbot", "silent aim", "magic bullet", "teleport hack",
               "menu", "third party software", "macro cheat"],
    "GR 7.1": ["pg", "power gaming", "powergaming", "power gamed", "power game", "powergame"],
    "GR 6.4": ["ub driving", "ubd", "ub drive", "ub driver", "unrealistic driving", "bad driving", "offroad",
               "off road"],
    "GR 6.5": ["ub", "unrealistic behaviour", "unrealistic behavior", "bunny hop", "bhop", "bunny hopping"],
    "GR 6.7": ["meta", "mg", "metagaming", "meta gaming", "metagamer", "stream snipe", "stream sniping",
               "stream sniper", "ghosting"],
    "GR 7.0": ["mixing", "mix", "ooc in ic", "ooc in rp", "ooc words in rp", "breaking character"],
    "GR 5.5": ["nlr", "new life rule", "revenge kill", "rk", "came back after death"],
    "GR 5.12": ["no initiation", "no init", "no rp initiation", "shot without rp", "didnt initiate",
                "no initiate", "without initiation", "initiation"],
    "GR 2.1": ["ooc insult", "insult", "insulting", "insulted", "ooc insulting", "flaming", "toxic ooc",
               "family insult"],
    "GR 2.2": ["n word", "nword", "f word", "fword", "slur", "racial slur", "racism", "racist",
               "homophobic slur"],
    "GR 2.5": ["ear rape", "earrape", "ear raping", "mic spam", "loud mic", "soundboard", "screaming mic"],
    "GR 3.2": ["bug abuse", "bug abusing", "glitch abuse", "vent shooting", "shooting through vents", "bug"],
    "GR 3.3": ["exploit", "exploiting", "exploiter", "dupe", "duping", "money glitch", "script exploit",
               "item dupe"],
    "GR 6.9": ["afk", "anti afk", "antiafk", "afk farming", "afk macro", "afk bot"],
    "GR 5.21": ["baiting", "bait", "baited"],
    "GR 5.19": ["glitch roll", "glitch rolling", "roll glitch", "rolling glitch"],
    "GR 5.6": ["teabag", "teabagging", "tbag", "t bag", "teabagged"],
    "GR 5.7": ["rape rp", "erp", "sexual rp"],
    "GR 5.10": ["mercy kill", "mercy killing", "finishing downed", "execute downed"],
    "GR 5.22": ["solo rob", "solo robbery", "rob alone", "robbing alone"],
    "GR 4.3": ["insult admin", "insulting admin", "admin insult", "disrespect admin", "disrespecting staff"],
    "GR 4.1": ["report abuse", "report spam", "false report", "fake report"],
    "GR 5.8": ["taking rp to discord", "rp to discord", "rp to ooc"],
    "GR 2.3": ["english only", "other language", "speaking arabic", "foreign language"],
    "AR 1.9": ["rmt", "real money trading", "selling money", "real money"],
    "AR 1.1": ["account sharing", "shared account", "selling account", "account selling"],
    "AR 1.10": ["fake admin", "impersonating staff", "impersonating admin", "staff impersonation"],
    "CR T9": ["ghost peek", "ghost peeking", "ghostpeek", "ghost peak"],
    "ZR 1.4": ["gz escape", "ran to gz", "ran into gz", "safe zone escape", "gz run", "running to gz"],
    "ZR 1.3": ["gz griefing", "griefing gz", "safe zone griefing"],
    "CCR 1.5": ["teaming", "alliance", "team up", "teamed"],
    "SR 1.8": ["stalling arrest", "stall arrest", "stalling"],
    "RR 2.1": ["tracers", "tracer"],
    "GR 5.18": ["starter job", "starter job worker"],
    "AR 1.2": ["got hacked", "account hacked", "hacked account", "acc hacked"],
}

# 3 = the rules handed out all day, 2 = very common, 1 = common
PRIORITY: Dict[str, int] = {
    **dict.fromkeys(["GR 6.2", "GR 6.1", "GR 5.20", "GR 5.2", "ZR 1.1", "GR 6.8", "GR 6.6", "GR 3.1"], 3),
    **dict.fromkeys(["GR 6.4", "GR 7.1", "GR 6.5", "GR 6.7", "GR 7.0", "GR 5.5", "GR 5.12", "GR 2.1", "GR 2.2",
                     "GR 3.2", "GR 3.3", "GR 6.9", "ZR 1.4", "GR 5.10"], 2),
    **dict.fromkeys(["GR 2.3", "GR 2.5", "GR 4.1", "GR 4.3", "GR 5.6", "GR 5.7", "GR 5.8", "GR 5.21", "GR 5.22",
                     "GR 5.13", "GR 5.11", "GR 5.18", "CR 1.11", "CR 1.10", "CR 1.8", "AR 1.9", "AR 1.7",
                     "CR T9", "ZR 1.2", "GR 5.19", "GR 5.15"], 1),
}

# an Arabic (101) layout left on: the key's English letter
ARABIC_KEYS = {
    "ض": "q", "ص": "w", "ث": "e", "ق": "r", "ف": "t", "غ": "y", "ع": "u", "ه": "i", "خ": "o", "ح": "p",
    "ج": "[", "د": "]", "ش": "a", "س": "s", "ي": "d", "ب": "f", "ل": "g", "ا": "h", "ت": "j", "ن": "k",
    "م": "l", "ك": ";", "ط": "'", "ئ": "z", "ء": "x", "ؤ": "c", "ر": "v", "ى": "n", "ة": "m", "و": ",",
    "ز": ".", "ظ": "/", "ذ": "`", "أ": "h", "إ": "h", "آ": "h", "لا": "b", "٠": "0", "١": "1", "٢": "2",
    "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9", "،": ",", "؟": "?", "؛": ";",
}
_ARABIC_RE = re.compile("[؀-ۿ]")


def transliterate(text: str) -> str:
    """Arabic letters typed on an Arabic layout -> the English keys they sit on."""
    if not _ARABIC_RE.search(text):
        return text
    text = text.replace("لا", "b")
    return "".join(ARABIC_KEYS.get(ch, ch) for ch in text)


# ----------------------------------------------------------------------------- text normalisation

COMPOUNDS = {
    "fearrp": "fear rp", "failrp": "fail rp", "powergaming": "power gaming", "powergame": "power game",
    "powergamed": "power gamed", "metagaming": "meta gaming", "metagame": "meta game", "metagamed": "meta gamed",
    "metagamer": "meta gamer", "ghostpeek": "ghost peek", "ghostpeeking": "ghost peeking",
    "ghostpeak": "ghost peek", "ghostpeaking": "ghost peeking", "earrape": "ear rape", "earraping": "ear raping",
    "antiafk": "anti afk", "bizwar": "biz war", "bizzwar": "biz war", "bizz": "biz", "combatlog": "combat log",
    "combatlogged": "combat logged", "combatlogging": "combat logging", "glitchroll": "glitch roll",
    "modmenu": "mod menu", "safezone": "safe zone", "safezones": "safe zones", "greenzone": "green zone",
    "greenzones": "green zones", "nword": "n word", "fword": "f word", "roleplay": "rp", "roleplaying": "rp",
    "roleplayed": "rp", "roleplays": "rp", "highground": "high ground", "multiaccount": "multi account",
    "multiaccounting": "multi account", "rdmed": "rdm", "rdming": "rdm", "rdmer": "rdm", "rdmd": "rdm",
    "vdmed": "vdm", "vdming": "vdm", "vdmd": "vdm", "speedhack": "speed hack", "wallhack": "wall hack",
    "aimlock": "aim lock", "triggerbot": "trigger bot", "autoclicker": "auto clicker", "bunnyhop": "bunny hop",
    "streamsniping": "stream sniping", "streamsnipe": "stream snipe", "pcheck": "pc check",
    "screenshare": "screen share", "crownholder": "crown holder", "gunstore": "gun store",
    "lawnmower": "lawn mower", "giftcard": "gift card", "cashapp": "cash app", "chargeback": "charge back",
    "chargebacks": "charge backs", "offroad": "off road", "dced": "disconnected", "logout": "log out",
    "logoff": "log off", "loggedoff": "logged off", "loggedout": "logged out", "alt+f4": "alt f4",
    "altf4": "alt f4", "godmode": "god mode", "noclip": "no clip", "teabagged": "teabag", "teabagging": "teabag",
    "teabaging": "teabag", "aimbotting": "aimbot", "carram": "car ram", "carramming": "car ramming",
    "ubdriving": "ub driving", "nlrd": "nlr", "aimboter": "aimbot",
}
CONTRACTIONS = re.compile(r"\b(did|do|does|was|were|is|are|could|would|should|has|have|had|need)nt\b")
DROP = set("""a an the me him her them us you someone somebody people players player guy guys dude bro
my his their our your theirs i he she they we hes shes theyre im ive myself himself herself themselves""".split())
SYN = {
    "cop": "leo", "cops": "leo", "police": "leo", "policeman": "leo", "officer": "leo", "officers": "leo",
    "leos": "leo", "lspd": "leo", "sapd": "leo", "sapf": "leo", "sheriff": "leo", "pd": "leo",
    "medic": "ems", "medics": "ems", "paramedic": "ems", "paramedics": "ems", "doctor": "ems", "doctors": "ems",
    "emt": "ems", "admins": "admin", "administrator": "admin", "administrators": "admin", "administration": "admin",
    "staff": "admin", "moderator": "admin", "moderators": "admin", "car": "vehicle", "cars": "vehicle",
    "vehicles": "vehicle", "veh": "vehicle", "guns": "gun", "weapon": "gun", "weapons": "gun", "firearm": "gun",
    "firearms": "gun", "pistol": "gun", "shot": "shoot", "shots": "shoot", "shooting": "shoot", "shoots": "shoot",
    "gunned": "shoot", "opened": "open", "killed": "kill", "kills": "kill", "killing": "kill", "murder": "kill",
    "murdered": "kill", "killer": "kill", "robbed": "rob", "robbing": "rob", "robbery": "rob", "robberies": "rob",
    "robber": "rob", "robbers": "rob", "robs": "rob", "mugged": "rob", "mug": "rob", "stole": "steal",
    "stolen": "steal", "stealing": "steal", "steals": "steal", "theft": "steal", "thief": "steal",
    "kidnapped": "kidnap", "kidnapping": "kidnap", "kidnaps": "kidnap", "kidnaped": "kidnap", "abduct": "kidnap",
    "abducted": "kidnap", "rammed": "ram", "ramming": "ram", "rams": "ram", "armour": "armor",
    "armoured": "armored", "colour": "color", "colours": "color", "colors": "color", "coloured": "color",
    "colored": "color", "behaviour": "behavior", "behaviours": "behavior", "tyre": "tire", "tyres": "tire",
    "tires": "tire", "logged": "log", "logging": "log", "logs": "log", "disconnected": "disconnect",
    "dc": "disconnect", "disconnecting": "disconnect", "green": "safe", "gz": "safe zone", "sz": "safe zone",
    "headquarters": "hq", "hostages": "hostage",
    "twink": "alt", "twinks": "alt", "alts": "alt", "alternate": "alt", "mil": "million", "millions": "million",
    "mins": "minute", "min": "minute", "minutes": "minute", "hrs": "hour", "hr": "hour", "hours": "hour",
    "spat": "spit", "ran": "run", "running": "run", "runs": "run", "drove": "drive", "driving": "drive",
    "drives": "drive", "driven": "drive", "driver": "drive", "drivers": "drive",
    "fled": "flee", "fleeing": "flee", "escaped": "escape", "escaping": "escape", "chased": "chase",
    "chasing": "chase", "pursuit": "chase", "provocation": "provoke", "insults": "insult", "cursed": "curse",
    "swore": "swear", "sworn": "swear", "said": "say", "says": "say", "saying": "say", "told": "tell",
    "telling": "tell", "took": "take", "taking": "take", "taken": "take", "gave": "give", "given": "give",
    "giving": "give", "sent": "send", "sending": "send", "came": "come", "coming": "come", "went": "go",
    "going": "go", "gone": "go", "left": "leave", "leaving": "leave", "quitting": "quit", "hit": "hit",
    "hitting": "hit", "hits": "hit", "bought": "buy", "buying": "buy", "sold": "sell", "selling": "sell",
    "sells": "sell", "money": "money", "cash": "money", "kicked": "kick", "dying": "die", "died": "die",
    "dead": "die", "death": "die", "downed": "down", "knocked": "down", "unconscious": "down", "injured": "down",
    "brutally": "", "randomly": "random", "racist": "racism", "rdms": "rdm",
    "used": "use", "using": "use", "uses": "use", "houses": "house", "interiors": "interior",
    "gov": "government", "govt": "government", "weekly": "week", "weeks": "week",
}
STOP = set("""
a an the and or of to in on at for by with from is are was were be been being it its this that these those
what whats which who whom how why when where do does did done can could should would will shall may might must
punishment punishments punish punished rule rules ruling penalty penalties sanction sanctions
give get got gets getting tell show find search check look lookup about if then than so just also only
very really please pls plz thanks thx ok okay like still even ever while because after before into onto
one some any every all again there here up out over during active time now today yesterday tonight
happen happened happens anyway later earlier ago whole literally basically
friend friends brother mate homie buddy
""".split())


def _prep(text: str) -> str:
    s = transliterate(text).lower()
    s = (s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
         .replace("–", "-").replace("—", "-"))
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)                  # 2,000,000 -> 2000000
    s = re.sub(r"\b(\d+)\s*(?:h|hr|hrs|hours?)\b", r"\1 hours", s)
    s = re.sub(r"n't\b", " not", s)
    s = s.replace("'", "")
    s = CONTRACTIONS.sub(r"\1 not", s)
    s = re.sub(r"\bcant\b|\bcannot\b", "can not", s)
    s = re.sub(r"\bwont\b", "will not", s)
    s = re.sub(r"\b(\w+(?:\+\w+)?)\b", lambda m: COMPOUNDS.get(m.group(1), m.group(1)), s)
    s = re.sub(r"\brole play", "rp", s)
    s = re.sub(r"\b(fear|fail)-rp\b", r"\1 rp", s)
    return s


TOKEN_RE = re.compile(r"/?[a-z0-9]+(?:\.[0-9]+)*")


def stem(t: str) -> str:
    if len(t) <= 3 or not t.isalpha():
        return t
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            if suf == "s" and t[-2] in "su":
                break
            if suf == "ed" and t[-3] == "e":
                break
            if suf == "es" and t[-3] not in "sxz" and not t.endswith(("ches", "shes")):
                t = t[:-1]
                break
            t = t[: -len(suf)]
            break
    if len(t) >= 4 and t[-1] == t[-2] and t[-1] not in "aeioulsfz":
        t = t[:-1]
    if len(t) >= 4 and t.endswith("e"):
        t = t[:-1]
    return t


@lru_cache(maxsize=50000)
def norm_word(w: str) -> str:
    w = SYN.get(w, w)
    if not w:
        return ""
    if " " in w:
        return " ".join(stem(x) for x in w.split())
    return stem(w)


def tokens(text: str) -> List[str]:
    """Normalised tokens (stop words kept: phrases need them)."""
    out: List[str] = []
    for raw in TOKEN_RE.findall(_prep(text)):
        if raw in DROP:
            continue
        n = norm_word(raw)
        if n:
            out.extend(n.split())
    return out


STOP_N = {norm_word(w) for w in STOP} | STOP


def content(toks: Iterable[str]) -> List[str]:
    return [t for t in toks if t not in STOP_N]


# ----------------------------------------------------------------------------- typo distance

_ROWS = [("1234567890-=", 0.0), ("qwertyuiop[]", 0.5), ("asdfghjkl;'", 0.75), ("zxcvbnm,./", 1.25)]
_KEYPOS = {c: (r, i + off) for r, (row, off) in enumerate(_ROWS) for i, c in enumerate(row)}


def _near(a: str, b: str) -> bool:
    pa, pb = _KEYPOS.get(a), _KEYPOS.get(b)
    return bool(pa and pb and abs(pa[0] - pb[0]) <= 1 and abs(pa[1] - pb[1]) <= 1.0)


def typo_cost(a: str, b: str) -> float:
    """Edit distance that knows typing: a neighbouring key, two swapped letters or a doubled / missing double
    letter cost 0.5, any other edit 1."""
    if a == b:
        return 0.0
    n, m = len(a), len(b)
    prev2: List[float] = []
    prev = [float(j) for j in range(m + 1)]
    for j in range(1, m + 1):
        prev[j] = prev[j - 1] + (0.5 if j > 1 and b[j - 1] == b[j - 2] else 1.0)
    for i in range(1, n + 1):
        cur = [0.0] * (m + 1)
        cur[0] = prev[0] + (0.5 if i > 1 and a[i - 1] == a[i - 2] else 1.0)
        for j in range(1, m + 1):
            ca, cb = a[i - 1], b[j - 1]
            sub = 0.0 if ca == cb else (0.5 if _near(ca, cb) else 1.0)
            dele = 0.5 if (i > 1 and ca == a[i - 2]) or (i < n and ca == a[i]) else 1.0
            ins = 0.5 if (j > 1 and cb == b[j - 2]) or (j < m and cb == b[j]) else 1.0
            v = min(prev[j - 1] + sub, prev[j] + dele, cur[j - 1] + ins)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, prev2[j - 2] + 0.5)
            cur[j] = v
        prev2, prev = prev, cur
    return prev[m]


def typo_budget(word: str) -> float:
    n = len(word)
    if n <= 4:
        return 0.5
    if n <= 6:
        return 1.0
    if n <= 9:
        return 1.5
    return 2.0


# ----------------------------------------------------------------------------- the index

CONTEXT_WORDS = {
    "ghetto": ["ghetto", "danger zone", "dz", "hood", "gang territory"],
    "event": ["event", "events", "biz war", "shipment", "ammo run", "crown holder", "circle", "gun store", "24/7",
              "bank heist", "bank robbery", "store robbery", "airdrop", "fz raid", "capture"],
    "turf": ["turf", "turfs", "turf war"],
    "safezone": ["safe zone", "green zone", "hospital", "business interior"],
    "hq": ["hq"],
    "leo": ["leo", "cop", "police", "officer", "fib", "sheriff"],
    "chase": ["chase", "chased", "chasing", "pursuit"],
}
CONTEXT_HOME = {
    "safezone": lambda r: r.book == "ZR",
    "turf": lambda r: r.id.startswith("CR T"),
    "event": lambda r: r.book in ("ER", "CCR"),
    "ghetto": lambda r: r.section == "Ghetto Rules",
    "hq": lambda r: " hq" in f" {r.name.lower()}",
    "leo": lambda r: False,
    "chase": lambda r: "chase" in r.name.lower(),
}
CONTEXT_BOOST = 4.0
CONTEXT_WEIGHT = 0.5
STRONG_IDF = 2.2
MIN_PICK = 11.0
ALIAS_WEIGHT = 1.6


@dataclass
class _Entry:
    rule: Rule
    name_t: Set[str]
    kw_t: Set[str]
    text_t: Set[str]
    phrases: List[Tuple[Tuple[str, ...], str, float]]   # tokens, source text, weight


@dataclass
class Match:
    rule: Rule
    score: float
    kind: str = "hit"              # id | alias | finding | allowed | hit | book | favorite
    matched: List[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.rule.id


@dataclass
class SearchResult:
    query: str
    mode: str                      # empty | id | book | punishment | alias | search | situation | none
    items: List[Match] = field(default_factory=list)
    corrections: Dict[str, str] = field(default_factory=dict)
    contexts: List[str] = field(default_factory=list)
    title: str = ""

    @property
    def best(self) -> Optional[Rule]:
        return self.items[0].rule if self.items else None

    def ids(self, n: int = 99) -> List[str]:
        return [m.id for m in self.items[:n]]


@dataclass
class _Hit:
    entry: _Entry
    score: float
    matched: List[str] = field(default_factory=list)
    positions: Set[int] = field(default_factory=set)
    strong: int = 0
    phrase_positions: Set[int] = field(default_factory=set)


BOOK_ORDER = ["GR", "AR", "CR", "ER", "ZR", "CCR", "SR", "LR", "DR", "RR"]
BOOK_WORDS = {
    "general": ("GR", None), "account": ("AR", None), "accounts": ("AR", None), "crime": ("CR", None),
    "event": ("ER", None), "events": ("ER", None), "zone": ("ZR", None), "zones": ("ZR", None),
    "clan": ("CCR", None), "cartel": ("CCR", None), "clan cartel": ("CCR", None),
    "state": ("SR", None), "leader": ("LR", None), "leaders": ("LR", None), "discord": ("DR", None),
    "redux": ("RR", None), "mod": ("RR", None), "mods": ("RR", None), "graphics": ("RR", None),
    "turf": ("CR", "Turf Rules"), "ghetto": ("CR", "Ghetto Rules"), "safe zone": ("ZR", None),
    "raid": ("SR", "Federal Investigation Bureau"), "fib": ("SR", "Federal Investigation Bureau"),
    "ems": ("SR", "Emergency Medical Services"), "lifeinvader": ("SR", "Lifeinvader"), "li": ("SR", "Lifeinvader"),
    "government": ("SR", "Government"), "gov": ("SR", "Government"), "doj": ("SR", "Government"),
    "sang": ("SR", "San Andreas National Guard"), "national guard": ("SR", "San Andreas National Guard"),
}
_PREFIXES = "ccr|ar|dr|gr|lr|sr|cr|er|zr|rr"
RE_DOTTED = re.compile(r"\b(c\.c\.r|[gacezsldr]\.r)\.?(?=[\s\d]|$)")
RE_ID = re.compile(rf"\b({_PREFIXES})\s*-?\s*(\d+(?:\.\d+)*|t\d+|r\d+|re)\b(?!\.\d)")
RE_NUM = re.compile(r"(?<![\w.])(\d+\.\d+(?:\.\d+)?)(?![\w.])")
RE_TURF = re.compile(r"\b(t\d{1,2})\b")


def sort_key(rule: Rule) -> tuple:
    num = rule.num
    bi = BOOK_ORDER.index(rule.book) if rule.book in BOOK_ORDER else 99
    if num.upper() == "RE":
        return (bi, 9, (999,))
    return (bi, 1 if num[0].isalpha() else 0, tuple(int(x) for x in re.findall(r"\d+", num)))


def _find_seq(hay: Sequence[str], needle: Tuple[str, ...]) -> int:
    n = len(needle)
    first = needle[0]
    for i in range(len(hay) - n + 1):
        if hay[i] == first and tuple(hay[i:i + n]) == needle:
            return i
    return -1


def _find_phrase(hay: Sequence[str], needle: Tuple[str, ...]) -> List[int]:
    """Positions of the phrase in the query; one filler word may sit inside it."""
    i = _find_seq(hay, needle)
    if i >= 0:
        return list(range(i, i + len(needle)))
    n = len(needle)
    if n < 2:
        return []
    for i, t in enumerate(hay):
        if t != needle[0]:
            continue
        for k in range(1, n):
            g = i + k
            if g < len(hay) and hay[g] in STOP_N and tuple(hay[i:g]) == needle[:k] \
                    and tuple(hay[g + 1:g + 1 + n - k]) == needle[k:]:
                return list(range(i, g)) + list(range(g + 1, g + 1 + n - k))
    return []


class SearchEngine:
    def __init__(self, book: RuleBook) -> None:
        self.book = book
        self.entries: List[_Entry] = []
        df: Dict[str, int] = {}
        raw_words: Dict[str, int] = {}

        def count_words(text: str, weight: int) -> None:
            for w in re.findall(r"[a-z]+", _prep(text)):
                if len(w) >= 2:
                    raw_words[w] = raw_words.get(w, 0) + weight

        self.alias_exact: Dict[str, List[str]] = {}     # "rdm" -> ["GR 6.2"]
        for rid, words in ALIASES.items():
            if rid not in book.by_id:
                continue
            for w in words:
                key = " ".join(tokens(w))
                if key and rid not in self.alias_exact.setdefault(key, []):
                    self.alias_exact[key].append(rid)
                count_words(w, 6)
        for r in book.rules:
            count_words(" ".join([r.name, r.short, " ".join(r.keywords)]), 2)
            count_words(" ".join([r.text, r.exception, r.note]), 1)
            name_t = set(content(tokens(r.name + " " + r.short)))
            phrases: List[Tuple[Tuple[str, ...], str, float]] = []
            seen: Set[Tuple[str, ...]] = set()
            kw_t: Set[str] = set()
            for src, weight in [(k, 1.0) for k in r.keywords] + [(a, ALIAS_WEIGHT) for a in ALIASES.get(r.id, [])]:
                pt = tuple(tokens(src))
                if pt and pt not in seen:
                    seen.add(pt)
                    phrases.append((pt, src, weight))
                    kw_t |= set(content(pt))
            body = " ".join([r.text, r.exception, r.note, r.section])
            text_t = set(content(tokens(body)))
            self.entries.append(_Entry(r, name_t, kw_t, text_t, phrases))
            for t in name_t | kw_t | text_t:
                df[t] = df.get(t, 0) + 1
        n = len(self.entries)
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        for w in COMPOUNDS:  # glued spellings are typo targets too ("metagamign" -> metagaming)
            if w.isalpha():
                raw_words[w] = raw_words.get(w, 0) + 3
        known = set(raw_words) | set(STOP) | set(SYN) | set(COMPOUNDS) | DROP
        self.known_words = known
        self.word_freq = raw_words
        self.vocab = sorted((w for w in raw_words if len(w) >= 3 and w.isalpha()),
                            key=lambda w: -raw_words[w])
        self.context_phrases = {k: [tuple(tokens(w)) for w in ws] for k, ws in CONTEXT_WORDS.items()}
        self.by_id = {e.rule.id: e for e in self.entries}
        self._cache: Dict[Tuple[str, int], SearchResult] = {}

    # ------------------------------------------------------------------ public
    def search(self, query: str, limit: int = 30) -> SearchResult:
        key = (query, limit)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        res = self._search(query, limit)
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = res
        return res

    def favorites(self, ids: Iterable[str]) -> List[Rule]:
        out = []
        for rid in ids:
            r = self.book.get(rid)
            if r is not None and r not in out:
                out.append(r)
        return out

    # ------------------------------------------------------------------ pieces
    def _search(self, query: str, limit: int) -> SearchResult:
        raw = query.strip()
        if not raw:
            return SearchResult(query, "empty")
        s = _prep(raw)
        s = RE_DOTTED.sub(lambda m: m.group(1).replace(".", ""), s)
        s = re.sub(r"(?<=\d),(?=\d)", ".", s)            # 6,2 -> 6.2
        ids = self._id_lookup(s)
        if ids:
            title = ids[0].id if len(ids) == 1 else f"{len(ids)} rules"
            return SearchResult(query, "id", [Match(r, 100.0, "id") for r in ids[:max(limit, 60)]], title=title)
        punish = self._punishment_lookup(s)
        if punish is not None:
            title, rows = punish
            return SearchResult(query, "punishment", [Match(r, 40.0, "book") for r in rows], title=title)
        listing = self._book_lookup(s)
        alias_ids = self._alias_lookup(s) or ([] if listing else self._alias_prefix(s, raw))
        if listing and not alias_ids:
            title, rows = listing
            return SearchResult(query, "book", [Match(r, 50.0, "book") for r in rows], title=title)
        res = self._ranked(raw, limit)
        if alias_ids:
            front = [Match(self.book.by_id[rid], 200.0 + PRIORITY.get(rid, 0), "alias") for rid in alias_ids]
            front_ids = {m.id for m in front}
            rest = [m for m in res.items if m.id not in front_ids]
            if listing:  # "cr": car ramming first, then the Crime Rules
                rest = [Match(r, 50.0, "book") for r in listing[1] if r.id not in front_ids]
            res.items = (front + rest)[:limit]
            res.mode = "alias"
        return res

    def _alias_lookup(self, s: str) -> List[str]:
        """The whole query is (nearly) one of the slang names: 'rdm', 'fera rp', 'combat loging'."""
        q = " ".join(tokens(s))
        if not q:
            return []
        if q in self.alias_exact:
            return sorted(self.alias_exact[q], key=lambda r: -PRIORITY.get(r, 0))
        flat = q.replace(" ", "")
        if len(flat) < 3:
            return []
        best: List[Tuple[float, str]] = []
        budget = typo_budget(flat)
        for key, rids in self.alias_exact.items():
            k = key.replace(" ", "")
            if abs(len(k) - len(flat)) > 2 or (len(k) <= 3) != (len(flat) <= 3):
                continue
            c = typo_cost(flat, k)
            if c <= budget:
                for rid in rids:
                    best.append((c, rid))
        if not best:
            return []
        top = min(c for c, _ in best)
        out: List[str] = []
        for c, rid in sorted(best, key=lambda x: (x[0], -PRIORITY.get(x[1], 0))):
            if c <= top + 0.01 and rid not in out:
                out.append(rid)
        return out[:3]

    def _alias_prefix(self, s: str, raw: str) -> List[str]:
        """Mid-word: 'r' -> RDM, ramming ...; 'co' -> combat log; 'sho' -> shooting in GZ."""
        if raw.endswith(" "):
            return []
        words = re.findall(r"[a-z]+", s)
        if not words or len(words) > 3:
            return []
        last = words[-1]
        if len(last) >= 4 and last in self.known_words and norm_word(last) in self.idf:
            return []  # a finished word: the normal search ranks it
        q = " ".join(tokens(s))
        if not q:
            return []
        best: Dict[str, Tuple[int, int]] = {}
        for key, rids in self.alias_exact.items():
            if key.startswith(q) and key != q:
                for rid in rids:
                    cand = (-PRIORITY.get(rid, 0), len(key))
                    if rid not in best or cand < best[rid]:
                        best[rid] = cand
        return sorted(best, key=lambda r: best[r])[:5]

    PUNISH_WORDS = {"warn": "warn", "warns": "warn", "warning": "warn", "ban": "block", "bans": "block",
                    "block": "block", "jail": "jail", "refinement": "jail", "kick": "jail", "mute": "mute",
                    "perm": "perm", "permanent": "perm", "permban": "perm"}

    def _punishment_lookup(self, s: str) -> Optional[Tuple[str, List[Rule]]]:
        """'warn' / 'jail 60' / 'ban 7' / 'perm' / 'mute 30' -> every rule with that punishment."""
        words = s.split()
        if not words or len(words) > 2 or words[0] not in self.PUNISH_WORDS:
            return None
        if len(words) == 2 and not words[1].isdigit():
            return None
        kind = self.PUNISH_WORDS[words[0]]
        amount = int(words[1]) if len(words) == 2 else None
        rows = []
        for r in self.book.rules:
            for a in r.actions:
                if kind == "perm":
                    ok = a.perm
                elif kind == "mute":
                    ok = a.label.lower().startswith("mute") and (amount is None or a.label == f"Mute {amount} min")
                else:
                    ok = a.kind == kind and not a.perm and (amount is None or a.amount == amount)
                if ok:
                    rows.append(r)
                    break
        if not rows:
            return None
        rows.sort(key=lambda r: (-PRIORITY.get(r.id, 0), sort_key(r)))
        label = {"warn": "Warn", "block": "Ban", "jail": "Jail", "mute": "Mute", "perm": "Permanent ban"}[kind]
        return f"{label}{f' {amount}' if amount else ''}: {len(rows)} rules", rows

    def _id_lookup(self, s: str) -> List[Rule]:
        out: List[Rule] = []

        def add(r: Rule) -> None:
            if r not in out:
                out.append(r)

        for m in RE_ID.finditer(s):
            pre, num = m.group(1).upper(), m.group(2).upper()
            rid = f"{pre} {num}"
            if rid in self.book.by_id:
                add(self.book.by_id[rid])
                continue
            hits = [r for r in self.book.rules if r.book == pre and r.num.upper().startswith(num + ".")]
            if not hits and re.fullmatch(r"\d{2,3}", num):  # "gr 62" -> GR 6.2, "gr 520" -> GR 5.20
                for cand in (f"{num[0]}.{num[1:]}", f"{num[:2]}.{num[2:]}"):
                    r = self.book.by_id.get(f"{pre} {cand}")
                    if r:
                        hits = [r]
                        break
            for r in sorted(hits, key=sort_key):
                add(r)
        short = len(content(tokens(RE_NUM.sub(" ", s)))) <= 2
        if not out and short and not RE_ID.search(s):
            for m in RE_NUM.finditer(s):
                for r in self.book.rules:
                    if r.num == m.group(1):
                        add(r)
            for m in RE_TURF.finditer(s):
                r = self.book.by_id.get(f"CR {m.group(1).upper()}")
                if r:
                    add(r)
        return out

    def _book_lookup(self, s: str) -> Optional[Tuple[str, List[Rule]]]:
        s = s.strip()
        forced = s.startswith("book ")
        if forced:
            s = s[5:].strip()
        prefixes = {b.prefix.lower(): b.prefix for b in self.book.books}
        if s in prefixes:
            bk, sec = prefixes[s], None
        else:
            base = re.sub(r"\s+rules?$", "", s).strip()
            if base == s and not forced:
                return None
            if base in prefixes:
                bk, sec = prefixes[base], None
            elif base in BOOK_WORDS:
                bk, sec = BOOK_WORDS[base]
            else:
                return None
        rows = [r for r in self.book.rules if r.book == bk and (sec is None or r.section.startswith(sec))]
        if not rows:
            return None
        return f"{bk} - {sec or self.book.book_title(bk)}", rows

    # ------------------------------------------------------------------ typo fixing / completion
    @lru_cache(maxsize=4096)
    def _fix_word(self, w: str) -> Optional[str]:
        if len(w) < 3 or w in self.known_words or norm_word(w) in self.idf or norm_word(w) in STOP_N:
            return None
        budget = typo_budget(w)
        best: Optional[Tuple[float, int, str]] = None
        for v in self.vocab:
            if abs(len(v) - len(w)) > 2:
                continue
            c = typo_cost(w, v)
            if v[0] != w[0] and not (len(w) > 1 and v[:2] == w[1::-1]):
                c += 0.5
            if c <= budget and (best is None or (c, -self.word_freq[v]) < (best[0], best[1])):
                best = (c, -self.word_freq[v], v)
        return best[2] if best else None

    def _correct(self, text: str) -> Tuple[str, Dict[str, str]]:
        fixes: Dict[str, str] = {}

        def fix(m: re.Match) -> str:
            w = m.group(0)
            f = self._fix_word(w)
            if f:
                fixes[w] = f
                return f
            return w

        return re.sub(r"[a-z]+", fix, _prep(text)), fixes

    def _completions(self, word: str, n: int = 4) -> List[str]:
        if len(word) < 2 or not word.isalpha():
            return []
        out = [v for v in self.vocab if v.startswith(word) and v != word]
        return out[:n]  # vocab is sorted by how often admins' words appear

    # ------------------------------------------------------------------ scoring
    def _score(self, e: _Entry, q: List[str], blocked: Set[int] = frozenset(), ctx_pos: Set[int] = frozenset(),
               weak: Set[int] = frozenset(), contexts: Tuple[str, ...] = ()) -> _Hit:
        found = []
        for ph, src, weight in e.phrases:
            hit = _find_phrase(q, ph)
            if not hit:
                continue
            meaningful = [t for t in ph if t not in STOP_N]
            if meaningful:
                found.append((len(ph), hit[0], ph, set(hit), meaningful, src, weight))
        found.sort(key=lambda f: (-f[0], -f[6], f[1]))
        covered: Set[int] = set()
        score = 0.0
        matched: List[str] = []
        strong = 0
        phrase_pos: Set[int] = set()
        for length, _i, ph, pos, meaningful, src, weight in found:
            if pos & blocked:
                continue
            fresh = [p for p in pos - covered - weak if q[p] not in STOP_N]
            if not fresh:
                if length < 2 or pos & covered:
                    continue
                fresh = [p for p in pos if q[p] not in STOP_N]
            w = sum(self.idf.get(t, 1.0) for t in meaningful) + 0.8 * (length - len(meaningful))
            real = [p for p in fresh if p not in ctx_pos]
            score += (3.0 + 2.2 * w + 2.5 * (length - 1)) * weight * (1.0 if real else CONTEXT_WEIGHT)
            covered |= pos
            if length >= 2:
                phrase_pos |= pos
            matched.append(src)
            if real and (length >= 2 or self.idf.get(q[real[0]], 0) >= STRONG_IDF or weight > 1.0):
                strong += 1
        seen: Set[str] = set()
        name_words = name_rare = 0
        for p, t in enumerate(q):
            if p in covered or p in blocked or p in weak or t in STOP_N or t in seen:
                continue
            seen.add(t)
            w = self.idf.get(t)
            if w is None:
                continue
            if t in e.name_t:
                s = 3.0 * w
            elif t in e.kw_t:
                s = 2.0 * w
            elif t in e.text_t:
                s = 1.0 * w
            else:
                continue
            score += s * (CONTEXT_WEIGHT if p in ctx_pos else 1.0)
            covered.add(p)
            matched.append(t)
            if p not in ctx_pos and t in e.name_t and w >= 1.5:
                name_words += 1
                name_rare += w >= STRONG_IDF
        if name_words >= 2 and name_rare:
            strong += 1
        q_content = {p for p, t in enumerate(q) if t not in STOP_N}
        if found and q_content and any(q_content <= f[3] for f in found):
            score += 8.0
        if score > 0:
            for c in contexts:
                if c in e.rule.context or CONTEXT_HOME[c](e.rule):
                    score += CONTEXT_BOOST
            tier = PRIORITY.get(e.rule.id, 0)
            score = score * (1.0 + 0.06 * tier) + 0.6 * tier
        return _Hit(e, round(score, 2), matched, covered, strong, phrase_pos)

    def _contexts(self, q: List[str]) -> Tuple[List[str], Set[int]]:
        found: List[str] = []
        pos: Set[int] = set()
        for key, phrases in self.context_phrases.items():
            for ph in phrases:
                i = _find_seq(q, ph)
                if i >= 0:
                    if key not in found:
                        found.append(key)
                    pos |= set(range(i, i + len(ph)))
        return found, pos

    def _rank_tokens(self, q: List[str]) -> Tuple[List[_Hit], List[str], Set[int]]:
        contexts, ctx_pos = self._contexts(q)
        ctx = tuple(contexts)
        hits = [self._score(e, q, ctx_pos=ctx_pos, contexts=ctx) for e in self.entries]
        hits = [h for h in hits if h.score > 0]
        hits.sort(key=lambda h: (-h.score, sort_key(h.entry.rule)))
        if hits:
            floor = max(6.0, 0.3 * hits[0].score)
            hits = [h for h in hits if h.score >= floor]
        return hits, contexts, ctx_pos

    def _ranked(self, raw: str, limit: int) -> SearchResult:
        body = re.sub(r"^\s*(?:report|ticket|complaint|case)\s*(?:#?\s*\d+)?\s*[:#\-]\s*", "", raw, flags=re.I)
        fixed, fixes = self._correct(body or raw)
        q = tokens(fixed)
        hits, contexts, ctx_pos = self._rank_tokens(q)
        # the last word may be half typed: try its likely endings too ("comb" -> combat)
        typing = not raw.endswith(" ")
        words = re.findall(r"[a-z]+", fixed)
        if typing and words:
            last = words[-1]
            last_known = last in self.known_words and norm_word(last) in self.idf
            if not last_known and last not in fixes.values():
                merged = {h.entry.rule.id: h for h in hits}
                base = fixed[: fixed.rfind(last)]
                for comp in self._completions(last):
                    alt_hits, alt_ctx, _ = self._rank_tokens(tokens(base + comp))
                    for h in alt_hits:
                        h.score = round(h.score * 0.95, 2)
                        cur = merged.get(h.entry.rule.id)
                        if cur is None or h.score > cur.score:
                            merged[h.entry.rule.id] = h
                    contexts = contexts or alt_ctx
                hits = sorted(merged.values(), key=lambda h: (-h.score, sort_key(h.entry.rule)))
                if hits:
                    floor = max(6.0, 0.3 * hits[0].score)
                    hits = [h for h in hits if h.score >= floor]
        res = SearchResult(raw, "search", corrections=fixes, contexts=contexts)
        if not hits:
            res.mode = "none"
            return res
        items = [Match(h.entry.rule, h.score, "hit", h.matched) for h in hits]
        words_c = content(q)
        if len(words_c) >= 3:
            findings, allowed = self._cover(q, ctx_pos, tuple(contexts))
            n_found = len(findings) + len(allowed)
            if (n_found >= 2 and len(words_c) >= 4) or (n_found and len(words_c) >= 6):
                res.mode = "situation"
                res.title = f"{len(findings)} possible rule break(s)"
                front = [Match(h.entry.rule, h.score, "finding", h.matched) for h in findings] + \
                        [Match(h.entry.rule, h.score, "allowed", h.matched) for h in allowed]
                ids = {m.id for m in front}
                items = front + [m for m in items if m.id not in ids]
        res.items = items[:limit]
        return res

    def _cover(self, q: List[str], ctx_pos: Set[int], contexts: Tuple[str, ...]) -> Tuple[List[_Hit], List[_Hit]]:
        """Greedy cover: take the best rule, block the words it explained, repeat."""
        blocked: Set[int] = set()
        weak: Set[int] = set()
        picks: List[_Hit] = []
        allowed: List[_Hit] = []
        picked: Set[str] = set()
        for _ in range(8):
            best: Optional[_Hit] = None
            for e in self.entries:
                if e.rule.id in picked:
                    continue
                h = self._score(e, q, blocked, ctx_pos, weak, contexts)
                if h.strong and (best is None or h.score > best.score):
                    best = h
            if best is None or best.score < MIN_PICK:
                break
            picked.add(best.entry.rule.id)
            blocked |= best.phrase_positions - ctx_pos
            weak |= (best.positions - best.phrase_positions) - ctx_pos
            is_allowed = best.entry.rule.punishment.strip().upper() == "N/A"
            (allowed if is_allowed else picks).append(best)
        return picks, allowed
