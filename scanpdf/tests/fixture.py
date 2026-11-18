"""Generate a PDF with known labels and no embedded text layer."""

from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("DejaVuSans.ttf", size)


def _insert_image(page: fitz.Page, rect: fitz.Rect, image: Image.Image) -> None:
    with BytesIO() as stream:
        image.save(stream, format="PNG")
        page.insert_image(rect, stream=stream.getvalue())


def _page_image(label: str) -> Image.Image:
    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    draw.text((100, 120), label, font=_font(58), fill="black")
    draw.text((100, 235), "This page was rendered as an image.", font=_font(42), fill="black")
    return image


def create_fixture(destination: Path) -> None:
    with fitz.open() as document:
        first = document.new_page(width=600, height=800)
        image = _page_image("FIRST PAGE EVIDENCE")
        draw = ImageDraw.Draw(image)
        for x in (100, 600, 1100):
            draw.line((x, 440, x, 740), fill="black", width=5)
        for y in (440, 590, 740):
            draw.line((100, y, 1100, y), fill="black", width=5)
        draw.text((145, 478), "Alpha", font=_font(52), fill="black")
        draw.text((645, 478), "Beta", font=_font(52), fill="black")
        draw.text((145, 628), "Gamma", font=_font(52), fill="black")
        draw.text((645, 628), "Delta", font=_font(52), fill="black")
        draw.text((345, 920), "E = m c²", font=_font(56), fill="black")
        draw.text((980, 920), "(1)", font=_font(56), fill="black")
        _insert_image(first, first.rect, image)

        second = document.new_page(width=600, height=800)
        _insert_image(second, second.rect, _page_image("SECOND PAGE EVIDENCE"))

        third = document.new_page(width=920, height=710)
        crop = fitz.Rect(35, 50, 835, 650)
        # The page rotation restores the stored bitmap to the intended visual view.
        image = _page_image("THIRD PAGE EVIDENCE").transpose(Image.Transpose.ROTATE_90)
        _insert_image(third, crop, image)
        third.set_cropbox(crop)
        third.set_rotation(90)
        document.save(destination)
