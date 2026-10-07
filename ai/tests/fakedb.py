"""Tests use the simulator's in-memory database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pi"))
from memory_db import FakeDB, MemoryDB  # noqa: E402,F401
