"""Pull a client's logo and masthead out of an invoice they already send.

Why extraction rather than OCR or an image model
------------------------------------------------
A PDF is not a picture of a document -- it is a container that still holds the
artwork that was placed into it. The client's logo is sitting inside their
invoice as an image object, at its original resolution. Taking it out gives
back the exact file their designer made.

OCR reads text and cannot reproduce artwork at all. An image model would draw
something that resembles the logo, which is both worse and the wrong thing to
do with a company's trademark. Neither is needed: the real thing is already in
the file.

Two ways out, because logos are built two ways
----------------------------------------------
`--images`  extracts every embedded raster image, at original resolution.
            This is the exact original file when the logo was placed as a
            bitmap, which is what an Excel- or Word-built invoice almost
            always contains.

`--band`    renders the top of the page at high DPI and crops it. This works
            no matter how the masthead is constructed -- vector art, live
            text, a table with borders, or all three -- and is the right answer
            when there is no single image to pull out. 600 DPI is well past
            what any printer resolves, so it is exact at print size.

Run both and keep whichever is cleaner; they answer different situations.

Usage
-----
    python scripts/extract_brand.py INVOICE.pdf --images
    python scripts/extract_brand.py INVOICE.pdf --band 40
    python scripts/extract_brand.py INVOICE.pdf --images --band 40 --username 3520224169621

With --username the chosen file is written into static/uploads/logos/ and the
SQL to attach it is printed.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pypdfium2
from pypdfium2 import raw as pdfium_raw
from PIL import Image

MM_PER_INCH = 25.4
BAND_DPI = 600           # far past print resolution; the crop is exact at size
RENDER_DPI = 600         # what logo crops are lifted at
MIN_LOGO_PX = 40         # ignore hairlines, bullets and 1px spacer images


def _page_geometry(page):
    width_pt, height_pt = page.get_size()
    return (width_pt / 72 * MM_PER_INCH, height_pt / 72 * MM_PER_INCH,
            width_pt, height_pt)


def extract_images(pdf, out_dir, page_index=0, top_mm=None):
    """The artwork in the masthead, composited exactly as the page shows it.

    Each image object gives its rectangle on the page; the page is rendered
    once at RENDER_DPI and each rectangle cropped out of it.

    Why not lift the embedded stream instead
    ----------------------------------------
    Because the stream is frequently only half the picture. A logo with a
    cut-out background stores its transparency as a *separate* soft-mask
    object, so the raw stream comes back with the masked area filled -- black,
    in the first file this was tried on, which is precisely the wrong colour
    for artwork going onto white paper. pdfium's own per-object render applies
    the mask but only at the size the object occupies on the page, throwing
    away resolution.

    Cropping a high-DPI render of the page sidesteps both: the mask, any
    blending and any vector content are all resolved by the renderer, and the
    resolution is whatever we ask for. The original pixel dimensions are still
    reported, so you can see what you started with.
    """
    page = pdf[page_index]
    page_w_mm, page_h_mm, width_pt, height_pt = _page_geometry(page)
    os.makedirs(out_dir, exist_ok=True)

    scale = RENDER_DPI / 72
    sheet = page.render(scale=scale).to_pil().convert("RGB")

    found = []
    for index, obj in enumerate(page.get_objects()):
        if obj.type != pdfium_raw.FPDF_PAGEOBJ_IMAGE:
            continue

        # PDF coordinates start at the BOTTOM of the page; invert so the
        # numbers read the way the eye does.
        left, bottom, right, top = obj.get_pos()
        top_edge_mm = (height_pt - top) / 72 * MM_PER_INCH
        placed_w_mm = (right - left) / 72 * MM_PER_INCH
        placed_h_mm = (top - bottom) / 72 * MM_PER_INCH

        if top_mm is not None and top_edge_mm > top_mm:
            continue
        if placed_w_mm < 3 or placed_h_mm < 3:
            continue                      # rules, spacers, hairlines

        box = (max(0, int(left * scale)),
               max(0, int((height_pt - top) * scale)),
               min(sheet.width, int(round(right * scale))),
               min(sheet.height, int(round((height_pt - bottom) * scale))))
        if box[2] - box[0] < MIN_LOGO_PX or box[3] - box[1] < MIN_LOGO_PX:
            continue

        crop = sheet.crop(box)
        name = f"logo-{index:02d}-{crop.width}x{crop.height}.png"
        path = os.path.join(out_dir, name)
        crop.save(path)

        # What the PDF actually stores, for information: if this is far larger
        # than the crop, raise RENDER_DPI and run again.
        try:
            native = obj.get_bitmap().to_pil().size
        except Exception:
            native = None

        found.append({
            "path": path,
            "px": crop.size,
            "native": native,
            "placed_mm": (round(placed_w_mm, 1), round(placed_h_mm, 1)),
            "top_mm": round(top_edge_mm, 1),
            "dpi": RENDER_DPI,
        })

    found.sort(key=lambda f: -(f["placed_mm"][0] * f["placed_mm"][1]))
    return found


def extract_band(pdf, out_dir, mm, page_index=0):
    """The top `mm` of the page, rendered at BAND_DPI and cropped."""
    page = pdf[page_index]
    _, page_h_mm, _, _ = _page_geometry(page)
    os.makedirs(out_dir, exist_ok=True)

    image = page.render(scale=BAND_DPI / 72).to_pil().convert("RGB")
    px = max(1, min(image.height, int(round(image.height * mm / page_h_mm))))
    crop = image.crop((0, 0, image.width, px))
    path = os.path.join(out_dir, f"masthead-{int(mm)}mm.png")
    crop.save(path)
    return {"path": path, "px": crop.size, "mm": mm, "dpi": BAND_DPI}


def trim_whitespace(path, tolerance=248):
    """Crop uniform white margins off an extracted logo.

    An image placed in Excel usually carries a generous white box around the
    artwork. Left alone it makes the logo look small and badly centred once it
    is dropped into a masthead that sizes by height.
    """
    image = Image.open(path)
    image.load()
    width, height = image.size

    # A pixel is "blank" if it is transparent OR near-white. Checking only
    # brightness would keep a wide transparent margin around a cut-out logo.
    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    probe = image.convert("RGBA")
    pixels = probe.load()

    def blank(x, y):
        r, g, b, a = pixels[x, y]
        return a == 0 or min(r, g, b) >= tolerance

    def blank_row(y):
        return all(blank(x, y) for x in range(width))

    def blank_col(x):
        return all(blank(x, y) for y in range(height))

    top, bottom = 0, height - 1
    while top < bottom and blank_row(top):
        top += 1
    while bottom > top and blank_row(bottom):
        bottom -= 1
    left, right = 0, width - 1
    while left < right and blank_col(left):
        left += 1
    while right > left and blank_col(right):
        right -= 1

    if (left, top, right, bottom) == (0, 0, width - 1, height - 1):
        return path, image.size
    trimmed = (probe if has_alpha else image).crop((left, top, right + 1, bottom + 1))
    out = path.replace(".png", "-trimmed.png")
    trimmed.save(out)
    return out, trimmed.size


BRAND_DIR = os.path.join(ROOT, "templates", "brand")

# Inlining costs bytes, so give the artwork only as much resolution as paper
# can use. 16 colours is ample for a logo: they are flat-colour artwork, not
# photographs, and quantising one cuts it to about a seventh of its size.
BRAND_COLOURS = 16


def _data_uri(path, colours=BRAND_COLOURS, octree=False):
    image = Image.open(path).convert("RGB")
    buffer = io.BytesIO()
    # Median cut splits the palette by pixel count. On a masthead that is
    # mostly pale ornament -- Paper Land's -- it spends all 16 colours on
    # greys, and a black wordmark comes out olive. Octree keeps the extremes.
    method = Image.FASTOCTREE if octree else Image.MEDIANCUT
    image.quantize(colors=colours, method=method).save(
        buffer, format="PNG", optimize=True)
    raw = buffer.getvalue()
    return ("data:image/png;base64," + base64.b64encode(raw).decode()), len(raw)


def classify(found):
    """Guess which extracted image is the masthead and which the strapline.

    By shape, because that is what actually distinguishes them: a masthead is
    wide and short and sits at the top; a strapline is a tall narrow ribbon
    down an edge. Both are overridable, and should be checked by eye.
    """
    masthead = strapline = None
    for item in found:
        w_mm, h_mm = item["placed_mm"]
        if h_mm > w_mm * 3:
            if strapline is None or h_mm > strapline["placed_mm"][1]:
                strapline = item
        elif w_mm > h_mm and item["top_mm"] < 60:
            if masthead is None or w_mm > masthead["placed_mm"][0]:
                masthead = item
    return masthead, strapline


def emit_brand_partial(username, masthead=None, strapline=None, note="",
                       octree=False, colours=BRAND_COLOURS):
    """Write templates/brand/<username>.html with the artwork inlined.

    Why inline rather than link
    ---------------------------
    app.py hands WeasyPrint the invoice HTML with no base_url on its PDF
    paths, so a "/static/..." src resolves to nothing and the alt text prints
    instead -- and only on some routes, which is the worst way for it to fail.
    A client's identity should not depend on which button produced the PDF, so
    the artwork travels inside the template.
    """
    os.makedirs(BRAND_DIR, exist_ok=True)
    path = os.path.join(BRAND_DIR, f"{username}.html")

    lines = [
        "{#",
        f"  Brand artwork for {username}, lifted from their own invoice by",
        "  scripts/extract_brand.py. Do not hand-edit: re-run the script.",
        "",
        "  Inlined as data URIs on purpose. app.py renders invoice PDFs with no",
        '  base_url, so a "/static/..." src silently resolves to nothing on some',
        "  routes and prints the alt text instead. Artwork that travels inside",
        "  the template cannot fail that way.",
    ]
    if note:
        lines += ["", "  " + note]
    lines += ["#}"]

    total = 0
    for name, source, alt in (("masthead", masthead, "masthead"),
                              ("strapline", strapline, "strapline")):
        if not source:
            continue
        uri, size = _data_uri(source, colours=colours, octree=octree)
        total += size
        lines.append("")
        lines.append("{%% macro %s(alt='') %%}" % name)
        lines.append('<img class="brand-%s" alt="{{ alt }}" src="%s">' % (alt, uri))
        lines.append("{% endmacro %}")

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path, total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="the client's existing invoice")
    parser.add_argument("--images", action="store_true",
                        help="extract embedded raster images at original resolution")
    parser.add_argument("--band", type=float, metavar="MM",
                        help="also crop the top MM of the page at %d DPI" % BAND_DPI)
    parser.add_argument("--top-mm", type=float, default=60.0,
                        help="only consider images within this far of the page top")
    parser.add_argument("--out", default=None, help="output directory")
    parser.add_argument("--username", help="install the best candidate for this client")
    parser.add_argument("--trim", action="store_true", default=True,
                        help="crop white margins off extracted images (default)")
    parser.add_argument("--emit-brand", action="store_true",
                        help="write templates/brand/<username>.html with the "
                             "artwork inlined (needs --username)")
    parser.add_argument("--masthead", help="use this file as the masthead "
                                           "instead of the auto-detected one")
    parser.add_argument("--strapline", help="use this file as the strapline")
    parser.add_argument("--octree", action="store_true",
                        help="quantise with octree instead of median cut -- use "
                             "when a dark logo comes out washed-out or tinted")
    parser.add_argument("--colours", type=int, default=BRAND_COLOURS,
                        help="palette size for the inlined artwork (default "
                             "%(default)s); raise it when faint shading "
                             "flattens into a flat tint")
    args = parser.parse_args()

    if not (args.images or args.band):
        args.images = True

    if not os.path.exists(args.pdf):
        raise SystemExit(f"No such file: {args.pdf}")

    out_dir = args.out or os.path.join(ROOT, "extracted",
                                       os.path.splitext(os.path.basename(args.pdf))[0])
    pdf = pypdfium2.PdfDocument(args.pdf)
    try:
        page_w_mm, page_h_mm, _, _ = _page_geometry(pdf[0])
        print(f"{os.path.basename(args.pdf)}: {len(pdf)} page(s), "
              f"{page_w_mm:.0f} x {page_h_mm:.0f} mm\n")

        best = None
        if args.images:
            found = extract_images(pdf, out_dir, top_mm=args.top_mm)
            if not found:
                print("  no embedded raster images in the masthead area.")
                print("  The logo is probably vector art or live text -- use --band.\n")
            for item in found:
                print(f"  {os.path.basename(item['path'])}")
                native = (f", embedded {item['native'][0]}x{item['native'][1]}"
                          if item["native"] else "")
                print(f"      {item['px'][0]}x{item['px'][1]} px at "
                      f"{item['dpi']} DPI{native}")
                print(f"      sits {item['placed_mm'][0]}x{item['placed_mm'][1]} mm "
                      f"on the page, {item['top_mm']} mm from the top")
                if args.trim:
                    trimmed, size = trim_whitespace(item["path"])
                    if trimmed != item["path"]:
                        print(f"      trimmed -> {os.path.basename(trimmed)} "
                              f"({size[0]}x{size[1]} px)")
                        item["path"] = trimmed
            if found:
                best = found[0]["path"]
                print()

        if args.band:
            band = extract_band(pdf, out_dir, args.band)
            print(f"  {os.path.basename(band['path'])}")
            print(f"      {band['px'][0]}x{band['px'][1]} px, top {band['mm']}mm "
                  f"at {band['dpi']} DPI\n")
            best = best or band["path"]
    finally:
        pdf.close()

    print(f"written to {out_dir}")

    if args.emit_brand:
        if not args.username:
            raise SystemExit("--emit-brand needs --username")
        mast = args.masthead
        strap = args.strapline
        if (mast is None or strap is None) and args.images:
            auto_m, auto_s = classify(found)
            mast = mast or (auto_m and auto_m["path"])
            strap = strap or (auto_s and auto_s["path"])
        if not mast:
            raise SystemExit(
                "No masthead found. Pass --masthead FILE, or run with --band "
                "and pass the cropped strip.")
        path, size = emit_brand_partial(args.username, mast, strap,
                                        octree=args.octree,
                                        colours=args.colours)
        print(f"\nbrand partial -> {os.path.relpath(path, ROOT)} "
              f"({size // 1024} KB of artwork inlined)")
        print("\nUse it from the client's invoice template:\n")
        print(f'  {{% import "brand/{args.username}.html" as brand %}}')
        print("  {{ brand.masthead() }}")
        if strap:
            print("  {{ brand.strapline() }}")
        return

    if args.username and best:
        logos = os.path.join(ROOT, "static", "uploads", "logos")
        os.makedirs(logos, exist_ok=True)
        target = os.path.join(logos, f"{args.username}-logo.png")
        Image.open(best).save(target)
        url = f"/static/uploads/logos/{args.username}-logo.png"
        print(f"\ninstalled {os.path.basename(best)} -> {url}\n")
        print("Attach it to the client:\n")
        print("UPDATE clients c")
        print(f"   SET logo_url = '{url}'")
        print("  FROM users u")
        print(" WHERE u.id = c.user_id")
        print(f"   AND u.username = '{args.username}';")


if __name__ == "__main__":
    main()
