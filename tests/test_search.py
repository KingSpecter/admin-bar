import time

import pytest

from cases import REGRESSION_TOP3, SITUATIONS, TOP1
from conftest import ENGINE as engine


def top(q, n=3):
    return engine.search(q).ids(n)


@pytest.mark.parametrize("query,expected", TOP1)
def test_top_hit(query, expected):
    res = engine.search(query)
    assert res.best is not None, f"{query!r}: no result"
    assert res.best.id == expected, f"{query!r}: expected {expected}, got {res.ids(4)}"


@pytest.mark.parametrize("query,expected", REGRESSION_TOP3)
def test_unseen_queries_top3(query, expected):
    assert expected in top(query, 3), f"{query!r}: {expected} not in {top(query, 5)}"


@pytest.mark.parametrize("text,expected", SITUATIONS)
def test_situations(text, expected):
    got = set(top(text, len(expected) + 1))
    assert expected <= got, f"{text!r}: missing {expected - got}, got {top(text, 6)}"


# ---------------------------------------------------------------- the admin's own words
@pytest.mark.parametrize("query,expected", [
    ("rdm", "GR 6.2"), ("vdm", "GR 6.1"), ("cr", "GR 5.20"), ("CR", "GR 5.20"), ("cl", "GR 5.2"),
    ("gz", "ZR 1.1"), ("shooting in gz", "ZR 1.1"), ("shooting in green zone", "ZR 1.1"), ("sz", "ZR 1.1"),
    ("fearrp", "GR 6.8"), ("fear", "GR 6.8"), ("failrp", "GR 6.6"), ("fail", "GR 6.6"), ("pg", "GR 7.1"),
    ("cheats", "GR 3.1"), ("hacks", "GR 3.1"), ("esp", "GR 3.1"), ("ubd", "GR 6.4"), ("ub driving", "GR 6.4"),
    ("ub", "GR 6.5"), ("meta", "GR 6.7"), ("mg", "GR 6.7"), ("nlr", "GR 5.5"), ("rk", "GR 5.5"),
    ("combat log", "GR 5.2"), ("dc", "GR 5.2"), ("alt f4", "GR 5.2"), ("logged off", "GR 5.2"),
    ("car ram", "GR 5.20"), ("ramming", "GR 5.20"), ("afk", "GR 6.9"), ("bait", "GR 5.21"),
    ("rmt", "AR 1.9"), ("ghost peek", "CR T9"), ("teaming", "CCR 1.5"), ("mixing", "GR 7.0"),
])
def test_slang(query, expected):
    assert engine.search(query).best.id == expected, top(query, 4)


@pytest.mark.parametrize("query,expected", [
    ("rdn", "GR 6.2"), ("rmd", "GR 6.2"), ("vmd", "GR 6.1"), ("fera rp", "GR 6.8"), ("faer rp", "GR 6.8"),
    ("fearp", "GR 6.8"), ("fial rp", "GR 6.6"), ("faill rp", "GR 6.6"), ("comabt log", "GR 5.2"),
    ("combat loging", "GR 5.2"), ("cobmat log", "GR 5.2"), ("powergamign", "GR 7.1"), ("metagamign", "GR 6.7"),
    ("cheates", "GR 3.1"), ("aimbto", "GR 3.1"), ("ghost peeek", "CR T9"), ("teabaging", "GR 5.6"),
    ("rdm punishmnet", "GR 6.2"), ("randon deathmatch", "GR 6.2"), ("vehical deathmatch", "GR 6.1"),
    ("shootin in gz", "ZR 1.1"), ("unrealistc driving", "GR 6.4"),
])
def test_typos(query, expected):
    assert engine.search(query).best.id == expected, top(query, 4)


@pytest.mark.parametrize("query,expected", [
    ("comb", "GR 5.2"), ("combat l", "GR 5.2"), ("fear r", "GR 6.8"), ("power gam", "GR 7.1"),
    ("metag", "GR 6.7"), ("aimb", "GR 3.1"), ("ghost pe", "CR T9"),
])
def test_half_typed(query, expected):
    assert expected in top(query, 2), top(query, 4)


@pytest.mark.parametrize("query,expected", [
    ("قية", "GR 6.2"), ("رية", "GR 6.1"), ("لا", None), ("بثشق قح", "GR 6.8"),
])
def test_arabic_layout(query, expected):
    res = engine.search(query)
    if expected:
        assert res.best and res.best.id == expected, res.ids(3)


@pytest.mark.parametrize("query,ids", [
    ("gr 6.2", ["GR 6.2"]), ("GR6.2", ["GR 6.2"]), ("g.r 6.2", ["GR 6.2"]), ("G.R 6.2", ["GR 6.2"]),
    ("g.r. 6.2", ["GR 6.2"]), ("gr 62", ["GR 6.2"]), ("gr 520", ["GR 5.20"]), ("ar 1.11", ["AR 1.11"]),
    ("gr 5.13.1", ["GR 5.13.1"]), ("cr t3", ["CR T3"]), ("t9", ["CR T9"]), ("sr r2", ["SR R2"]),
    ("c.c.r 1.11", ["CCR 1.11"]), ("z.r 1.1", ["ZR 1.1"]), ("gr 6,2", ["GR 6.2"]),
])
def test_ids(query, ids):
    res = engine.search(query)
    assert res.mode == "id" and res.ids() == ids, (res.mode, res.ids(5))


def test_bare_numbers():
    assert set(top("6.2", 9)) == {"GR 6.2", "SR 6.2"}
    assert {"AR 1.6", "CR 1.6", "ER 1.6"} <= set(top("1.6", 20))
    assert engine.search("he sent 2.5 million to his alt").mode != "id"


def test_id_prefix_lists_the_section():
    got = top("gr 5", 40)
    assert got[0] == "GR 5.1" and "GR 5.22" in got and "GR 6.1" not in got


def test_book_listing():
    assert engine.search("gr").mode == "book" and len(engine.search("gr").items) == 45
    assert engine.search("turf rules").ids(1) == ["CR T1"]
    res = engine.search("cr")  # car ramming first, then the crime rules
    assert res.ids(2) == ["GR 5.20", "CR 1.1"]


def test_frp_offers_both():
    assert set(top("frp", 2)) == {"GR 6.8", "GR 6.6"}


def test_priority_breaks_ties():
    # 'fear' is FearRP before the EMS 'fearing for their lives' rule
    assert top("fear", 1) == ["GR 6.8"]
    assert top("shooting", 3)[0] in {"ZR 1.1", "GR 6.2", "GR 5.12"}


def test_long_report():
    q = ("Report: player ran me over with his car twice near legion, then when I got up "
         "he shot me without any rp and then logged off before admins came")
    res = engine.search(q)
    assert res.mode == "situation"
    assert {"GR 6.1", "GR 5.12", "GR 5.2"} <= set(res.ids(3))


def test_nothing():
    assert engine.search("xqzzv wplk").mode == "none"
    assert engine.search("   ").mode == "empty"


def test_fast_enough_for_typing():
    queries = ["r", "rd", "rdm", "comb", "combat lo", "he shot me without rp then logged off", "fera r", "g.r 6"]
    t = time.perf_counter()
    for q in queries:
        engine._search(q, 30)  # bypass the cache
    per = (time.perf_counter() - t) / len(queries)
    assert per < 0.08, f"{per * 1000:.0f} ms per search"
