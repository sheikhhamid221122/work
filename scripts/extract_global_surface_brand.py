"""Lift Global Surface Technologies' letterhead artwork out of their invoice.

Their sheet (`0124 Invoice for 01 new four-way valve body 15-07-26.pdf`) is a
Word document, so the letterhead is not a separate file -- it is the top and
bottom of the printed page. Two pieces are artwork and are taken as pixels:

    masthead  the two workshop photographs that flank the company name, and
              the WordArt wordmark between them (a pale green drop shadow with
              a dark green face). The wordmark is drawn as glyph outlines in a
              font nobody here has, so it cannot be set as text -- it has to be
              rendered.

    strip     the row of ten product thumbnails across the foot of the sheet.

Everything else down there is typed text and stays typed text in the template:
the "Specialized:" lines, the address, the phone numbers and the e-mails. They
read more crisply as text, and the address is theirs to correct without
re-running this script.

Output is `templates/brand/global_surface.html`, with both images inlined as
data URIs. Never a `/static/...` src: app.py renders invoice PDFs on routes
that do not all give WeasyPrint a base_url, so a linked image silently
resolves to nothing on some of them.

Run:  python3 scripts/extract_global_surface_brand.py
"""

from __future__ import annotations

import argparse
import base64
import io
import os

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "0124 Invoice for 01 new four-way valve body 15-07-26.pdf")
OUT = os.path.join(ROOT, "templates", "brand", "global_surface.html")

# Rendered at 600 DPI and scaled back down in the template. The source page is
# 612x792pt (US Letter) and PDF user space is 1/72in, so 600 DPI is scale 25/3.
DPI = 600
SCALE = DPI / 72.0
PAGE_H_PT = 792.0

# Regions in PDF points, (x0, y0, x1, y1) with y measured from the foot of the
# page, generous enough that the ink trim below decides the real edges.
#
#   masthead  the photographs sit at x 40.3-104.8 and 507.8-565.5, y 709.5-756;
#             the wordmark's two layers span y 701-766. Taken together so the
#             three pieces keep the spacing their designer gave them.
#   strip     ten images, x 70.3-553.7, y 12.25-57.35.
#
# The masthead is taken as three pieces rather than one band. As a single
# image it has to be PNG, because of the lettering -- and a PNG of two
# photographs is 200 KiB, inlined into every invoice this client prints.
# Split, the wordmark is three flat colours (18 KiB of PNG, lettering intact)
# and the photographs are JPEGs of a few KiB each. The template sets them
# back out at the widths measured here.
REGIONS = {
    "photo_left": (40.0, 709.0, 105.1, 756.3),
    "wordmark": (110.0, 697.0, 500.0, 770.0),
    "photo_right": (507.5, 712.0, 565.8, 756.3),
    "strip": (66.0, 12.0, 558.0, 57.0),
}

FORMATS = {
    "photo_left": "JPEG",
    "wordmark": "PNG",
    "photo_right": "JPEG",
    "strip": "JPEG",
}

# Only the wordmark is trimmed. A photograph's own edges are its edges -- ink
# trimming one crops whatever happens to be pale at its border, and the three
# pieces then no longer line up at the sizes measured off the sheet.
TRIMMED = {"wordmark"}

# Rendered width each piece is downsampled to. The wordmark earns the most:
# it is lettering, and it is the piece a reader looks at.
MAX_WIDTHS = {
    "photo_left": 540,
    "wordmark": 1800,
    "photo_right": 480,
    "strip": 1600,
}


def render_page(path: str) -> Image.Image:
    pdf = pdfium.PdfDocument(path)
    return pdf[0].render(scale=SCALE).to_pil().convert("RGB")


def crop(page: Image.Image, box_pt) -> Image.Image:
    x0, y0, x1, y1 = box_pt
    return page.crop((
        int(round(x0 * SCALE)),
        int(round((PAGE_H_PT - y1) * SCALE)),
        int(round(x1 * SCALE)),
        int(round((PAGE_H_PT - y0) * SCALE)),
    ))


def trim_to_ink(img: Image.Image, threshold: int = 247) -> Image.Image:
    """Trim the white border off, on all four sides.

    Measured from the rendered pixels rather than from the PDF's own object
    boxes: a glyph outline's advance width is wider than its ink, and the
    wordmark's drop shadow reaches past the letters that cast it.
    """
    grey = np.array(img.convert("L"))
    ink = grey < threshold
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    if not len(rows) or not len(cols):
        return img
    return img.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))


def encode(img: Image.Image, max_width: int, fmt: str) -> tuple[bytes, str]:
    """Bytes and media type, downsampled to `max_width`.

    The masthead is PNG because it carries the wordmark, and JPEG ringing
    around that lettering is the one artefact that would show at print size.
    The thumbnail strip is ten photographs and nothing else, so JPEG costs it
    nothing visible and saves most of a megabyte -- which matters here,
    because every invoice PDF this client prints carries both images inline.
    """
    if img.width > max_width:
        height = max(1, round(img.height * max_width / img.width))
        img = img.resize((max_width, height), Image.LANCZOS)
    buffer = io.BytesIO()
    if fmt == "JPEG":
        img.save(buffer, format="JPEG", quality=88, optimize=True, progressive=False)
        return buffer.getvalue(), "image/jpeg"
    # The wordmark is a pale green shadow, a dark green face and white, plus
    # the antialiasing between them. 64 colours holds all of that and cuts
    # the file to a fifth; median cut is fine here because none of the three
    # is a near-neighbour of another.
    img = img.convert("RGB").quantize(colors=64, method=Image.MEDIANCUT)
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), "image/png"


def data_uri(payload: bytes, media_type: str) -> str:
    return f"data:{media_type};base64," + base64.b64encode(payload).decode("ascii")


HEADER = '''{#
  Brand artwork for Global Surface Technologies, lifted from their own invoice
  by scripts/extract_global_surface_brand.py. Do not hand-edit: re-run it.

  Inlined as data URIs on purpose. app.py renders invoice PDFs with no
  base_url on some routes, so a "/static/..." src silently resolves to nothing
  there and prints the alt text instead. Artwork that travels inside the
  template cannot fail that way.

  Source: '0124 Invoice for 01 new four-way valve body 15-07-26.pdf', rendered
  at %(dpi)d DPI and cropped.

    masthead  the WordArt wordmark with a workshop photograph either side,
              laid out as their sheet has them. The wordmark is glyph
              outlines in a font we do not have, so it has to be pixels; the
              three are separate images so the lettering can stay PNG while
              the photographs go to JPEG.
    strip     the ten product thumbnails across the foot of their sheet.

  NOT lifted, and set as live text in the template instead: the "Specialized:"
  lines, the address, the telephone numbers and the e-mail addresses. None of
  them is artwork, and the address is an FBR field.
#}

{#- Widths in mm, measured off their sheet: the photographs are 23.0mm and
    20.6mm wide and the wordmark fills what is left between them. -#}
{%% macro masthead(alt='') %%}
<table class="brand-masthead">
  <tr>
    <td class="brand-photo-left">
      <img alt="" src="%(photo_left)s">
    </td>
    <td class="brand-wordmark">
      <img alt="{{ alt }}" src="%(wordmark)s">
    </td>
    <td class="brand-photo-right">
      <img alt="" src="%(photo_right)s">
    </td>
  </tr>
</table>
{%% endmacro %%}

{%% macro strip(alt='') %%}
<img class="brand-strip" alt="{{ alt }}"
     src="%(strip)s">
{%% endmacro %%}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()

    page = render_page(args.source)
    pieces = {}
    for name, box in REGIONS.items():
        piece = crop(page, box)
        if name in TRIMMED:
            piece = trim_to_ink(piece)
        payload, media_type = encode(piece, MAX_WIDTHS[name], FORMATS[name])
        pieces[name] = data_uri(payload, media_type)
        print(f"{name}: {piece.width}x{piece.height}px source, "
              f"{len(payload) / 1024:.0f} KiB {FORMATS[name]}")

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(HEADER % dict(pieces, dpi=DPI))
    print(f"wrote {args.out} ({os.path.getsize(args.out) / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
