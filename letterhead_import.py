"""Turn a client's existing invoice into a letterhead image.

Clients do not want to design an invoice. They want the invoice they already
send -- their masthead, their logo, their colours -- with the FBR compliance
marks added. Asking them to export that masthead as a correctly proportioned
PNG is asking them to do prepress. This module takes the artefact they
actually have (last month's invoice, as a PDF or a scan), renders page one,
finds where the masthead ends, and crops it.

Why only the masthead
---------------------
The body of an FBR sales tax invoice is not the client's to design. The item
columns, the tax breakdown and the totals are determined by the DI API, and
the QR / DI logo / FBR invoice number block is mandatory. So reconstructing
their *layout* would be reconstructing something we then have to override.
What is genuinely theirs is the band at the top of the page, and that is what
this extracts.

Fidelity
--------
The crop always starts at the physical top of the page rather than at the
first pixel of ink, and it keeps the full page width. Both are deliberate: an
imported letterhead is rendered full-bleed (see `letterhead_fullbleed` in
invoice_templates.resolve_theme), so a band cropped from y=0 to y=H mm and
printed into a reservation of exactly H mm reproduces the original at 1:1 --
same margins, same position, same size. Trimming the whitespace above the logo
would shift the masthead up by however much we trimmed.
"""

from __future__ import annotations

import hashlib
import io
import os
import re

import numpy as np
from PIL import Image

# Pillow refuses absurdly large images by default; we are stricter still,
# because a rendered page only ever needs to be legible on screen.
MAX_PIXELS = 40_000_000

# --- Page ----------------------------------------------------------------
# A4 in millimetres. Used as the assumed page size for a bare image upload,
# where there is no page box to read. A PDF carries its own size and that is
# preferred -- a client on US Letter gets Letter millimetres.
A4_W_MM = 210.0
A4_H_MM = 297.0

RENDER_DPI = 150          # 1240x1754 for A4: sharp enough to print, small enough to post
MM_PER_INCH = 25.4

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_IMPORT_BYTES = 12 * 1024 * 1024

# --- Detection -----------------------------------------------------------
# A pixel counts as ink when any channel differs from the page colour by more
# than this. Measured against the paper rather than against black so that a
# pale but coloured masthead band -- very common -- is found too.
INK_DELTA = 26

# A row counts as content when this fraction of it is ink. Low enough to catch
# a thin rule, high enough to ignore scanner speckle.
INK_ROW_FRACTION = 0.004

# The masthead ends at the first horizontal band of blank paper at least this
# tall. Smaller than this and we would cut between the two lines of an address.
GAP_MM = 5.0

# Bounds on what may be called a masthead. The floor stops a stray speck one
# millimetre down from ending the band; the ceiling stops an invoice with no
# whitespace anywhere from swallowing half the page.
MIN_BAND_MM = 15.0
MAX_BAND_FRACTION = 0.45

# Breathing room kept below the last ink, so a descender or a soft shadow is
# not shaved off.
PAD_MM = 1.5

# What resolve_theme accepts. Kept here so `suggest` can never propose a value
# that the settings screen would then clamp to something else.
MIN_LETTERHEAD_MM = 10.0
MAX_LETTERHEAD_MM = 200.0

# A rendered source page is parked in the uploads directory between the two
# steps of the import. The name binds it to the client that uploaded it, so
# the commit step cannot be pointed at another client's file.
#
# The client is identified by a hash rather than by its id: ids are not
# guaranteed to be integers (or to be safe in a filename), and hashing sidesteps
# both without putting the id itself in a URL the browser can see.
SOURCE_PREFIX = "import"
SOURCE_RE = re.compile(r"^import-([0-9a-f]{16})-[0-9a-f]{32}\.png$")


class LetterheadImportError(ValueError):
    """A problem with the uploaded file that the client can act on."""


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def render_first_page(blob, filename):
    """Render page one of an uploaded invoice.

    Returns `(image, page_w_mm, page_h_mm)`. A PDF is rasterised at RENDER_DPI
    and reports its own page box; an image is assumed to be a full page whose
    width is A4, which is the only sane reading of "here is my invoice" when
    the file carries no page geometry.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise LetterheadImportError("Upload a PDF, PNG, JPG or WebP file")
    if not blob:
        raise LetterheadImportError("That file is empty")
    if len(blob) > MAX_IMPORT_BYTES:
        raise LetterheadImportError("File must be 12MB or smaller")

    if ext == ".pdf":
        return _render_pdf(blob)
    return _load_image(blob)


def _render_pdf(blob):
    try:
        import pypdfium2
    except ImportError:                                  # pragma: no cover
        raise LetterheadImportError(
            "PDF import is unavailable on this server; upload a PNG or JPG instead")

    try:
        pdf = pypdfium2.PdfDocument(io.BytesIO(blob))
        page_count = len(pdf)
    except LetterheadImportError:
        raise
    except Exception:
        raise LetterheadImportError(
            "That PDF could not be opened. It may be password protected or damaged")

    try:
        if page_count < 1:
            raise LetterheadImportError("That PDF has no pages")
        page = pdf[0]
        width_pt, height_pt = page.get_size()
        if width_pt <= 0 or height_pt <= 0:
            raise LetterheadImportError("That PDF has an unreadable page size")
        image = page.render(scale=RENDER_DPI / 72).to_pil().convert("RGB")
        return (image,
                width_pt / 72 * MM_PER_INCH,
                height_pt / 72 * MM_PER_INCH)
    finally:
        try:
            pdf.close()
        except Exception:
            pass


def _load_image(blob):
    try:
        image = Image.open(io.BytesIO(blob))
        image.load()
    except Exception:
        raise LetterheadImportError("That image could not be read")

    if image.width < 2 or image.height < 2:
        raise LetterheadImportError("That image is too small to read")
    if image.width * image.height > MAX_PIXELS:
        raise LetterheadImportError("That image is too large; scale it down first")

    image = image.convert("RGB")
    # A bare image has no page box. Assume the width is A4 and derive the
    # height from the image's own aspect ratio, so a Letter-shaped or
    # part-page scan still maps rows to millimetres proportionally rather
    # than being stretched onto a shape it does not have.
    page_w_mm = A4_W_MM
    page_h_mm = A4_W_MM * image.height / image.width
    return image, page_w_mm, page_h_mm


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
def _ink_rows(image):
    """Fraction of each pixel row that is ink rather than paper."""
    pixels = np.asarray(image, dtype=np.int16)
    # The page colour, taken as a high percentile per channel: paper is the
    # overwhelming majority of any invoice, so this lands on the paper even
    # when the sheet is cream, greyed by a scanner, or tinted.
    paper = np.percentile(pixels.reshape(-1, 3), 92, axis=0)
    deviation = np.abs(pixels - paper).max(axis=2)
    return (deviation > INK_DELTA).mean(axis=1)


def _smooth(values, window):
    """Moving average, so scanner speckle does not read as a content row."""
    window = max(1, int(window))
    if window <= 1 or values.size < window:
        return values
    kernel = np.ones(window) / window
    return np.convolve(values, kernel, mode="same")


def detect_masthead_mm(image, page_h_mm):
    """Height in mm of the masthead band at the top of `image`.

    The band runs from the top of the page to the first stretch of blank paper
    at least GAP_MM tall that follows some ink. That is what a masthead *is* on
    a printed invoice: the block above the first real gap.

    Returns None when the page has no ink at all in its top portion, which is
    the honest answer for a blank first page -- the caller falls back to the
    default reservation rather than inventing a number.
    """
    height = image.height
    if height < 2 or page_h_mm <= 0:
        return None

    px_per_mm = height / page_h_mm
    rows = _smooth(_ink_rows(image), round(px_per_mm))
    content = rows > INK_ROW_FRACTION

    limit = max(2, min(height, int(height * MAX_BAND_FRACTION)))
    floor_px = int(MIN_BAND_MM * px_per_mm)
    gap_px = max(2, int(GAP_MM * px_per_mm))

    window = content[:limit]
    if not window.any():
        return None

    first_ink = int(np.argmax(window))

    # Walk forward looking for the first blank run of gap_px rows that starts
    # after both the first ink and the minimum band height.
    run_start = None
    for y in range(first_ink, limit):
        if content[y]:
            run_start = None
            continue
        if run_start is None:
            run_start = y
        if y - run_start + 1 >= gap_px and run_start >= floor_px:
            return _bounded_mm((run_start + PAD_MM * px_per_mm) / px_per_mm,
                               page_h_mm)

    # No qualifying gap in the top MAX_BAND_FRACTION of the page. Fall back to
    # the last ink we saw inside that window: an invoice whose header runs
    # straight into its table still has a defensible cut there.
    last_ink = limit - int(np.argmax(window[::-1]))
    return _bounded_mm((last_ink + PAD_MM * px_per_mm) / px_per_mm, page_h_mm)


def _bounded_mm(value_mm, page_h_mm):
    low, high = bounds_mm(page_h_mm)
    return float(max(low, min(high, round(value_mm))))


def bounds_mm(page_h_mm):
    """The range the client may drag the cut line to, for this page.

    Whole millimetres, because `clients.tpl_letterhead_mm` is an integer
    column. Keeping the bound integral is what lets the crop and the
    reservation be the same number by construction -- a crop of 133.7mm
    reserved as 134mm would print the masthead very slightly stretched.
    """
    high = min(MAX_LETTERHEAD_MM, max(page_h_mm, 0) * MAX_BAND_FRACTION)
    return MIN_LETTERHEAD_MM, float(int(max(MIN_LETTERHEAD_MM, high)))


# --------------------------------------------------------------------------
# Cropping
# --------------------------------------------------------------------------
def crop_masthead(image, page_h_mm, mm):
    """The top `mm` millimetres of the page, full width.

    Full width and starting at y=0 because the result is printed full-bleed
    into a reservation of exactly `mm` -- see the module docstring. Returns the
    cropped image and the millimetres actually used, which is the value the
    caller must store: a request outside the page's range is clamped, and a
    reservation that disagreed with the crop would print the band stretched.
    """
    low, high = bounds_mm(page_h_mm)
    try:
        mm = round(float(mm))
    except (TypeError, ValueError):
        mm = low
    mm = float(max(low, min(high, mm)))
    px = int(round(image.height * mm / page_h_mm)) if page_h_mm else image.height
    px = max(1, min(image.height, px))
    return image.crop((0, 0, image.width, px)), mm


def client_token(client_id):
    """A stable, filename-safe identifier for a client.

    Whatever `clients.id` turns out to be -- integer, UUID, string -- this is
    sixteen hex characters, so neither the filename nor the pattern that
    validates it has to care.
    """
    return hashlib.sha256(str(client_id).encode("utf-8")).hexdigest()[:16]


def source_prefix(client_id):
    """The filename prefix every source page of this client shares."""
    return f"{SOURCE_PREFIX}-{client_token(client_id)}-"


def source_name(client_id, token):
    return f"{source_prefix(client_id)}{token}.png"


def source_is_mine(name, client_id):
    """True when `name` is a source page this client uploaded.

    The commit step receives this name from the browser, so it is untrusted.
    Matching the whole basename against a fixed pattern carrying this client's
    token rules out both path traversal and reaching another client's upload.
    """
    match = SOURCE_RE.match(os.path.basename(str(name or "")))
    return bool(match) and match.group(1) == client_token(client_id)
