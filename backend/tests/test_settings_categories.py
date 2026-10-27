"""BUG-89: every backend setting is filed under a category in the UI.

The settings page groups settings by a hand-kept ``CATEGORIES`` map in
``settings-editor.tsx``. A key missing from it still renders, in an
"uncategorised" card under its key prefix, which reads as a duplicate of
the curated card rather than as an omission. That happened three times
before anything checked for it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.settings_service import SETTING_DEFINITIONS

EDITOR = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "app"
    / "(dashboard)"
    / "settings"
    / "settings-editor.tsx"
)


def _categorised_keys() -> set[str]:
    if not EDITOR.exists():
        pytest.skip("frontend source is not present alongside the backend")
    source = EDITOR.read_text()
    start = source.index("const CATEGORIES")
    end = source.index("\n}\n", start)
    return set(re.findall(r'"([a-z_]+\.[a-z0-9_]+)"', source[start:end]))


def test_every_setting_is_in_a_category():
    missing = sorted(set(SETTING_DEFINITIONS) - _categorised_keys())
    assert not missing, (
        f"{missing} would render in an 'uncategorised' card; add them to "
        f"CATEGORIES in {EDITOR.relative_to(EDITOR.parents[4])}"
    )


def test_every_categorised_key_is_a_setting():
    """A stale entry is harmless on the page, but it means a rename or a
    removal did not reach the map, which is the same drift."""
    unknown = sorted(_categorised_keys() - set(SETTING_DEFINITIONS))
    assert not unknown, f"CATEGORIES lists keys the backend does not define: {unknown}"
