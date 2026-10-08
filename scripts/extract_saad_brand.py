# -*- coding: utf-8 -*-
"""Lift SAAD Enterprises' monogram and wordmark out of the photo of their
invoice, and emit templates/brand/3520287506167.html.

    python scripts/extract_saad_brand.py

There is no PDF of their invoice, only a phone photograph, so
scripts/extract_brand.py cannot run: there is no page to pull objects out of.
Everything around the lettering -- the black banner, the orange band, the grey
shadow, the services box, the footer bars -- is flat colour and is drawn in the
template instead, so it prints sharp and carries none of the photo's lighting
or skew. Only the two pieces that are genuinely artwork are lifted here.

They are not lifted as pixels. A photographed letter is a muddle of the ink,
the paper's lighting and JPEG noise, and pasting that onto the drawn banner
showed every bit of it. Instead the crop is separated into coverage (how much
ink is at this pixel) and hue (orange wordmark or white monogram stroke), and
the lettering is re-drawn from those in the brand's own two colours on the
exact black the template draws. What survives from the photo is the shape of
the letters; the colour and the background are clean.

The shapes are still only about 140 DPI, so this is a stand-in: ask the client
for their logo file and re-emit from it, the way F.K. Printers was fixed.
"""
import base64
import io
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "templates", "user-invoices", "3520287506167.jpeg")
PARTIAL = os.path.join(ROOT, "templates", "brand", "3520287506167.html")
# Where the two pieces are dropped so they can be looked at. Delete the folder
# afterwards: the partial is the part that matters.
PROOF = os.path.join(ROOT, "extracted", "3520287506167")

SKEW_DEG = 0.26             # measured off the sheet's long horizontal rules
BANNER = np.array([45, 44, 42], dtype=np.float32)    # the drawn banner's black
ORANGE = np.array([180, 110, 84], dtype=np.float32)  # brand orange, off the top band
WHITE = np.array([255, 255, 255], dtype=np.float32)

# Coverage: background reads lum ~33, an orange letter core ~110, a white
# monogram stroke ~176. Everything under INK_LO is paper-black, everything
# over INK_HI is solid ink.
INK_LO, INK_HI = 44.0, 104.0
# Chroma of a solid orange stroke (r - b). White strokes read near zero.
CHROMA = 52.0
SCALE = 4
COLOURS = 16

# Both boxes lie wholly inside the black banner at their own heights. One box
# covering both would stick out past the banner's diagonal at the top right.
CROPS = {
    "monogram": (222, 16, 350, 98),
    "wordmark": (38, 98, 516, 160),
}


def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def redraw(crop):
    """Re-draw the lettering in the brand's colours on the banner black."""
    arr = np.asarray(crop).astype(np.float32)
    lum = arr.mean(axis=2)

    alpha = smoothstep((lum - INK_LO) / (INK_HI - INK_LO))
    # Chroma per unit of coverage, so a half-covered orange edge still reads as
    # orange rather than drifting pink.
    chroma = (arr[:, :, 0] - arr[:, :, 2]) / np.maximum(alpha, 0.25)
    whiteness = 1.0 - smoothstep(chroma / CHROMA)

    # Upscale the two fields rather than the pixels: there is no colour to ring
    # around an edge, and the curve below re-steepens what interpolation
    # softened.
    def up(field):
        im = Image.fromarray((np.clip(field, 0, 1) * 255).astype(np.uint8))
        im = im.resize((im.width * SCALE, im.height * SCALE), Image.LANCZOS)
        return np.asarray(im).astype(np.float32) / 255.0

    alpha = smoothstep((up(alpha) - 0.42) / 0.30)
    whiteness = up(whiteness)

    ink = ORANGE[None, None, :] + (WHITE - ORANGE)[None, None, :] * whiteness[:, :, None]
    out = BANNER[None, None, :] * (1 - alpha[:, :, None]) + ink * alpha[:, :, None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def data_uri(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    os.makedirs(PROOF, exist_ok=True)
    photo = Image.open(SRC).convert("RGB")
    photo = photo.rotate(SKEW_DEG, resample=Image.BICUBIC, center=(0, 0),
                         fillcolor=(255, 255, 255))

    pieces = {}
    for name, box in CROPS.items():
        art = redraw(photo.crop(box))
        art = art.quantize(colors=COLOURS, method=Image.Quantize.FASTOCTREE).convert("RGB")
        art.save(os.path.join(PROOF, "saad-%s.png" % name))
        pieces[name] = art
        print("%-9s %s" % (name, art.size))

    body = '''{#
  Brand artwork for 3520287506167 (SAAD Enterprises).

  Inlined as data URIs on purpose. app.py renders invoice PDFs with no
  base_url, so a "/static/..." src silently resolves to nothing on some
  routes and prints the alt text instead. Artwork that travels inside the
  template cannot fail that way.

  Source: a phone PHOTOGRAPH of their invoice. They sent no PDF and no logo
  file, so scripts/extract_brand.py could not run -- there is no page to lift
  objects out of. Only the two pieces that are genuinely artwork are here.
  Everything else on their letterhead is flat colour and is drawn in the
  template, so it prints sharp instead of carrying the photo's lighting and
  skew.

  These two are not photographed pixels either: the crop was separated into
  coverage and hue and the lettering re-drawn from those in the brand's own
  orange and white on the exact black the template draws. What survives from
  the photo is the SHAPE of the letters, which is only about 140 DPI -- so
  this is a stand-in. Ask the client for their logo file and re-emit from it;
  that is what F.K. Printers needed and it took one message.
  Re-emit with: python scripts/extract_saad_brand.py

  Their address, phone and email are NOT here. They live in the footer, and
  the address is an FBR field printed live from the payload, so a change of
  premises cannot leave the sheet contradicting what was submitted.
#}

{% macro monogram(alt='') %}
<img class="brand-monogram" alt="{{ alt }}" src="__MONOGRAM__">
{%- endmacro %}

{% macro wordmark(alt='') %}
<img class="brand-wordmark" alt="{{ alt }}" src="__WORDMARK__">
{%- endmacro %}
'''
    body = body.replace("__MONOGRAM__", data_uri(pieces["monogram"]))
    body = body.replace("__WORDMARK__", data_uri(pieces["wordmark"]))
    with io.open(PARTIAL, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    print("wrote", PARTIAL, os.path.getsize(PARTIAL), "bytes")


if __name__ == "__main__":
    main()
