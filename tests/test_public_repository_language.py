from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PUBLIC_PHRASES = {
    "conversation_heading": re.compile(
        r"\u4e3a\u4ec0\u4e48\u4e0d\u80fd\u4fdd\u8bc1|\u522b\u4eba\u62e5\u6709\u540c\u4e00\u6a21\u578b\u73af\u5883"
    ),
    "audience_specific_deployment": re.compile(
        r"\u5176\u4ed6\u5f00\u53d1\u8005|\u6bcf\u4f4d\u5f00\u53d1\u8005|\u5f00\u53d1\u8005\u81ea\u5df1\u7684\u8d26\u53f7"
    ),
    "private_review_context": re.compile(
        r"\u7528\u6237(?:\u8981\u6c42|\u8bf7\u6c42|\u63d0\u4f9b|\u786e\u8ba4|\u6388\u6743)|\u672c\u73af\u5883|\u672c\u4f1a\u8bdd|\u5f53\u524d\u4f1a\u8bdd"
    ),
    "private_english_context": re.compile(
        r"\b(?:user "
        r"(?:requested|specified|supplied|explicitly|confirmed|authorized|review|screenshots?)|prior "
        r"codex thread)\b",
        re.IGNORECASE,
    ),
    "second_person_placeholder": re.compile(r"\[" r"YOUR_[A-Z0-9_]+\]"),
}


def test_public_repository_docs_use_environment_neutral_language() -> None:
    paths = [ROOT / "README.md", ROOT / "outputs" / "manifest.json"]
    paths.extend(sorted((ROOT / "docs").rglob("*.md")))
    findings: list[str] = []

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for name, pattern in FORBIDDEN_PUBLIC_PHRASES.items():
            match = pattern.search(text)
            if match is not None:
                findings.append(
                    f"{path.relative_to(ROOT)}: {name}: {match.group(0)!r}"
                )

    assert findings == []
