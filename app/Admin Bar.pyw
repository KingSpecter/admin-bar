"""Admin Bar - double-click to start (runs without a console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adminbar.main import main  # noqa: E402

sys.exit(main())
