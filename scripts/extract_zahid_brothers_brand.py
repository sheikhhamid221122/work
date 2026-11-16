"""Rebuild Zahid Brothers' masthead from their scanned letterhead.

What the client sent is a **CamScanner photograph of a printed letterhead**,
not artwork and not a sheet with a placed logo file in it:
`templates/user-invoices/zahid_brothers-letterhead.pdf` holds exactly one
image object, a 2106 x 3000 px capture of the whole page. So neither mode of
`scripts/extract_brand.py` applies -- there is no image object to lift
(`--images` would lift the whole page) and a `--band` crop would carry the
scan's defects straight into the invoice at print size.

The defects are what this script exists to undo. The client's own words were
that the logo is low quality, and measured against the file they are right:

  * the black script "Zahid" and the emblem come back at RGB (57, 57, 57),
    not black -- a photograph of black ink under room light;
  * the red "Brothers" and the ZB monogram come back at (190, 55, 48), a
    desaturated brick where the print is a saturated red;
  * every stroke carries a halo of mid-greys from JPEG ringing and the
    camera's own blur, which at 85 mm across prints as a dirty edge.

CamScanner had already flattened the paper to a true 255 white, so there is
no background to remove -- the whole job is the ink.

How the ink is rebuilt
----------------------
Not by thresholding. A hard threshold on artwork this size eats the hairlines
in the script's connecting strokes and leaves the serifs jagged, and it throws
away the one thing the scan does get right, which is **coverage**: how much
ink a pixel holds. So the scan is separated into coverage and hue and then
re-inked:

  1. **coverage** -- how dark the pixel is between paper (255) and the
     measured core of the ink (about 48), clamped to 0..1. Edge pixels keep
     their partial values, which is what keeps the curves smooth.
  2. **hue** -- how red the pixel is, as r - (g + b) / 2, ramped between two
     thresholds rather than cut at one, then blurred slightly so a single
     noisy pixel in the middle of a black stroke cannot flip to red.
  3. **re-ink** -- composite coverage over white using the two brand colours
     below in place of the greys the camera recorded.

Coverage is also gamma-corrected (COVERAGE_GAMMA) before compositing. The
camera's blur spreads a stroke's edge over three or four pixels, and
compositing that linearly prints a stroke that looks soft and about a pixel
fatter than the press laid down; a gamma under 1.0 pulls the pale end of the
ramp down without touching the core, which re-cuts the edge. The same job
`--band` extractions do with an unsharp mask, done where the information is
rather than after the fact.

Only then is the result upscaled. Upscaling first would interpolate the
greys and bake the halo in at twice the size.

What is in the artwork, and what is not
---------------------------------------
The crop is the emblem, the "Zahid Brothers" wordmark AND the
"Main Dealer: MILLAT TRACTORS LTD. LAHORE" line under it, because that is
what the client asked for: the masthead replaces the two typed lines their
old sheet carried ("ZAHID BROTHERS" over "Main Dealer: Millat Tractors
Limited."). The address line their sheet prints under those two stays live
text in the template.

What is NOT here, deliberately:

  * the Millat and Massey Ferguson dealer marks at the foot of the letterhead
    -- their old invoice never carried them, and they are other companies'
    trade marks;
  * the address, phone, NTN and sales tax registration number. Nothing in
    this image is a number. Those are FBR fields, they are typed in the
    template, and artwork that froze them could leave a printed invoice
    contradicting what was filed -- the rule the whole of
    CLIENT_INVOICE_TEMPLATES.md turns on.

Inlined as a data URI, never a `/static/...` src: app.py renders invoice PDFs
on routes that do not all give WeasyPrint a base_url, so a linked image
silently resolves to nothing on some of them and prints the alt text instead.

Run:  python3 scripts/extract_zahid_brothers_brand.py
"""

from __future__ import annotations

import argparse
import base64
import io
import os

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "templates", "user-invoices",
                      "zahid_brothers-letterhead.pdf")
OUT = os.path.join(ROOT, "templates", "brand", "zahid_brothers.html")

# The masthead's share of the scanned page, as fractions of its height, so a
# re-scan at another resolution crops the same thing. Measured on the file:
# the wordmark's ink runs rows 179-467 and the dealer line 592-~700 of 3000,
# with nothing else above row 2200.
TOP = 0.045
BOTTOM = 0.250

# Measured on the file. The core of the black ink photographs at (57, 57, 57)
# and the core of the red at (190, 55, 48); these are what the press actually
# laid down, as close as a flat colour gets to it.
BLACK = (26, 26, 26)
RED = (186, 30, 36)

# Where a pixel stops being paper and where it is fully inked. 248 rather
# than 255 so the JPEG's faint speckle over clean paper is dropped outright.
PAPER = 248.0
INK = 48.0

# Pulls the pale end of the coverage ramp down, re-cutting edges the camera
# blurred. Below about 0.7 the hairlines in the script start to break up.
COVERAGE_GAMMA = 0.80

# Red is ramped between these, not cut at one of them, so the boundary
# between "Zahid" and "Brothers" does not come out pixelated.
RED_LO = 18.0
RED_HI = 55.0

# Not upscaled. The crop is already 1713 px across the 85 mm it prints at,
# about 512 DPI, and the re-inking above has done the edge work an upscale
# would otherwise be there to help with -- doubling it tripled the payload
# inlined into every invoice and changed nothing on paper.
SCALE = 1
COLOURS = 24


def scanned_page(path: str) -> Image.Image:
    """The one image object in the client's PDF, at its own resolution.

    Rendering the page instead would resample a photograph that is already
    larger than the page box -- 2106 px across 560 pt -- and throw away
    detail before any of the work below starts.
    """
    page = pdfium.PdfDocument(path)[0]
    images = [obj for obj in page.get_objects() if obj.type == 3]
    if not images:
        raise SystemExit(f"{path}: no image object in the page")
    biggest = max(images, key=lambda obj: obj.get_bitmap(render=False).width)
    return biggest.get_bitmap(render=False).to_pil().convert("RGB")


def trim(image: Image.Image, threshold: int = 200) -> Image.Image:
    """Crop to the ink, ignoring single stray pixels.

    A plain bounding box on a photograph catches the speck of dust the camera
    found near the top edge and leaves 150 px of blank paper above the
    emblem, so a row or column has to carry at least two inked pixels to
    count.
    """
    ink = np.asarray(image).astype(int).mean(2) < threshold
    rows = np.where(ink.sum(1) > 1)[0]
    cols = np.where(ink.sum(0) > 1)[0]
    if not len(rows) or not len(cols):
        return image
    return image.crop((int(cols.min()), int(rows.min()),
                       int(cols.max()) + 1, int(rows.max()) + 1))


def reink(image: Image.Image) -> Image.Image:
    """Separate the scan into coverage and hue, then re-ink it.

    See the module docstring. The returned image is the same size; nothing is
    resampled here.
    """
    array = np.asarray(image).astype(np.float64)
    r, g, b = array[..., 0], array[..., 1], array[..., 2]

    coverage = np.clip((PAPER - array.mean(2)) / (PAPER - INK), 0.0, 1.0)
    coverage = coverage ** COVERAGE_GAMMA

    redness = r - (g + b) / 2.0
    weight = np.clip((redness - RED_LO) / (RED_HI - RED_LO), 0.0, 1.0)
    # A three-pixel blur of the hue decision only. Coverage is untouched, so
    # this cannot soften an edge -- it only stops one noisy pixel inside a
    # black stroke from printing red.
    weight = np.asarray(
        Image.fromarray((weight * 255).astype(np.uint8))
             .filter(ImageFilter.GaussianBlur(1.5))
    ).astype(np.float64) / 255.0

    black = np.array(BLACK, dtype=np.float64)
    red = np.array(RED, dtype=np.float64)
    target = black + (red - black) * weight[..., None]

    out = 255.0 + (target - 255.0) * coverage[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def encode(image: Image.Image) -> bytes:
    """Quantise, then put paper back to exactly 255.

    Octree rather than median cut: the artwork is about 90% white, and median
    cut spends the palette on near-whites and muddies the red -- the wrinkle
    Rubber X's and Paper Land's partials hit. Octree keeps the extremes, but
    the palest edge pixels still land in the paper node and pull it to 254,
    which prints as a faint panel the width of the image wherever the sheet
    is genuinely white. There is no second near-white in this artwork, so the
    palest palette entry IS paper and is simply rewritten.
    """
    quantised = image.quantize(colors=COLOURS, method=Image.FASTOCTREE)
    palette = quantised.getpalette()
    lightest = max(range(len(palette) // 3),
                   key=lambda i: sum(palette[i * 3:i * 3 + 3]))
    palette[lightest * 3:lightest * 3 + 3] = [255, 255, 255]
    quantised.putpalette(palette)

    buffer = io.BytesIO()
    quantised.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


HEADER = """{#
  Brand artwork for Zahid Brothers, emitted by
  scripts/extract_zahid_brothers_brand.py. Do not hand-edit: re-run the script.

  Source: templates/user-invoices/zahid_brothers-letterhead.pdf -- a
  CamScanner photograph of their printed letterhead, one %(page_w)d x %(page_h)d px
  image object. The masthead band is cropped out of it, the camera's greys are
  separated into coverage and hue and re-inked in the printed colours
  (black %(black)s, red %(red)s), then quantised to %(colours)d colours with
  octree. %(out_w)d x %(out_h)d px, about %(dpi)d DPI at the %(print_mm)g mm it
  prints at.

  It carries the emblem, the "Zahid Brothers" wordmark and the
  "Main Dealer: MILLAT TRACTORS LTD. LAHORE" line, which between them replace
  the two typed lines at the top of the client's old invoice. It carries NO
  numbers: the address, phone, NTN and sales tax registration number are live
  text in templates/invoice_zahid_brothers.html, because they are FBR fields
  and frozen artwork could contradict what was filed.

  The Millat and Massey Ferguson dealer marks at the foot of the letterhead
  are not here -- their invoice never carried them.

  Inlined as a data URI on purpose. app.py renders invoice PDFs with no
  base_url, so a "/static/..." src silently resolves to nothing on some routes
  and prints the alt text instead.
#}

{%% macro masthead(alt='') %%}
<img class="brand-masthead" alt="{{ alt }}" src="%(uri)s">
{%% endmacro %%}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--print-mm", type=float, default=85.0,
                        help="width the template prints it at, for the DPI note")
    parser.add_argument("--png", help="also write the artwork here, to look at")
    args = parser.parse_args()

    page = scanned_page(args.source)
    band = page.crop((0, int(page.height * TOP), page.width,
                      int(page.height * BOTTOM)))
    cropped = trim(band)
    inked = reink(cropped)
    upscaled = (inked if SCALE == 1 else
                inked.resize((inked.width * SCALE, inked.height * SCALE),
                             Image.LANCZOS))

    payload = encode(upscaled)
    if args.png:
        with io.open(args.png, "wb") as handle:
            handle.write(payload)

    uri = "data:image/png;base64," + base64.b64encode(payload).decode()
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(HEADER % {
            "page_w": page.width,
            "page_h": page.height,
            "black": "#%02x%02x%02x" % BLACK,
            "red": "#%02x%02x%02x" % RED,
            "scale": SCALE,
            "colours": COLOURS,
            "out_w": upscaled.width,
            "out_h": upscaled.height,
            "print_mm": args.print_mm,
            "dpi": round(upscaled.width / (args.print_mm / 25.4)),
            "uri": uri,
        })

    print(f"page {page.width}x{page.height}px, "
          f"masthead {cropped.width}x{cropped.height}px, "
          f"emitted {upscaled.width}x{upscaled.height}px "
          f"({len(payload) / 1024:.1f} KiB PNG, "
          f"{round(upscaled.width / (args.print_mm / 25.4))} DPI at {args.print_mm} mm)")
    print(f"wrote {args.out} ({os.path.getsize(args.out) / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
