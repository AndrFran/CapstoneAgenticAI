"""Pytest configuration.

Adds ``src`` to the path so the tests run against the source tree without an
install step, and keeps the runtime write store out of the committed fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supplychain.data import access  # noqa: E402


@pytest.fixture(autouse=True)
def clean_runtime_store():
    """Every test starts from the committed fixtures, with no runtime writes."""
    access.reset_runtime_store()
    yield
    access.reset_runtime_store()
