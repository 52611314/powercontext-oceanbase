"""Generate the demo PDFs from the Chinese sample notes.

Usage: python scripts/make_samples.py
Requires: reportlab (dev only, not a runtime dependency).
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

PROJECT_DIR = Path(__file__).resolve().parents[1]
NOTES_DIR = PROJECT_DIR / "samples" / "notes"
OUT_DIR = PROJECT_DIR / "samples" / "papers"

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

TITLE = ParagraphStyle("title", fontName="STSong-Light", fontSize=20, leading=30, spaceAfter=18)
HEADING = ParagraphStyle("heading", fontName="STSong-Light", fontSize=14, leading=22, spaceBefore=14, spaceAfter=8)
BODY = ParagraphStyle("body", fontName="STSong-Light", fontSize=10.5, leading=18, spaceAfter=8, firstLineIndent=21)
BULLET = ParagraphStyle("bullet", parent=BODY, firstLineIndent=0, leftIndent=18, bulletIndent=6)


def parse_markdown(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if buffer:
                blocks.append(("p", " ".join(buffer)))
                buffer = []
            continue
        if line.startswith("## "):
            if buffer:
                blocks.append(("p", " ".join(buffer)))
                buffer = []
            blocks.append(("h2", line[3:].strip()))
        elif line.startswith("# "):
            if buffer:
                blocks.append(("p", " ".join(buffer)))
                buffer = []
            blocks.append(("h1", line[2:].strip()))
        elif line.startswith("- "):
            if buffer:
                blocks.append(("p", " ".join(buffer)))
                buffer = []
            blocks.append(("li", line[2:].strip()))
        else:
            buffer.append(line)
    if buffer:
        blocks.append(("p", " ".join(buffer)))
    return blocks


def build_pdf(source: Path, target: Path) -> None:
    blocks = parse_markdown(source.read_text(encoding="utf-8"))
    title = next((text for kind, text in blocks if kind == "h1"), source.stem)
    document = SimpleDocTemplate(
        str(target),
        pagesize=A4,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
        topMargin=2.2 * cm,
        bottomMargin=2.2 * cm,
        title=title,
        author="示例资料",
    )
    story = []
    for kind, text in blocks:
        if kind == "h1":
            story.append(Paragraph(text, TITLE))
        elif kind == "h2":
            story.append(Paragraph(text, HEADING))
        elif kind == "li":
            story.append(Paragraph(text, BULLET, bulletText="•"))
        else:
            story.append(Paragraph(text, BODY))
    story.append(Spacer(1, 12))
    document.build(story)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for source in sorted(NOTES_DIR.glob("*.md")):
        target = OUT_DIR / f"{source.stem}.pdf"
        build_pdf(source, target)
        print(f"generated {target.relative_to(PROJECT_DIR)} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
