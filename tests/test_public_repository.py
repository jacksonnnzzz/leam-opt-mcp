from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    [
        ROOT / ".env.example",
        ROOT / "CHANGELOG.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "pyproject.toml",
    ]
    + list((ROOT / ".github").rglob("*"))
    + list((ROOT / "docs").rglob("*"))
    + list((ROOT / "examples").rglob("*"))
    + list((ROOT / "src").rglob("*"))
    + list((ROOT / "tools").rglob("*"))
)


def _text_files():
    for path in PUBLIC_FILES:
        if path.is_file() and path.suffix.lower() in {
            "",
            ".example",
            ".json",
            ".md",
            ".py",
            ".toml",
            ".yaml",
            ".yml",
        }:
            yield path


def test_public_files_do_not_contain_personal_windows_profiles():
    personal_profile = re.compile(r"[A-Za-z]:[/\\]Users[/\\](?!<|example)", re.IGNORECASE)
    failures = []
    for path in _text_files():
        if path == Path(__file__):
            continue
        if personal_profile.search(path.read_text("utf-8", errors="replace")):
            failures.append(str(path.relative_to(ROOT)))
    assert failures == []


def test_public_files_do_not_contain_api_key_literals():
    token_prefix = "s" + "k-"
    token = re.compile(re.escape(token_prefix) + r"[A-Za-z0-9_-]{12,}")
    failures = []
    for path in _text_files():
        if path == Path(__file__):
            continue
        if token.search(path.read_text("utf-8", errors="replace")):
            failures.append(str(path.relative_to(ROOT)))
    assert failures == []
