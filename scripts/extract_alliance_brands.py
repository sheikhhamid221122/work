# -*- coding: utf-8 -*-
"""Lift Aero Alliance's and Beacon Alliance's artwork out of the photographs
of their letterheads, and emit templates/brand/3740564149343.html and
templates/brand/3740545582835.html.

    python scripts/extract_alliance_brands.py

Both clients sent a phone photograph of a blank letterhead, like Bilal
Brothers before them -- 840 and 891 px across 210 mm, about 102 and 108 DPI.
scripts/extract_brand.py cannot run on either: there is no page to pull
objects out of.

So the same rule applies as for Bilal Brothers: **draw what is type, lift only
what is genuinely artwork.** On both sheets the whole masthead -- wordmark,
strapline, "Deals in" lines, address, phone and e-mail -- is type, and all of
it is set as text in the templates, in the faces and at the proportions
measured off the sheets. What is left is the logo on each, which is real
artwork and has to be lifted.

What makes these two different from Bilal Brothers is that the artwork is in
COLOUR, so the black-line-art pipeline there does not apply:

  * the lighting is divided out PER CHANNEL rather than on luminance, which
    both flattens the lamp's falloff and white-balances the paper, so the blue
    does not come out muddy where the sheet was in shadow;
  * JPEG stores colour at half resolution, so small coloured detail arrives
    desaturated. Saturation is restored in HSV, but only on pixels that are
    already coloured and not near-white, so the paper cannot pick up a tint;
  * the quantizer gets 64 colours instead of 8, because a globe with
    continents and a two-tone swoosh have real tonal range.

Both logos are about 105 DPI and both are therefore STAND-INS: **ask each
client for their logo file and re-run this script.** Nothing in either
template has to change.

Aero Alliance (3740564149343)
-----------------------------
One piece: the eagle head, the "AERO ALLIANCE / Precision & Accuracy"
logotype and the blue wing below it, lifted whole. The logotype text inside it
is the brand's own mark, not an FBR field, so it stays.

Their address ("Office No. F-13, 1st Floor Ramzan Plaza, Bank Road, Saddar
Rawalpindi.") is a line of type in the masthead, NOT part of the logo, and it
prints live from the payload. Their Ref. No. / Date rules and the blue rubber
stamp near the foot are not reproduced -- the rules are the blank sheet's
correspondence fields, and the stamp is ink they applied by hand.

Beacon Alliance (3740545582835)
-------------------------------
Two pieces:

  * `masthead` -- the globe, the red-and-yellow swoosh, the aeroplane and the
    "Beacon Alliance / Our passion is in precision and accuracy" logotype.
    The template also uses this one as the big pale WATERMARK in the middle of
    their sheet, scaled up and faded, because that watermark is this same
    artwork. Re-using it prints far better than lifting the washed-out pixels.
  * `partners` -- the row of distributor marks across the foot of their sheet
    (Pro'sKit / DORMER, elme / eurosaft, PPG, TOKU / FLUKE, TOPTUL /
    Snap-on).

Their rubber stamp was pressed just above the right-hand end of that row, and
its tail reaches into the top of the band. It is masked out with paper white
before the band is emitted -- hand-applied ink must not end up looking like
part of their printed stationery.

The partner band is the weakest piece here: those are other companies' marks
at about 108 DPI, and they deserve the original file more than anything else
on either sheet.

Their address, NTN and STRN are type on the sheet and print live from the
payload -- all three are FBR fields.
"""
import io
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from extract_brand import _data_uri  # noqa: E402

BRAND_DIR = os.path.join(ROOT, "templates", "brand")
SRC_DIR = os.path.join(ROOT, "templates", "user-invoices")

# Where the crops are dropped so they can be looked at. Delete the folder
# afterwards: the partials are the part that matters.
WORK = os.path.join(ROOT, "extracted")

SCALE = 4                 # upscale before cleaning, so edges do not stair-step
PAPER_SNAP = 243.0        # at or above this -> pure white, no grey panel
# The crop's own white point, as a percentile of its pixels. Each crop is
# more than half paper, so this lands on paper and clips the rest of the
# paper to pure white -- which is the point. Set it too high and a cluster
# of near-whites survives just under 255, shares an octree node with it and
# prints as a cream panel behind the artwork.
PAPER_PCT = 88.0
SAT_GAIN = 1.45           # restores what JPEG chroma subsampling took out
SAT_FLOOR = 0.10          # below this a pixel is neutral and is left alone
# The shoulder that takes the last of the paper to white, applied to a pixel's
# WEAKEST channel so that coloured ink is spared: a pale yellow has a low blue
# channel and survives, while a grey cast in the paper does not. Without it the
# photo's uneven shading leaves cream blotches round the artwork.
SHOULDER_LO, SHOULDER_HI = 214.0, 240.0


def flatten(im):
    """Divide the lighting out of each channel.

    On a phone photo the lamp falls off across the sheet, and the paper is
    rarely neutral. Dividing each channel by its own smoothed local maximum
    does both jobs at once: the paper goes to white everywhere, and the ink
    keeps the colour it actually is rather than the colour the lamp made it.
    """
    a = np.asarray(im).astype(np.float32)
    paper = np.stack([
        np.asarray(Image.fromarray(a[:, :, c].astype(np.uint8))
                   .filter(ImageFilter.MaxFilter(25))
                   .filter(ImageFilter.GaussianBlur(25))).astype(np.float32)
        for c in range(3)], axis=2)
    out = np.clip(a / np.maximum(paper, 1.0) * 255.0, 0, 255)

    # The estimate is a *blurred* maximum, so it reads a little under the true
    # paper and the division lands at about 248 rather than 255 -- and lands
    # there unevenly, which leaves a warm cast. Pin the white point: whatever
    # the 99th percentile of each channel is, that is paper, and paper is 255.
    white = np.percentile(out.reshape(-1, 3), 99, axis=0)
    return np.clip(out * (255.0 / np.maximum(white, 1.0)), 0, 255)


def resaturate(arr):
    """Put back the chroma JPEG threw away, without tinting the paper.

    4:2:0 subsampling stores colour at half resolution, so anything small and
    coloured -- a 2 mm swoosh, a thin navy serif -- arrives washed out. The
    gain is applied only where there is colour to begin with and the pixel is
    not nearly white, so paper and black stay exactly where they are.
    """
    a = arr / 255.0
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    live = (sat > SAT_FLOOR) & (mx < 0.97)
    # Pull each channel away from the pixel's own brightness.
    grey = a.mean(axis=2, keepdims=True)
    boosted = np.clip(grey + (a - grey) * SAT_GAIN, 0, 1)
    out = np.where(live[:, :, None], boosted, a)
    return out * 255.0


def piece(src, angle, box, mask=None):
    """Deskew, flatten, crop, clean and upscale one piece of artwork."""
    im = Image.open(src).convert("RGB")
    im = im.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    flat = Image.fromarray(flatten(im).astype(np.uint8))

    art = flat.crop(box)
    if mask:
        # Paint a rectangle of paper white over something that is not part of
        # the stationery -- a rubber stamp pressed across the artwork.
        x0, y0, x1, y1 = (mask[0] - box[0], mask[1] - box[1],
                          mask[2] - box[0], mask[3] - box[1])
        px = np.asarray(art).copy()
        px[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 255
        art = Image.fromarray(px)

    # White-point the crop against its own paper. The page-wide division
    # leaves a little of the lamp's warmth behind wherever the sheet was in
    # shadow, and a global stretch cannot see a local deficit -- so the crop,
    # which is mostly paper, sets its own white. Without this the artwork
    # prints on a cream panel.
    px = np.asarray(art).astype(np.float32)
    white = np.percentile(px.reshape(-1, 3), PAPER_PCT, axis=0)
    px = np.clip(px * (255.0 / np.maximum(white, 1.0)), 0, 255)

    art = Image.fromarray(px.astype(np.uint8))
    art = art.resize((art.width * SCALE, art.height * SCALE), Image.LANCZOS)

    a = resaturate(np.asarray(art).astype(np.float32))
    # Snap the last of the near-white to pure white. Anti-aliased pixels just
    # under paper share an octree node with it and drag the node's average to
    # 254, which prints as a faint panel behind the artwork. Same wrinkle as
    # extract_bilal_brothers_brand.py.
    # Ramp the weakest channel towards white first, so a grey cast in the
    # paper goes but coloured ink, which always has one weak channel, stays.
    m = a.min(axis=2)
    t = np.clip((m - SHOULDER_LO) / (SHOULDER_HI - SHOULDER_LO), 0, 1)
    t = (t * t * (3 - 2 * t))[:, :, None]
    a = a * (1 - t) + 255.0 * t

    near = a.min(axis=2) >= PAPER_SNAP
    a[near] = 255.0
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def write_partial(username, note, pieces, colours=64):
    """templates/brand/<username>.html, one Jinja macro per piece.

    Inlined as data URIs on purpose: app.py renders invoice PDFs with no
    base_url, so a "/static/..." src silently resolves to nothing on some
    routes and prints the alt text instead.
    """
    os.makedirs(BRAND_DIR, exist_ok=True)
    path = os.path.join(BRAND_DIR, "%s.html" % username)
    lines = [
        "{#",
        "  Brand artwork for %s, lifted from the photograph of their" % username,
        "  letterhead by scripts/extract_alliance_brands.py.",
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
        uri, size = _data_uri(source, colours=colours, octree=True)
        total += size
        lines.append("")
        lines.append("{%% macro %s(alt='') %%}" % name)
        lines.append('<img class="brand-%s" alt="{{ alt }}" src="%s">' % (name, uri))
        lines.append("{% endmacro %}")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path, total


# username -> deskew angle, and the pieces to lift. Boxes are in pixels on the
# DESKEWED frame, measured from the ink profiles of each sheet.
AERO = "3740564149343"
BEACON = "3740545582835"

JOBS = [
    (AERO, 1.60, [
        # The whole logo: eagle head, logotype and the blue wing under it.
        # 53.8 x 41.2 mm on their sheet, sitting at 11.8 mm from the left edge.
        ("masthead", (62, 93, 281, 199), None),
    ], "Source: templates/user-invoices/%s.jpeg, a photograph of their blank\n"
       "letterhead (840 px across 210 mm, about 102 DPI).\n"
       "\n"
       "Only the logo is lifted. The AERO ALLIANCE wordmark, the GOVT\n"
       "CONTRACTOR strapline, the 'Deals in' line, the address and the phone\n"
       "and e-mail lines are all type and are set as text in the template,\n"
       "which prints sharp and carries none of the photo's lighting or skew.\n"
       "\n"
       "About 105 DPI. STAND-IN: ask the client for their logo file and\n"
       "re-run the script -- nothing in the template has to change.\n"
       "\n"
       "Their address is NOT here. It is an FBR field, printed live from the\n"
       "payload, so a change of premises cannot leave the sheet contradicting\n"
       "what was filed." % AERO),

    (BEACON, -1.10, [
        # Globe, swoosh, aeroplane and the logotype under them.
        # 41.0 x 29.9 mm on their sheet. The template also scales this up as
        # the pale watermark across the middle of the page.
        ("masthead", (72, 63, 256, 200), None),
        # The distributor marks across the foot, 181.9 x 22.9 mm. Their rubber
        # stamp's tail reaches into the top right of this band and is masked
        # out with paper white: hand-applied ink is not stationery.
        ("partners", (64, 1116, 844, 1217), (676, 1116, 844, 1134)),
    ], "Source: templates/user-invoices/%s.jpeg, a photograph of their blank\n"
       "letterhead (891 px across 210 mm, about 108 DPI).\n"
       "\n"
       "Only the logo and the distributor band are lifted. The BEACON ALLIANCE\n"
       "wordmark, the Importers/Exporters line, the Government Contractor\n"
       "line, the two 'Deals in' lines, the address, NTN, STRN, phone and\n"
       "e-mail are all type and are set as text in the template.\n"
       "\n"
       "`masthead` is also what the template scales up and fades for the\n"
       "watermark across the middle of their sheet -- that watermark is this\n"
       "same artwork, and re-using it prints better than lifting the\n"
       "washed-out pixels.\n"
       "\n"
       "`partners` had their rubber stamp pressed over its right-hand end; the\n"
       "stamp is masked out with paper white here.\n"
       "\n"
       "About 105 DPI, and the partner marks belong to other companies.\n"
       "STAND-IN: ask the client for their letterhead file and re-run the\n"
       "script -- nothing in the template has to change.\n"
       "\n"
       "Their address, NTN and STRN are NOT here. All three are FBR fields and\n"
       "print live from the payload." % BEACON),
]


def main():
    os.makedirs(WORK, exist_ok=True)
    for username, angle, specs, note in JOBS:
        src = os.path.join(SRC_DIR, "%s.jpeg" % username)
        if not os.path.exists(src):
            raise SystemExit("No such letterhead: %s" % src)
        out = os.path.join(WORK, username)
        os.makedirs(out, exist_ok=True)

        pieces = []
        for name, box, mask in specs:
            art = piece(src, angle, box, mask)
            where = os.path.join(out, "%s.png" % name)
            art.save(where)
            pieces.append((name, where))
            print("  %-9s %4d x %-4d" % (name, art.width, art.height))

        path, size = write_partial(username, note, pieces)
        print("%-14s -> %s (%d KB)"
              % (username, os.path.relpath(path, ROOT), size // 1024))


if __name__ == "__main__":
    main()
