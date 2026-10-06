"""Kept so old commands and cron lines still work. The AI now lives in the `ai` package — see ai/run.py.

    python ai/smart_home_ai.py train   ==   python -m ai.run train
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.run import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    main(["plan" if args[:1] == ["predict"] else args[0], *args[1:]] if args else ["--help"])
