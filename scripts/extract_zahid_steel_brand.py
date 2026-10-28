# -*- coding: utf-8 -*-
"""Build Zahid Steel Industries' brand partial from their letterhead PDF.

    python scripts/extract_zahid_steel_brand.py

Why this client needs its own script
------------------------------------
scripts/extract_brand.py pulls *embedded raster images* out of a PDF. This
letterhead has almost none: everything on it -- the gear monogram, the
wordmark, the footer line, even the tax numbers -- was converted to vector
outlines before the file was saved. `page.get_objects()` reports 23 PATHs and
only a handful of tiny IMAGEs (the highlights inside the gear teeth). There is
no logo file in there to take out.

So the page is rendered at 600 DPI and the two pieces that are genuinely
*artwork* are cropped out of the render. At 600 DPI the crop is past what any
printer resolves, which is what the client asked for: a sheet they can print
and hand to a customer.

The two pieces
--------------
  * `masthead`  the ZSI gear monogram, the "ZAHID STEEL INDUSTRIES (PVT) LTD"
                wordmark and the red "ISO 9001 : 2015" line, as one block in
                the top right corner, exactly as their letterhead sets it.
  * `ornament`  the large half-gear in the bottom right corner. It runs off
                the right and bottom edges of their sheet, so those two sides
                are NOT trimmed -- the template bleeds it back off the page
                edge and the cut has to stay where the designer put it.

What is deliberately LEFT OUT of the artwork
--------------------------------------------
  * The NTN and STRN printed under the wordmark. Both are FBR fields, and
    both of the numbers on this sheet are **wrong for this client** -- the
    letterhead file carries NTN 8255820-3 / STRN 32-7787-6203-99-6, which
    are ZAHID ENTERPRISES STEEL RE-ROLLING MILLS' numbers, a separate
    business. Zahid Steel Industries (Pvt) Ltd is
    NTN J527785-7 / STRN 3277876392575. Frozen into a picture they
    would contradict the payload on every invoice. The template prints them
    live from `data`, in the serif the sheet uses.
  * The footer address / Tel / Email / Website line. The address is an FBR
    field for the same reason, and the line is plain Arial-grade grotesque
    that sets as text more sharply than any crop of it. The template types
    the whole line, address live and the contact details fixed.

Lifting only the two pictures and typing the rest is the same rule the other
replicas follow: draw what is type, lift only what is artwork.
"""
from __future__ import annotations

import io
import os
import sys

import numpy as np
import pypdfium2
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from extract_brand import BRAND_DIR, _data_uri  # noqa: E402

USERNAME = "J527785"
SOURCE = os.path.join(ROOT, "templates", "user-invoices", "%s.pdf" % USERNAME)

# Well past print resolution, so the crops are exact at any size the template
# puts them on the page.
DPI = 600
SCALE = DPI / 72.0

# Crop boxes in PDF points, measured from the ink profile of the rendered
# page rather than guessed: see the module docstring for what each one is.
# `trim` says which sides may be trimmed back to the ink -- the ornament
# bleeds off the sheet, so its right and bottom edges are left where they are.
PIECES = [
    ("masthead", (444.5, 36.0, 576.0, 156.1), "ltrb"),
    ("ornament", (434.4, 669.0, 612.0, 792.0), "lt"),
]

# Their paper reads 253-255, not 255. Left alone an octree palette rounds it
# to a near-white grey, which prints as a visible panel behind the artwork on
# an otherwise white page.
PAPER_SNAP = 248

# The gear is shaded steel-blue through a dozen stops. 16 colours bands it
# visibly; 64 does not, and the artwork is still small inlined.
COLOURS = 64

NOTE = (
    "Source: 'Zahid Steel Industries - Latter Head.pdf', the letterhead the\n"
    "client supplied, rendered at 600 DPI and cropped. Nothing on that sheet\n"
    "is an embedded image -- it is all vector outlines -- so there was no logo\n"
    "file to pull out and the crop IS the artwork.\n"
    "\n"
    "  masthead  gear monogram + wordmark + the red ISO 9001 : 2015 line.\n"
    "  ornament  the half-gear in the bottom right corner. Its right and\n"
    "            bottom edges are the sheet's own edges and are NOT trimmed:\n"
    "            the template bleeds it back off the page.\n"
    "\n"
    "  NOT lifted: the NTN and STRN under the wordmark, and the footer\n"
    "  address line. All three are FBR fields, and the two tax numbers on\n"
    "  this sheet are a DIFFERENT business's (Zahid Enterprises Steel\n"
    "  Re-Rolling Mills). The template prints all of them live from the\n"
    "  payload."
)


def snap_paper(image):
    """Pull the near-white paper to pure white, leaving the ink alone."""
    arr = np.asarray(image.convert("RGB")).astype(np.float32)
    arr[arr.min(axis=2) >= PAPER_SNAP] = 255.0
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def trim_to_ink(image, sides):
    """Crop white margins off the sides named in `sides` ("ltrb")."""
    grey = np.asarray(image.convert("L"))
    ink = grey < 240
    rows, cols = np.nonzero(ink)
    if not len(rows):
        return image
    left = int(cols.min()) if "l" in sides else 0
    top = int(rows.min()) if "t" in sides else 0
    right = int(cols.max()) + 1 if "r" in sides else image.width
    bottom = int(rows.max()) + 1 if "b" in sides else image.height
    return image.crop((left, top, right, bottom))


def write_partial(pieces, note):
    """templates/brand/<username>.html, one Jinja macro per piece.

    Inlined as data URIs on purpose. app.py renders invoice PDFs with no
    base_url, so a "/static/..." src silently resolves to nothing on some
    routes and prints the alt text instead -- a client's identity must not
    depend on which button produced the PDF.
    """
    os.makedirs(BRAND_DIR, exist_ok=True)
    path = os.path.join(BRAND_DIR, "%s.html" % USERNAME)
    lines = [
        "{#",
        "  Brand artwork for %s (Zahid Steel Industries (Pvt) Ltd), lifted" % USERNAME,
        "  from their letterhead by scripts/extract_zahid_steel_brand.py.",
        "  Do not hand-edit: re-run the script.",
        "",
        "  Inlined as data URIs on purpose. app.py renders invoice PDFs with no",
        '  base_url, so a "/static/..." src silently resolves to nothing on some',
        "  routes and prints the alt text instead. Artwork that travels inside",
        "  the template cannot fail that way.",
        "",
        "  " + note.replace("\n", "\n  "),
        "#}",
    ]
    total = 0
    for name, source in pieces:
        uri, size = _data_uri(source, colours=COLOURS, octree=True)
        total += size
        lines.append("")
        lines.append("{%% macro %s(alt='') %%}" % name)
        lines.append('<img class="brand-%s" alt="{{ alt }}" src="%s">' % (name, uri))
        lines.append("{% endmacro %}")
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path, total


def main():
    if not os.path.exists(SOURCE):
        raise SystemExit("No such letterhead: %s" % SOURCE)

    work = os.path.join(ROOT, "extracted", "zahid-steel")
    os.makedirs(work, exist_ok=True)

    pdf = pypdfium2.PdfDocument(SOURCE)
    try:
        sheet = pdf[0].render(scale=SCALE).to_pil().convert("RGB")
    finally:
        pdf.close()

    cut = []
    for name, (left, top, right, bottom), sides in PIECES:
        box = tuple(int(round(v * SCALE)) for v in (left, top, right, bottom))
        art = trim_to_ink(snap_paper(sheet.crop(box)), sides)
        out = os.path.join(work, "%s.png" % name)
        art.save(out)
        cut.append((name, out))
        print("%-9s %4dx%-4d px at %d DPI  (%.1f x %.1f mm on their sheet)"
              % (name, art.width, art.height, DPI,
                 art.width / DPI * 25.4, art.height / DPI * 25.4))

    path, total = write_partial(cut, NOTE)
    print("\nbrand partial -> %s (%d KB of artwork inlined)"
          % (os.path.relpath(path, ROOT), total // 1024))


if __name__ == "__main__":
    main()
