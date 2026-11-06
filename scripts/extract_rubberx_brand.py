"""Inline Rubber X Technologies' logo into their brand partial.

What the client sent is a **logo file**, not an invoice PDF, so
`scripts/extract_brand.py` does not apply -- there is no page to lift objects
out of. The file is `templates/user-invoices/rubberx-logo.png`, 322 x 135 px.

Three colours and nothing else
------------------------------
The artwork is flat vector-style art: white, their orange (255, 153, 0) and
their dark red (153, 0, 0), with a one-pixel anti-aliased edge between them.
That is what makes 322 px usable at all. A photograph at this size would be
hopeless (see F.K. Printers and SAAD Enterprises in
CLIENT_INVOICE_TEMPLATES.md); flat art has no detail to lose, only edges, and
edges can be re-cut.

So the pipeline is short:

  1. crop the seven blank columns off the left -- the ink already reaches
     every other edge of the file, so there is nothing else to trim;
  2. upscale 3x with Lanczos and re-steepen with a light unsharp mask, which
     re-cuts the soft edges rather than inventing detail;
  3. snap everything at or above 240 to pure white. Without this the paper
     prints as a visible grey panel behind the logo: the file's background is
     already 255, but the anti-aliased pixels just under it share an octree
     node with it and pull the node's average down. Same wrinkle as Power
     Vision's and Bilal Brothers' letterheads -- see
     CLIENT_INVOICE_TEMPLATES.md;
  4. quantise to 16 colours with **octree**. Median cut splits the palette by
     pixel count and this file is 68% white, so it spends the palette on
     near-whites and the dark red comes out muddy. Octree keeps the extremes.

The result is about 14 KiB of base64 inlined into every invoice.

### This artwork is a stand-in

322 px across the 45 mm it prints at is about 182 DPI. That is enough for flat
art to look right on paper, but it is below the ~300 DPI the other clients'
mastheads run at, and the thin "technologies" lettering is the part that will
show it first. **Ask them for the vector file (AI/EPS/PDF/SVG) or a 1000 px+
PNG and re-run this script** -- nothing in `templates/invoice_rubberx.html`
has to change, it sizes the image by width with `height: auto`.

Inlined as a data URI, never a `/static/...` src: app.py renders invoice PDFs
on routes that do not all give WeasyPrint a base_url, so a linked image
silently resolves to nothing on some of them and prints the alt text instead.

Run:  python3 scripts/extract_rubberx_brand.py
"""

from __future__ import annotations

import argparse
import base64
import io
import os

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "templates", "user-invoices", "rubberx-logo.png")
OUT = os.path.join(ROOT, "templates", "brand", "rubberx.html")

# Flat art: 3x is enough to re-cut the edges, and 32 colours would double the
# payload for a file that only has three.
SCALE = 3
COLOURS = 16

HEADER = """{#
  Brand artwork for Rubber X Technologies, emitted by
  scripts/extract_rubberx_brand.py. Do not hand-edit: re-run the script.

  Source: templates/user-invoices/rubberx-logo.png, the logo file the client
  supplied -- %(src_w)d x %(src_h)d px, flat three-colour art (white, orange
  #ff9900, dark red #990000). Cropped to ink, upscaled %(scale)dx with Lanczos,
  edges re-steepened, paper snapped to pure white, quantised to %(colours)d
  colours with octree.

  STAND-IN. At the 45 mm it prints at this is about %(dpi)d DPI, against the
  ~300 DPI the other clients' mastheads run at. Ask the client for the vector
  file or a 1000 px+ PNG and re-run the script; the template needs no change,
  it sizes by width with height: auto.

  Inlined as a data URI on purpose. app.py renders invoice PDFs with no
  base_url, so a "/static/..." src silently resolves to nothing on some routes
  and prints the alt text instead. Artwork that travels inside the template
  cannot fail that way.

  The services list that sits beside this logo on the client's own sheet --
  "We Deal in all kind of Rubber / Silicon / ..." -- is NOT here. It is typed
  text in the template's footer, so it prints sharp and stays editable.
#}

{%% macro masthead(alt='') %%}
<img class="brand-masthead" alt="{{ alt }}" src="%(uri)s">
{%% endmacro %%}
"""


def trim_left(image: Image.Image) -> Image.Image:
    """Drop blank columns from the left edge.

    Only the left: the ink in this file runs to the top, bottom and right
    edges already, so trimming those would be a no-op that could only ever
    bite into the crosshair if the client sends a differently-cropped file.
    """
    ink = np.asarray(image).astype(int).sum(2) < 740
    columns = np.where(ink.any(0))[0]
    return image.crop((int(columns.min()), 0, image.width, image.height))


def snap_paper(image: Image.Image, floor: int = 240) -> Image.Image:
    """Force near-white pixels to pure white before quantising.

    The source file's background is already 255, but the anti-aliased pixels
    just under it land in the same octree node and pull its average to about
    252 -- which prints as a grey panel behind the logo, the width of the
    whole image. Snapping first keeps paper in a node of its own.
    """
    array = np.asarray(image).astype(int)
    array[array.min(2) >= floor] = 255
    return Image.fromarray(array.astype(np.uint8))


def encode(image: Image.Image) -> bytes:
    """Quantise, then put paper back to exactly 255.

    Snapping before the quantiser is not enough on its own. Octree averages
    the colours that fall in a node, and the very palest edge pixels still
    land in the paper node, so it comes back 254 rather than 255 -- a flat
    panel one level off the page, which shows as a faint rectangle wherever
    the sheet is genuinely white. There is no second near-white in this
    artwork to confuse with paper, so the palest palette entry IS paper and
    is simply rewritten.
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--print-mm", type=float, default=45.0,
                        help="width the template prints it at, for the DPI note")
    args = parser.parse_args()

    original = Image.open(args.source).convert("RGB")
    cropped = trim_left(original)

    upscaled = cropped.resize(
        (cropped.width * SCALE, cropped.height * SCALE), Image.LANCZOS)
    upscaled = upscaled.filter(
        ImageFilter.UnsharpMask(radius=2.0, percent=90, threshold=2))

    payload = encode(snap_paper(upscaled))
    uri = "data:image/png;base64," + base64.b64encode(payload).decode()

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(HEADER % {
            "src_w": cropped.width,
            "src_h": cropped.height,
            "scale": SCALE,
            "colours": COLOURS,
            "dpi": round(cropped.width / (args.print_mm / 25.4)),
            "uri": uri,
        })

    print(f"source {original.width}x{original.height}px, "
          f"cropped {cropped.width}x{cropped.height}px, "
          f"emitted {upscaled.width}x{upscaled.height}px "
          f"({len(payload) / 1024:.1f} KiB PNG)")
    print(f"wrote {args.out} ({os.path.getsize(args.out) / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
