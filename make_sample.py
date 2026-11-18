"""Rebuild the checked-in sample PDF with ReportLab. No network needed."""
from pathlib import Path
from os import environ
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFont

OUT = Path(__file__).with_name("sample.pdf")
INK = HexColor("#18232e")
TEAL = HexColor("#007261")
MUTED = HexColor("#536676")
PAPER = HexColor("#f4f8f7")
PAGE_W, PAGE_H = A4


def draw_para(canvas, text, x, y, width, size=12, leading=18):
    style = ParagraphStyle("body", fontName="DeskSans", fontSize=size,
                           leading=leading, textColor=INK)
    para = Paragraph(text, style)
    _, height = para.wrap(width, PAGE_H)
    para.drawOn(canvas, x, y - height)
    return y - height


def header(canvas, page, label, title, deck):
    canvas.setFillColor(TEAL)
    canvas.rect(0, PAGE_H - 12, PAGE_W, 12, fill=1, stroke=0)
    canvas.setFont("DeskSans-Bold", 10)
    canvas.drawString(56, 760, "EVIDENCE DESK   /   " + label)
    canvas.setFillColor(INK)
    canvas.setFont("DeskSans-Bold", 34)
    canvas.drawString(56, 711, title)
    draw_para(canvas, deck, 56, 683, 477, size=14, leading=20)
    canvas.setStrokeColor(HexColor("#cddbd9"))
    canvas.line(56, 74, 539, 74)
    canvas.setFillColor(MUTED)
    canvas.setFont("DeskSans", 9)
    canvas.drawString(56, 53, "Original demonstration guide | Not a research paper")
    canvas.drawRightString(539, 53, f"{page} / 2")


def section(canvas, y, label, body):
    canvas.setFillColor(TEAL)
    canvas.setFont("DeskSans-Bold", 16)
    canvas.drawString(56, y, label)
    return draw_para(canvas, body, 56, y - 16, 477) - 38


def main():
    fonts = Path(environ.get("EVIDENCE_DESK_FONT_DIR", "/usr/share/fonts/truetype/dejavu"))
    registerFont(TTFont("DeskSans", str(fonts / "DejaVuSans.ttf")))
    registerFont(TTFont("DeskSans-Bold", str(fonts / "DejaVuSans-Bold.ttf")))
    canvas = Canvas(str(OUT), pagesize=A4, invariant=1)
    canvas.setTitle("Evidence Desk: A local evidence retrieval walkthrough")
    canvas.setAuthor("Evidence Desk")
    header(canvas, 1, "QUICKSTART", "From question to passage",
           "A short walkthrough for finding and checking evidence in a local PDF.")
    y = section(canvas, 597, "01  Import a text PDF",
                "Choose a PDF that you are allowed to use. This file was written "
                "for the demo and contains selectable text. Documents with only "
                "scanned page images need text recognition before text can be found.")
    y = section(canvas, y, "02  Search for source wording",
                "Start with distinctive words you expect in the document. "
                "If your first query misses, try a shorter phrase or a term the author uses.")
    section(canvas, y, "03  Open the source page",
            "A ranked result is a navigation aid, not a finding. Open the cited "
            "page and read the surrounding text to see what the passage supports. "
            "Check nearby definitions, conditions, and exceptions before quoting.")
    canvas.showPage()

    header(canvas, 2, "VERIFICATION", "A test you can reproduce",
           "Try this known-answer lookup and compare the retrieved passage with "
           "the PDF page itself.")
    canvas.setFillColor(PAPER)
    canvas.roundRect(56, 493, 483, 116, 9, fill=1, stroke=0)
    canvas.setFillColor(TEAL)
    canvas.setFont("DeskSans-Bold", 10)
    canvas.drawString(73, 586, "SEARCH QUERY")
    canvas.setFillColor(INK)
    canvas.setFont("DeskSans-Bold", 19)
    canvas.drawString(73, 555, "verify page numbering")
    draw_para(canvas, "Expected source: this PDF, physical page 2.", 73, 532, 449)
    y = section(canvas, 448, "Why check the page?",
                "To verify page numbering, distinguish a page's physical position "
                "in the PDF from any printed label. Front matter can make the two "
                "differ. Open the physical page shown by the search result and confirm "
                "the passage is present before recording a citation.")
    section(canvas, y, "Why this sample works",
            "The answer is written directly in the document, and the exercise does "
            "not depend on a model, an internet connection, or outside facts. "
            "A correct result is a passage from page 2 about physical position "
            "versus printed labels, not merely a repetition of the query.")
    canvas.showPage()
    canvas.save()


if __name__ == "__main__":
    main()
