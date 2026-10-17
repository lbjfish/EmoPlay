#!/usr/bin/env python3
"""Render the fixed-page submission reports from their Markdown sources."""

from __future__ import annotations

import argparse
import html
import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
)


def _font_path() -> Path:
    candidates = (
        Path("/System/Library/Fonts/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("a CJK font is required to build the reports")


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r'<font color="#235a8b">\1</font>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="CJK",
            fontSize=16,
            leading=21,
            textColor=colors.HexColor("#17395c"),
            alignment=TA_CENTER,
            spaceAfter=7 * mm,
        ),
        "h2": ParagraphStyle(
            "ReportH2",
            parent=base["Heading2"],
            fontName="CJK",
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor("#17395c"),
            spaceBefore=3.2 * mm,
            spaceAfter=1.4 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName="CJK",
            fontSize=9.1,
            leading=13.1,
            textColor=colors.HexColor("#202b36"),
            spaceAfter=2.3 * mm,
            wordWrap="CJK",
        ),
        "note": ParagraphStyle(
            "ReportNote",
            parent=base["BodyText"],
            fontName="CJK",
            fontSize=8.5,
            leading=12.2,
            textColor=colors.HexColor("#724b00"),
            backColor=colors.HexColor("#fff5d8"),
            borderColor=colors.HexColor("#e4bd59"),
            borderWidth=0.6,
            borderPadding=6,
            spaceAfter=4 * mm,
            wordWrap="CJK",
        ),
        "bullet": ParagraphStyle(
            "ReportBullet",
            parent=base["BodyText"],
            fontName="CJK",
            fontSize=8.9,
            leading=12.7,
            textColor=colors.HexColor("#202b36"),
            leftIndent=1.5 * mm,
            wordWrap="CJK",
        ),
    }


def _story(markdown: str, styles: dict[str, ParagraphStyle]) -> list[Any]:
    flow: list[Any] = []
    paragraph_lines: list[str] = []
    bullet_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            flow.append(
                Paragraph(_inline(" ".join(paragraph_lines)), styles["body"])
            )
            paragraph_lines.clear()

    def flush_bullets() -> None:
        if bullet_lines:
            # ReportLab's stubs model this parameter as an invariant list of
            # heterogeneous flowables. Keep the loosened type at this library
            # boundary instead of weakening checking for the whole script.
            bullet_items: list[Any] = [
                ListItem(Paragraph(_inline(item), styles["bullet"]))
                for item in bullet_lines
            ]
            flow.append(
                ListFlowable(
                    bullet_items,
                    bulletType="bullet",
                    start="circle",
                    leftIndent=5 * mm,
                    bulletFontName="Helvetica",
                    bulletFontSize=6,
                    spaceAfter=2 * mm,
                )
            )
            bullet_lines.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            continue
        if line.startswith("# "):
            flush_paragraph()
            flush_bullets()
            flow.append(Paragraph(_inline(line[2:]), styles["title"]))
        elif line.startswith("## "):
            flush_paragraph()
            flush_bullets()
            flow.append(Paragraph(_inline(line[3:]), styles["h2"]))
        elif line.startswith("> "):
            flush_paragraph()
            flush_bullets()
            flow.append(Paragraph(_inline(line[2:]), styles["note"]))
        elif line.startswith("- "):
            flush_paragraph()
            bullet_lines.append(line[2:])
        else:
            flush_bullets()
            paragraph_lines.append(line)
    flush_paragraph()
    flush_bullets()
    return flow


def _footer(canvas: Any, document: Any) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#d5dde5"))
    canvas.line(16 * mm, 13 * mm, A4[0] - 16 * mm, 13 * mm)
    canvas.setFont("CJK", 7.5)
    canvas.setFillColor(colors.HexColor("#687789"))
    canvas.drawString(16 * mm, 8.5 * mm, "EmoPlay 连续性报告")
    canvas.drawRightString(
        A4[0] - 16 * mm, 8.5 * mm, f"第 {document.page} 页"
    )
    canvas.restoreState()


def render(source: Path, output: Path, *, title: str) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=17 * mm,
        title=title,
        author="EmoPlay 项目",
        subject="多模态连续性说明",
    )
    document.build(
        _story(source.read_text(encoding="utf-8"), _styles()),
        onFirstPage=_footer,
        onLaterPages=_footer,
    )
    return document.page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    pdfmetrics.registerFont(TTFont("CJK", str(_font_path())))
    output = root / "output" / "pdf"
    main_pages = render(
        root / "docs" / "submission" / "main_report.md",
        output / "nining-main-report.pdf",
        title="EmoPlay 多模态连续性报告",
    )
    report_manifest = {
        "schema_version": "1.0",
        "reports": {
            "nining-main-report.pdf": {
                "pages": main_pages,
                "page_limit": 3,
                "sha256": sha256(
                    (output / "nining-main-report.pdf").read_bytes()
                ).hexdigest(),
            },
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(report_manifest, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    for path in sorted(output.glob("*.pdf")):
        print(path.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
