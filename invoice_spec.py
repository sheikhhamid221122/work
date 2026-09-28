"""Validation for templates built in the invoice template designer.

A designer-made template is stored as a **placement spec** -- a closed
vocabulary of typed elements, each with a position and size on an A4 page --
and never as HTML.

Why not HTML
------------
Storing the designer's output as HTML would mean the PDF renderer executes
markup that a user composed. Two things go wrong immediately:

1. **Compliance.** A user could delete the FBR/QR element, drag it off the
   page, or shrink it to nothing, and we would happily print a sales tax
   invoice with no scannable QR code. `validate()` guarantees exactly one
   `fbr` element in every saved spec, fully on the page and no smaller than
   FBR_MIN_MM.
2. **Injection.** Arbitrary markup in a template that renders server-side is
   an injection surface. A closed vocabulary of typed elements has no such
   surface: unknown types are dropped, and every string is escaped at render
   time by Jinja's autoescaping.

Coordinates
-----------
Every element carries `x`, `y`, `w`, `h` in millimetres, measured from the
top-left of the printable area. Millimetres rather than pixels because the
renderer targets paper: the designer's canvas scales mm to screen pixels at
whatever zoom the user picked, so a template laid out at 50% zoom prints
identically to one laid out at 150%.

The items table is the one element whose height is a *minimum* rather than a
fixed box -- see ELASTIC below and the note in invoices/custom.html. A fixed
height would clip a forty-line invoice or strand it on a blank second page.
"""

from __future__ import annotations

import re

# The single source of truth for which invoice fields exist and how each one
# is resolved from a payload. The designer's "Invoice Field" element picks
# from this catalogue rather than keeping a list of its own, so a field added
# for the compliance layer is immediately placeable on a template.
try:
    from compliance.fields import FIELD_CATALOG
    FIELD_KEYS = tuple(FIELD_CATALOG)
except Exception:                       # compliance is optional at import time
    FIELD_CATALOG = {}
    FIELD_KEYS = ()

# --- The page ------------------------------------------------------------
# A4 at the printable area our stylesheet uses. The designer canvas and the
# renderer both derive from these, so the two can never disagree.
PAGE_W_MM = 210.0
PAGE_H_MM = 297.0
MARGIN_MM = 12.0
CANVAS_W_MM = PAGE_W_MM - 2 * MARGIN_MM      # 186
CANVAS_H_MM = PAGE_H_MM - 2 * MARGIN_MM      # 273

MAX_ELEMENTS = 60
MAX_TEXT = 2000
MIN_W_MM = 8.0
MIN_H_MM = 5.0
FBR_MIN_MM = 20.0        # a QR smaller than this does not scan reliably
GRID_MM = 2.0            # snap step the designer offers; not enforced here

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# --- Items table columns -------------------------------------------------
# Every field the FBR Digital Invoicing API carries at item level (DI API
# v1.12 s4.1.2, "Invoice Items Field Description"), plus the two of our own
# the app already collects (serial number and product code).
#
# `req` marks the fields the DI API requires in the *payload*. That is not the
# same as requiring them on the printed page -- FBR validates the JSON, not
# the paper -- so a required field here is one the designer defaults to ON and
# warns about switching off, not one it refuses to hide. The exception is
# `description`, which is locked: a line-item table with no description of the
# item is not an invoice.
#
# key -> (label, payload keys tried in order, kind, nominal width %, req)
ITEM_COLUMNS = {
    "serial":          ("#", (), "index", 4.5, False),
    "product_code":    ("Code", ("product_code",), "text", 10.0, False),
    "description":     ("Description", ("productDescription",), "desc", 0.0, True),
    "hs_code":         ("HS Code", ("hsCode", "hs_code"), "text", 10.0, True),
    "uom":             ("UoM", ("uoM", "uom"), "text", 11.0, True),
    "quantity":        ("Qty", ("quantity",), "num", 6.0, True),
    "rate":            ("Rate", ("unitrate", "rate"), "money", 9.0, True),
    "value_excl":      ("Value excl. ST", ("valueSalesExcludingST",), "money", 12.0, True),
    "sales_tax":       ("Sales Tax", ("salesTaxApplicable",), "money", 11.0, True),
    "fixed_value":     ("Fixed / Retail Price",
                        ("fixedNotifiedValueOrRetailPrice",), "money", 12.0, True),
    "st_withheld":     ("ST Withheld", ("salesTaxWithheldAtSource",), "money", 11.0, True),
    "extra_tax":       ("Extra Tax", ("extraTax",), "money", 10.0, False),
    "further_tax":     ("Further Tax", ("furtherTaxAmount", "furtherTax"), "money", 11.0, False),
    "fed_payable":     ("FED Payable", ("fedPayable",), "money", 11.0, False),
    "discount":        ("Discount", ("discount",), "money", 10.0, False),
    "sale_type":       ("Sale Type", ("saleType",), "text", 16.0, True),
    "sro_schedule":    ("SRO Schedule", ("sroScheduleNo",), "text", 12.0, False),
    "sro_item_serial": ("SRO Serial", ("sroItemSerialNo",), "text", 10.0, False),
    "total":           ("Total", (), "total", 12.0, False),
}

# The columns a printed sales tax invoice carries by convention, and what a
# new template starts with. Deliberately not "everything required by the API":
# eighteen columns on A4 portrait is unreadable, and the API payload already
# carries the rest.
DEFAULT_ITEM_COLUMNS = ("serial", "description", "hs_code", "uom", "quantity",
                        "rate", "value_excl", "sales_tax", "total")

# `description` is never hideable; it is re-inserted if a spec omits it.
LOCKED_ITEM_COLUMN = "description"

MIN_DESC_PCT = 14.0      # the description column never squeezes below this

# Past this many columns an A4 portrait sheet is technically still correct --
# item_columns() guarantees it fits -- but genuinely hard to read. The
# designer warns here rather than refusing, because a client printing on A3 or
# in landscape may legitimately want more.
LEGIBLE_ITEM_COLUMNS = 12


def item_columns(keys=None):
    """Resolve column keys into render-ready column dicts.

    Widths are the interesting part. Every column except the description has
    a nominal width, and the description takes whatever is left. Switch on
    enough optional columns and the nominal widths would exceed the page, so
    they are scaled down proportionally until the description keeps
    MIN_DESC_PCT. Without this the table silently overflows the sheet -- the
    same failure the hardcoded widths in this macro were written to avoid.

    Exposed to Jinja as the `item_columns` global; see add_template_routes.
    """
    keys = [k for k in (keys or DEFAULT_ITEM_COLUMNS) if k in ITEM_COLUMNS]
    if LOCKED_ITEM_COLUMN not in keys:
        # Put it back where it usually sits rather than at the end.
        at = 1 if keys and keys[0] == "serial" else 0
        keys.insert(at, LOCKED_ITEM_COLUMN)

    # Preserve the catalogue's order, not the order the keys arrived in: the
    # money columns must stay to the right of the quantities whatever order a
    # user ticked the boxes in.
    ordered = [k for k in ITEM_COLUMNS if k in keys]

    fixed = sum(ITEM_COLUMNS[k][3] for k in ordered if k != LOCKED_ITEM_COLUMN)
    budget = 100.0 - MIN_DESC_PCT
    scale = min(1.0, budget / fixed) if fixed > budget else 1.0

    out = []
    for key in ordered:
        label, sources, kind, width, req = ITEM_COLUMNS[key]
        out.append({
            "key": key, "label": label, "sources": list(sources), "kind": kind,
            "required": req,
            "width": None if key == LOCKED_ITEM_COLUMN else round(width * scale, 2),
            "align": "left" if kind in ("text", "desc", "index") else "num",
        })
    return out


# --- Element vocabulary --------------------------------------------------
# type -> {field: (kind, default, choices_or_range)}
#
# kind is one of: "text", "enum", "number", "bool", "url", "color",
# "columns" (an ordered subset of ITEM_COLUMNS).
# Every element additionally gets the universal style fields in STYLE.
#
# What a user may change is *presentation*: where an element sits, how big it
# is, and how it is styled. What a user may not change is which figures an
# invoice carries.
#
# So there is deliberately no "hide the NTN", "hide the sales tax column" or
# "hide the invoice number" option here, tempting as they look in a designer.
# Those fields are mandatory on an FBR sales tax invoice, parts.html keeps the
# item columns identical across every layout for exactly that reason, and the
# rows in `meta` come from the compliance layer rather than from the template.
# A toggle that switched one off would be a compliance hole dressed up as a
# preference.
ELEMENTS = {
    "header":  {"text": ("text", "INVOICE", None),
                "size": ("enum", "title", ("name", "heading", "title"))},
    "logo":    {},                                  # sized by w/h alone
    "seller":  {"heading": ("text", "", None)},
    "buyer":   {"heading": ("text", "Bill To", None)},
    "meta":    {},                                  # rows come from the data
    "items":   {"variant": ("enum", "rule", ("rule", "filled", "grid")),
                "columns": ("columns", DEFAULT_ITEM_COLUMNS, None)},
    "totals":  {"emphasis": ("enum", "rule", ("rule", "fill"))},
    "fbr":     {"orientation": ("enum", "row", ("row", "col"))},
    "notes":   {"heading": ("text", "Notes", None),
                "text": ("text", "", None)},
    "footer":  {"text": ("text", "", None)},
    "text":    {"text": ("text", "", None)},
    "divider": {"weight": ("enum", "thin", ("hair", "thin", "thick"))},
    "box":     {},                                  # a plain rectangle

    # One labelled invoice field, placed anywhere. This is what makes the
    # designer specific to FBR invoicing rather than generic: the key list is
    # the compliance catalogue, so P.O #, D.C #, time of issue, sale type,
    # delivery date, currency and the rest are all placeable, each resolved
    # from the real payload and labelled the way this client's profile
    # labels it.
    "field":   {"key": ("enum", FIELD_KEYS[0] if FIELD_KEYS else "", FIELD_KEYS),
                "label": ("text", "", None),
                "stacked": ("bool", False, None)},
}

# Universal style, applied to every element type.
STYLE = {
    "align": ("enum", "left", ("left", "center", "right")),
    "valign": ("enum", "top", ("top", "middle", "bottom")),
    "font_size": ("number", 9, (6, 48)),
    "bold": ("bool", False, None),
    "italic": ("bool", False, None),
    "color": ("color", "", None),
    "fill": ("enum", "none", ("none", "light", "accent")),
    "border": ("enum", "none", ("none", "thin", "thick")),
    "z": ("number", 0, (0, 99)),
}

# The items table grows past its designed height when an invoice has many
# lines; the renderer pushes everything below it down to make room.
ELASTIC = {"items"}

REQUIRED = "fbr"         # every spec must contain exactly one of these
SINGLETONS = (REQUIRED, "items")

# Only images the app itself serves may be referenced. A designer that
# accepted arbitrary URLs would fetch attacker-controlled hosts at
# PDF-render time.
ALLOWED_IMAGE_PREFIXES = ("/static/", "static/", "data:image/png;base64,",
                          "data:image/jpeg;base64,")


def _num(value, default, low, high):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return max(low, min(high, n))


def _text(value, default=""):
    if value is None:
        return default
    return str(value).replace("\x00", "")[:MAX_TEXT]


def _url(value):
    text = _text(value).strip()
    if not text:
        return ""
    return text if text.startswith(ALLOWED_IMAGE_PREFIXES) else ""


def _color(value):
    text = _text(value).strip()
    return text if HEX_RE.match(text) else ""


def _columns(value, default):
    """Validate an items-table column list.

    Unknown keys are dropped rather than rejected, so a spec saved by a newer
    build degrades instead of failing, and the locked description column is
    restored by item_columns() at render time either way.
    """
    if not isinstance(value, (list, tuple)):
        return list(default)
    seen = []
    for key in value[:len(ITEM_COLUMNS)]:
        if key in ITEM_COLUMNS and key not in seen:
            seen.append(key)
    if not seen:
        return list(default)
    if LOCKED_ITEM_COLUMN not in seen:
        seen.insert(1 if seen[0] == "serial" else 0, LOCKED_ITEM_COLUMN)
    return seen


def _field(kind, value, default, extra):
    if kind == "number":
        return _num(value, default, *extra)
    if kind == "enum":
        return value if value in extra else default
    if kind == "bool":
        return bool(value) if isinstance(value, bool) else default
    if kind == "url":
        return _url(value)
    if kind == "color":
        return _color(value)
    if kind == "columns":
        return _columns(value, default)
    return _text(value, default)


def _geometry(raw, etype):
    """Clamp an element's box so it always lands on the page.

    Width and height are clamped first, then the origin, so an element
    dragged half off the canvas comes back fully inside it rather than being
    silently resized to fit where it was dropped.
    """
    min_w = FBR_MIN_MM if etype == REQUIRED else MIN_W_MM
    min_h = FBR_MIN_MM if etype == REQUIRED else MIN_H_MM

    w = _num(raw.get("w"), min(CANVAS_W_MM, 60.0), min_w, CANVAS_W_MM)
    h = _num(raw.get("h"), min(CANVAS_H_MM, 20.0), min_h, CANVAS_H_MM)
    x = _num(raw.get("x"), 0.0, 0.0, CANVAS_W_MM - w)
    y = _num(raw.get("y"), 0.0, 0.0, CANVAS_H_MM - h)
    return {"x": round(x, 2), "y": round(y, 2),
            "w": round(w, 2), "h": round(h, 2)}


def _clean_element(raw, index, used_ids):
    """Return a validated element, or None if it must be dropped."""
    if not isinstance(raw, dict):
        return None
    etype = raw.get("type")
    if etype not in ELEMENTS:
        return None

    out = {"type": etype}

    eid = _text(raw.get("id")).strip()
    if not ID_RE.match(eid) or eid in used_ids:
        eid = "e%d" % index
        while eid in used_ids:
            eid += "x"
    used_ids.add(eid)
    out["id"] = eid

    # Groups are cosmetic: they let the designer move several elements at
    # once. An unknown group id simply means the element travels alone.
    group = _text(raw.get("group")).strip()
    out["group"] = group if ID_RE.match(group) else ""

    out.update(_geometry(raw, etype))

    for field, (kind, default, extra) in ELEMENTS[etype].items():
        out[field] = _field(kind, raw.get(field, default), default, extra)
    for field, (kind, default, extra) in STYLE.items():
        out[field] = _field(kind, raw.get(field, default), default, extra)

    return out


def _dedupe(elements, etype):
    """Keep only the first element of a singleton type."""
    seen = False
    kept = []
    for e in elements:
        if e["type"] == etype:
            if seen:
                continue
            seen = True
        kept.append(e)
    return kept


# Typical height, in mm, for a block converted from the old stacked spec.
# Only used by _from_blocks below.
_LEGACY_H = {"logo": 26, "title": 14, "header": 14, "party": 32, "meta": 32,
             "items": 90, "totals": 34, "fbr": 34, "signature": 18,
             "seller": 32, "buyer": 32,
             "text": 10, "image": 30, "spacer": 8, "divider": 4, "row": 32}

# Old block type -> new element type. Types with no counterpart are dropped.
# `party` is absent deliberately: it carried the side as a field, which is a
# type here, so _legacy_type resolves it from the block itself.
_LEGACY_TYPE = {"logo": "logo", "title": "header", "meta": "meta",
                "items": "items", "totals": "totals", "fbr": "fbr",
                "text": "text", "divider": "divider", "spacer": None,
                "signature": "text", "image": "logo"}


def _legacy_type(block):
    """New element type for an old block, or None if it has no counterpart."""
    btype = block.get("type")
    if btype == "party":
        return "seller" if block.get("side") == "seller" else "buyer"
    return _LEGACY_TYPE.get(btype)


def _from_blocks(blocks):
    """Convert a spec from the old stacked builder into placed elements.

    The stacked builder stored an ordered list with no coordinates, so this
    walks the list top to bottom and gives each block the full content width
    at its natural height -- reproducing what the old renderer printed. A
    `row` becomes its children side by side, which is how it rendered too.

    Without this, every client who had already built a template would open
    the designer to a blank page.
    """
    out = []
    y = 0.0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")

        if btype == "row":
            kids = [k for k in (block.get("children") or [])
                    if isinstance(k, dict)]
            kids = [k for k in kids if _legacy_type(k)]
            if not kids:
                continue
            gap = 6.0
            each = (CANVAS_W_MM - gap * (len(kids) - 1)) / len(kids)
            tall = max(_LEGACY_H.get(k.get("type"), 20) for k in kids)
            for i, kid in enumerate(kids):
                out.append(dict(kid,
                                type=_legacy_type(kid),
                                x=i * (each + gap), y=y, w=each, h=tall))
            y += tall + 4
            continue

        etype = _legacy_type(block)
        if not etype:
            y += _LEGACY_H.get(btype, 6)      # a spacer still takes its space
            continue
        h = _LEGACY_H.get(btype, 20)
        out.append(dict(block, type=etype, x=0.0, y=y, w=CANVAS_W_MM, h=h))
        y += h + 4

    return out


def validate(spec):
    """Return a safe, renderable spec.

    Never raises and never returns None: a corrupt or hostile spec degrades
    to something printable rather than breaking invoicing. The returned spec
    always contains exactly one FBR element -- on the page, and large enough
    to scan -- and exactly one items table.

    A spec in the old stacked `blocks` shape is converted rather than
    discarded, so upgrading does not wipe a client's template.
    """
    spec = spec if isinstance(spec, dict) else {}
    raw_elements = spec.get("elements")
    if not isinstance(raw_elements, list):
        raw_elements = []
    if not raw_elements and isinstance(spec.get("blocks"), list):
        raw_elements = _from_blocks(spec["blocks"][:MAX_ELEMENTS])

    used_ids = set()
    elements = []
    for i, raw in enumerate(raw_elements[:MAX_ELEMENTS]):
        cleaned = _clean_element(raw, i, used_ids)
        if cleaned:
            elements.append(cleaned)

    # --- The compliance guarantee ----------------------------------------
    # Exactly one FBR element, always. _geometry has already forced it
    # on-page and no smaller than FBR_MIN_MM, so the only repairs left are a
    # missing one and duplicates.
    fbr = [e for e in elements if e["type"] == REQUIRED]
    if not fbr:
        elements.append(_clean_element(
            {"type": REQUIRED, "x": CANVAS_W_MM - 32, "y": CANVAS_H_MM - 32,
             "w": 30, "h": 30}, len(elements), used_ids))
    elif len(fbr) > 1:
        elements = _dedupe(elements, REQUIRED)

    # Exactly one items table: two elastic tables cannot both reflow without
    # overlapping, and an invoice with none is not an invoice.
    items = [e for e in elements if e["type"] == "items"]
    if not items:
        elements.append(_clean_element(
            {"type": "items", "x": 0, "y": 90, "w": CANVAS_W_MM, "h": 60},
            len(elements), used_ids))
    elif len(items) > 1:
        elements = _dedupe(elements, "items")

    # Paint order: z first, then top-to-bottom, then left-to-right. The
    # renderer relies on this being stable to decide what flows after the
    # items table.
    elements.sort(key=lambda e: (e["z"], e["y"], e["x"]))
    return {"elements": elements}


def default_spec():
    """The starting point a new custom template opens with.

    Mirrors the layout the designer shows on a fresh canvas: masthead and
    logo up top, the two parties facing each other, the items table through
    the middle, totals and the FBR marks at the foot.
    """
    W = CANVAS_W_MM
    return validate({"elements": [
        {"id": "hdr", "type": "header", "text": "SALES TAX INVOICE",
         "x": 0, "y": 0, "w": W, "h": 14, "align": "center",
         "size": "title", "bold": True, "font_size": 20},
        {"id": "logo", "type": "logo", "x": 0, "y": 18, "w": 56, "h": 26,
         "border": "thin"},
        {"id": "seller", "type": "seller", "x": 106, "y": 18, "w": 80,
         "h": 26, "align": "left", "fill": "light"},
        {"id": "buyer", "type": "buyer", "x": 0, "y": 50, "w": 90, "h": 34,
         "heading": "Bill To", "fill": "light"},
        {"id": "meta", "type": "meta", "x": 96, "y": 50, "w": 90, "h": 34,
         "fill": "light"},
        {"id": "items", "type": "items", "x": 0, "y": 90, "w": W, "h": 90,
         "variant": "rule", "border": "thin"},
        {"id": "totals", "type": "totals", "x": 96, "y": 180, "w": 90,
         "h": 34, "emphasis": "rule"},
        {"id": "fbr", "type": "fbr", "x": 0, "y": 180, "w": 86, "h": 34,
         "orientation": "row"},
        {"id": "notes", "type": "notes", "x": 0, "y": 220, "w": W, "h": 20,
         "heading": "Notes", "font_size": 8},
        {"id": "footer", "type": "footer", "x": 0, "y": CANVAS_H_MM - 10,
         "w": W, "h": 8, "align": "center", "font_size": 8},
    ]})


def describe():
    """Element vocabulary for the designer UI, as plain JSON-able data."""
    def fields(table):
        return [{"name": name, "kind": kind,
                 "default": list(default) if kind == "columns" else default,
                 "choices": list(extra) if kind == "enum" else None,
                 "range": list(extra) if kind == "number" else None}
                for name, (kind, default, extra) in table.items()]

    return {
        "page": {"w": PAGE_W_MM, "h": PAGE_H_MM, "margin": MARGIN_MM,
                 "canvas_w": CANVAS_W_MM, "canvas_h": CANVAS_H_MM,
                 "grid": GRID_MM, "min_w": MIN_W_MM, "min_h": MIN_H_MM,
                 "fbr_min": FBR_MIN_MM, "max_elements": MAX_ELEMENTS,
                 "legible_columns": LEGIBLE_ITEM_COLUMNS,
                 "min_desc_pct": MIN_DESC_PCT},
        "style": fields(STYLE),
        "fields_catalog": [
            {"key": key, "label": label}
            for key, (label, _resolver) in FIELD_CATALOG.items()
        ],
        # `sources` and `kind` travel to the designer so its canvas resolves
        # each cell from the sample payload the same way parts.item_cell()
        # resolves it from a real one -- the preview cannot show a figure the
        # printed invoice would not.
        "item_columns": [
            {"key": key, "label": label, "payload": bool(req),
             "locked": key == LOCKED_ITEM_COLUMN,
             "default": key in DEFAULT_ITEM_COLUMNS,
             "sources": list(sources), "kind": kind, "width": width}
            for key, (label, sources, kind, width, req) in ITEM_COLUMNS.items()
        ],
        "elements": [
            {"type": etype,
             "required": etype == REQUIRED,
             "singleton": etype in SINGLETONS,
             "elastic": etype in ELASTIC,
             "fields": fields(table)}
            for etype, table in ELEMENTS.items()
        ],
    }
