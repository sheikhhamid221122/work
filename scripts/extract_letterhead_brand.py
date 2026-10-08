# -*- coding: utf-8 -*-
"""Build a brand partial from a letterhead image the client supplied.

    python scripts/extract_letterhead_brand.py

scripts/extract_brand.py pulls artwork out of an invoice PDF. These clients
sent the letterhead itself -- flat black line art, already at print
resolution -- so there is nothing to pull out and nothing to rescue. All that
is needed is the crop.

The crop is the whole point. Both of these letterheads end in a reversed black
bar carrying the seller's ADDRESS, and under it the words "Sales Tax Invoice".

  * The address is an FBR field. Frozen into a picture it can silently
    contradict the payload the day they move premises, so the bar is cropped
    off and the address is printed live from `data` by the template. This is
    the warning in CLIENT_INVOICE_TEMPLATES.md, and it is the usual shape of
    an Excel- or Word-built letterhead.
  * "Sales Tax Invoice" is the document's title, not stationery. The template
    sets it as text, so a credit or debit note can say what it actually is.

Their phone numbers stay in the artwork: they are not FBR fields, they are
part of the design, and they are not in the invoice payload either.
"""
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from extract_brand import emit_brand_partial  # noqa: E402

# Where the cropped masthead is dropped so it can be looked at. Delete the
# folder afterwards: the partial is the part that matters.
WORK = os.path.join(ROOT, "extracted")

# username -> (letterhead file under templates/user-invoices/, first row of the
# address bar, note)
# The cut rows come from the ink profile of each file: the last row of real
# artwork, then blank, then the reversed address bar.
JOBS = {
    "3740545496385": (
        "3740545496385-letterhead.png", 298,
        "Source: the letterhead PNG the client supplied, 2170 px across the\n"
        "  sheet (about 262 DPI). Cropped at the address bar: their address and\n"
        "  mobile number were reversed out of a black bar below this artwork,\n"
        "  and the address is an FBR field that has to print live from the\n"
        "  payload. Their 'Sales Tax Invoice' line is cropped off too -- the\n"
        "  template sets the title as text.",
    ),
    "3740549685449": (
        "3740549685449-letterhead.png", 233,
        "Source: the letterhead PNG the client supplied, 2170 px across the\n"
        "  sheet (about 262 DPI). Cropped at the address bar: their shop address\n"
        "  was reversed out of a black bar below this artwork, and the address is\n"
        "  an FBR field that has to print live from the payload. Their 'Sales Tax\n"
        "  Invoice' line is cropped off too -- the template sets the title as\n"
        "  text. The Cell / Ph block at the top right is kept: a phone number is\n"
        "  not an FBR field, it is part of their design, and it is not in the\n"
        "  invoice payload.",
    ),
}


def flatten_and_crop(src, cut):
    """White background, cropped above the address bar, trimmed to the ink."""
    im = Image.open(src).convert("RGBA")
    white = Image.new("RGBA", im.size, (255, 255, 255, 255))
    im = Image.alpha_composite(white, im).convert("RGB")
    im = im.crop((0, 0, im.width, cut))

    # Snap the paper to pure white and the ink to pure black, leaving the
    # anti-aliased edges in between. The files read 252-255 on the paper, and
    # an 8-colour octree rounds that to a near-white grey -- which prints as a
    # visible panel behind the masthead on an otherwise white page.
    arr = np.asarray(im).astype(np.float32)
    arr = np.clip((arr - 60.0) / (238.0 - 60.0) * 255.0, 0, 255)
    im = Image.fromarray(arr.astype(np.uint8))

    # Trim the surrounding white so the template can size the artwork by its
    # own width without guessing at the padding baked into the file.
    grey = im.convert("L")
    mask = grey.point(lambda v: 255 if v < 200 else 0)
    box = mask.getbbox()
    if box:
        im = im.crop(box)
    return im


def main():
    os.makedirs(WORK, exist_ok=True)
    for username, (name, cut, note) in JOBS.items():
        src = os.path.join(ROOT, "templates", "user-invoices", name)
        if not os.path.exists(src):
            raise SystemExit("No such letterhead: %s" % src)

        art = flatten_and_crop(src, cut)
        cropped = os.path.join(WORK, "%s-masthead.png" % username)
        art.save(cropped)

        # Black line art on white: octree at 8 keeps the black black. Median
        # cut would spend the palette on the anti-aliased greys and print the
        # wordmark a muddy olive -- the Paper Land lesson.
        path, size = emit_brand_partial(username, masthead=cropped, note=note,
                                        octree=True, colours=8)
        print("%-14s %-32s %4dx%-4d -> %s (%d KB)"
              % (username, name, art.width, art.height,
                 os.path.relpath(path, ROOT), size // 1024))


if __name__ == "__main__":
    main()
