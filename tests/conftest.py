import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adminbar.rules import RuleBook  # noqa: E402
from adminbar.search import SearchEngine  # noqa: E402

BOOK = RuleBook.load()
ENGINE = SearchEngine(BOOK)
