# -*- coding: utf-8 -*-
"""Lift Bilal Brothers' crest out of the photo of their letterhead, and emit
templates/brand/3740582041351.html.

    python scripts/extract_bilal_brothers_brand.py

They sent a phone photograph of a blank letterhead -- the third client to send
a photo rather than a file -- so scripts/extract_brand.py cannot run: there is
no page to pull objects out of. The sheet is about 870 px across 210 mm, a
little over 100 DPI, which is below even the F.K. Printers photo that could
not be rescued.

So almost nothing is lifted. Everything on their letterhead except the crest
is *type*:

  * "BILAL BROTHERS"                           bold italic serif, letterspaced
  * "Govt Contractor & General Order Suppliers" bold italic serif
  * the "Deals in : ..." paragraph              regular sans
  * the double rules above and below the strapline
  * the footer rule and address line

All of it is set as text in the template, in the same faces, which prints
sharp at any size and carries none of the photo's lighting, skew or JPEG
noise. This is the SAAD Enterprises approach: draw what is flat, lift only
what is genuinely artwork.

The crest is the one piece that is artwork -- an ornate shield with a scrolled
border and the firm's name in script inside it. It is lifted here:

  1. deskew by the 1.23 degrees measured off the sheet's top edge;
  2. divide out the lighting, so the paper is flat white across the frame
     rather than shading off towards the corners;
  3. upscale, then smoothstep the levels so the ink is solid black and the
     paper is solid white with the edges left anti-aliased.

The script *inside* the shield does not survive -- at 134 px across it was
never going to. It reads as texture, which is roughly how it reads on their
own sheet, and the same four words are set in type beside it anyway. The
shapes are still only about 160 DPI, so this is a stand-in: **ask the client
for their logo file and re-emit from it**, the way F.K. Printers was fixed.
Nothing in the template has to change when that happens.

Their address is NOT here. It is an FBR field, printed live from the payload
in the footer, so a change of premises cannot leave the sheet contradicting
what was submitted. Their mobile number is not in the payload and is fixed
text in the template.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from extract_brand import emit_brand_partial  # noqa: E402

USERNAME = "3740582041351"
SRC = os.path.join(ROOT, "templates", "user-invoices", "%s.jpeg" % USERNAME)
# Where the crop is dropped so it can be looked at. Delete the folder
# afterwards: the partial is the part that matters.
WORK = os.path.join(ROOT, "extracted", USERNAME)

# Measured off the sheet's top edge: it rises 15 px over 700 px to the right,
# so the page is turned a little anticlockwise and has to come back.
SKEW_DEG = -1.23

# The crest's box on the deskewed frame, found from the ink profile of the
# masthead band: columns 88-222, rows 92-234, with a little air around it.
CREST = (84, 88, 228, 240)

# Upscaling before thresholding keeps the smoothstep from stair-stepping the
# curves of the scrollwork.
SCALE = 6

# Levels. Ink on this photo reads about 110 after the lighting is divided out
# and clean paper about 240. Below LO is solid ink, above HI is solid paper,
# and in between the edge stays soft. Pitching LO at 120 keeps the shield's
# outline the thin double rule it is on their sheet; higher and it fattens
# into a single heavy stroke.
LO, HI = 120.0, 200.0

# Everything at or above this is forced to pure white before quantizing; see
# the note in crest().
PAPER_SNAP = 240.0


def flatten(im):
    """Divide out the lighting so the paper is flat white across the frame.

    A phone photo of a sheet is brighter where the lamp is. A single global
    threshold then either drops the ink in the dark corner or turns the bright
    corner's paper grey, which prints as a panel behind the crest.
    """
    bg = im.filter(ImageFilter.MaxFilter(21)).filter(ImageFilter.GaussianBlur(21))
    a = np.asarray(im).astype(np.float32)
    b = np.asarray(bg).astype(np.float32)
    return np.clip(a / np.maximum(b, 1.0) * 255.0, 0, 255)


def crest():
    im = Image.open(SRC).convert("L")
    im = im.rotate(SKEW_DEG, resample=Image.BICUBIC, fillcolor=255)
    norm = Image.fromarray(flatten(im).astype(np.uint8))

    art = norm.crop(CREST)
    art = art.resize((art.width * SCALE, art.height * SCALE), Image.LANCZOS)

    a = np.asarray(art).astype(np.float32)
    t = np.clip((a - LO) / (HI - LO), 0, 1)
    t = t * t * (3 - 2 * t)          # smoothstep: no aliasing on the curves
    t = t * 255.0

    # Snap the last of the near-white to pure white. The smoothstep already
    # takes clean paper to 255, but the anti-aliased pixels just under it
    # share an octree node with it and pull the node's average down to 254 --
    # which prints as a faint panel behind the crest on an otherwise white
    # page. Same wrinkle as extract_letterhead_brand.py's level snap.
    t[t >= PAPER_SNAP] = 255.0
    art = Image.fromarray(t.astype(np.uint8)).convert("RGB")

    # Trim to the ink so the template can size the crest by its own width
    # without guessing at the padding left by the crop box above.
    mask = art.convert("L").point(lambda v: 255 if v < 200 else 0)
    box = mask.getbbox()
    if box:
        art = art.crop(box)
    return art


def main():
    if not os.path.exists(SRC):
        raise SystemExit("No such letterhead: %s" % SRC)
    os.makedirs(WORK, exist_ok=True)

    art = crest()
    cropped = os.path.join(WORK, "crest.png")
    art.save(cropped)

    note = (
        "Source: templates/user-invoices/%s.jpeg, a photograph of their blank\n"
        "  letterhead. Only the crest is lifted -- the wordmark, strapline,\n"
        "  rules, 'Deals in' paragraph and footer are all type and are set as\n"
        "  text in the template, which prints sharp and carries none of the\n"
        "  photo's lighting or skew.\n"
        "\n"
        "  The crest is about 160 DPI and the script inside the shield is a\n"
        "  blur at that size. STAND-IN: ask the client for their logo file and\n"
        "  re-emit -- nothing in the template has to change.\n"
        "  Re-emit with: python scripts/extract_bilal_brothers_brand.py\n"
        "\n"
        "  Their address is NOT here. It is an FBR field, printed live from the\n"
        "  payload in the footer, so a change of premises cannot leave the\n"
        "  sheet contradicting what was filed."
    ) % USERNAME

    # Black line art on white: octree at 8 keeps the black black. Median cut
    # would spend the palette on the anti-aliased greys and print the crest a
    # muddy olive -- the Paper Land lesson.
    path, size = emit_brand_partial(USERNAME, masthead=cropped, note=note,
                                    octree=True, colours=8)
    print("%-14s %4dx%-4d -> %s (%d KB)"
          % (USERNAME, art.width, art.height,
             os.path.relpath(path, ROOT), size // 1024))


if __name__ == "__main__":
    main()
