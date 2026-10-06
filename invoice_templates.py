"""Invoice template registry, theme resolution and layout metadata.

Replaces the per-client `if username == "..."` chain that used to choose an
invoice template in app.py. A template is now a *choice a client makes*, stored
on `clients.tpl_template`, and the thirteen layouts are arrangements of one
shared set of parts (see templates/invoices/).

Design rules
------------
* **The FBR block is not optional.** Every layout includes
  `parts/_fbr_block.html`; a layout only decides *where* it sits, never
  whether it appears. It renders once the invoice has an FBR invoice number,
  i.e. after submission -- before that there is nothing to encode in the QR.
* **Layouts never hardcode colour, font or spacing.** Those come from the
  resolved theme, so one client's "Classic" can look different from another's
  without a second template file.
* **Unknown ids fall back** to the default layout rather than raising, so a
  row written by a newer build cannot 500 an older one.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Layouts
# --------------------------------------------------------------------------
# `fbr` is where the compliance block sits in that layout, and it is the only
# thing a layout gets to say about it:
#   "footer"  full-width band above the signature row
#   "aside"   stacked in the layout's side rail / meta column
#   "header"  top-right of the header, beside the invoice title
LAYOUTS = [
    {"id": "classic",     "name": "Classic",     "desc": "Logo left, large invoice title right",         "fbr": "footer"},
    {"id": "modern",      "name": "Modern",      "desc": "Stacked header over a full-width accent rule", "fbr": "footer"},
    {"id": "sidebar",     "name": "Sidebar",     "desc": "Full-height accent rail carries the details",  "fbr": "aside"},
    {"id": "bold_header", "name": "Bold Header", "desc": "Full-bleed colour band across the top",        "fbr": "footer"},
    {"id": "minimal",     "name": "Minimal",     "desc": "No rules or fills - whitespace does the work", "fbr": "footer"},
    {"id": "letterhead",  "name": "Letterhead",  "desc": "Centred masthead, formal and symmetrical",     "fbr": "footer"},
    {"id": "compact",     "name": "Compact",     "desc": "Tight sheet that fits more rows per page",     "fbr": "footer"},
    {"id": "two_tone",    "name": "Two-Tone",    "desc": "Soft tinted bands behind header and totals",   "fbr": "footer"},
    {"id": "bordered",    "name": "Bordered",    "desc": "Ruled frame throughout, official-form feel",   "fbr": "framed"},
    {"id": "left_rail",   "name": "Left Rail",   "desc": "Thin accent stripe, editorial spacing",        "fbr": "footer"},
    {"id": "statement",   "name": "Statement",   "desc": "Summary strip of key figures up front",        "fbr": "footer"},
    {"id": "corner",      "name": "Corner",      "desc": "Invoice total stamped in the top corner",      "fbr": "header"},
    {"id": "column",      "name": "Column",      "desc": "Metadata column beside the items",             "fbr": "aside"},
]

LAYOUTS_BY_ID = {layout["id"]: layout for layout in LAYOUTS}
DEFAULT_LAYOUT = "classic"

# Legacy per-client templates. Still resolvable so an existing client whose row
# points at one keeps rendering exactly as before; they are not offered in the
# picker.
LEGACY_TEMPLATES = {
    "invoice_template.html", "invoice_template2.html", "invoice_template3.html",
    "invoice_template_nologo.html", "invoice_template_universal.html",
    "invoice_alraheem.html", "invoice_innovative.html", "invoice_templatezahid.html",
    "invoice_zeeshanst.html",
    # Hand-built per-client replicas. Listed here so a client is assigned one
    # by setting clients.tpl_template -- a row update, not a code change and
    # not another branch in the username chain in app.py.
    "invoice_apple_international.html",
    "invoice_ak_international.html",
    "invoice_paper_land.html",
    "invoice_hannan_traders.html",
    "invoice_paper_experts.html",
    "invoice_fk_printers.html",
}


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------
ACCENT_PRESETS = [
    ("Navy", "#1e3a8a"), ("Indigo", "#4338ca"), ("Teal", "#0f766e"),
    ("Forest", "#15803d"), ("Plum", "#7e22ce"), ("Rust", "#c2410c"),
    ("Crimson", "#be123c"), ("Ink", "#111827"), ("Slate", "#334155"),
]

FONTS = {
    # Families that ship with Windows and common Linux print stacks, so
    # WeasyPrint never silently falls back to something the client did not pick.
    "sans":      ("Sans",      '"Segoe UI", "Helvetica Neue", Helvetica, Arial, "DejaVu Sans", sans-serif'),
    "serif":     ("Serif",     'Georgia, "Times New Roman", "Liberation Serif", "DejaVu Serif", serif'),
    "condensed": ("Condensed", '"Arial Narrow", "Liberation Sans Narrow", "DejaVu Sans Condensed", "Segoe UI", sans-serif'),
    "mono":      ("Mono",      'Consolas, "DejaVu Sans Mono", "Courier New", monospace'),
}

# pt, not rem: this is paper. Sizes are chosen so a 20-line invoice fits one A4
# page at "compact" and stays readable at "spacious".
DENSITIES = {
    "compact": {
        "body_pt": 7.5, "label_pt": 6.0, "name_pt": 9.5, "heading_pt": 15, "total_pt": 10,
        "section_mm": 4.0, "row_mm": 1.0, "gap_mm": 4,
        "margin_mm": 10, "margin_x_mm": 10, "margin_bottom_mm": 10,
    },
    "comfortable": {
        "body_pt": 8.5, "label_pt": 6.5, "name_pt": 11.0, "heading_pt": 18, "total_pt": 12,
        "section_mm": 5.5, "row_mm": 1.35, "gap_mm": 6,
        "margin_mm": 14, "margin_x_mm": 14, "margin_bottom_mm": 14,
    },
    "spacious": {
        "body_pt": 9.5, "label_pt": 7.0, "name_pt": 12.5, "heading_pt": 21, "total_pt": 14,
        "section_mm": 8.0, "row_mm": 2.2, "gap_mm": 8,
        "margin_mm": 18, "margin_x_mm": 18, "margin_bottom_mm": 18,
    },
}

# The market convention for pre-printed letterhead paper.
DEFAULT_LETTERHEAD_MM = 120

# How much of the seller block a layout prints. A letterhead already carries
# the company name and address, so repeating them directly underneath is the
# single most common complaint about putting an invoice under a letterhead.
# The tax numbers are not optional in the same way -- they belong on the face
# of a sales tax invoice and a letterhead almost never carries them -- so the
# middle setting is the useful one, and the default whenever a letterhead
# image is present.
SELLER_DISPLAY = {
    "full":     ("Name, address and tax numbers", True, True),
    "tax_only": ("Tax numbers only (letterhead has the rest)", False, True),
    "hidden":   ("Nothing (letterhead has everything)", False, False),
}
DEFAULT_SELLER_DISPLAY = "full"
LETTERHEAD_SELLER_DISPLAY = "tax_only"


def _clamp(value, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _hex_ok(value, fallback):
    text = str(value or "").strip()
    if len(text) == 7 and text[0] == "#":
        try:
            int(text[1:], 16)
            return text.lower()
        except ValueError:
            pass
    return fallback


def on_accent(hex_color):
    """Black or white text for a given accent, by relative luminance.

    A mid-tone accent with white text on it is the usual way these templates
    turn unreadable the moment a client picks their own brand colour.
    """
    raw = _hex_ok(hex_color, "#1e3a8a").lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    return "#111827" if luminance > 0.45 else "#ffffff"


def _tint(hex_color, amount):
    """Mix an accent toward white. amount=0.90 keeps 10% of the accent."""
    raw = _hex_ok(hex_color, "#1e3a8a").lstrip("#")
    rgb = [int(raw[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(round(c + (255 - c) * amount) for c in rgb)


def resolve_theme(settings=None):
    """Build the theme dict the templates read, from stored settings.

    Everything is validated here so no template has to defend itself against a
    bad colour or a negative margin coming out of the database.
    """
    stored = settings or {}

    accent = _hex_ok(stored.get("accent_color"), "#1e3a8a")
    font_id = stored.get("font") if stored.get("font") in FONTS else "sans"
    density = stored.get("density") if stored.get("density") in DENSITIES else "comfortable"

    theme = dict(DENSITIES[density])
    theme.update({
        "accent": accent,
        "on_accent": on_accent(accent),
        "accent_soft": _tint(accent, 0.90),
        "accent_tint": _tint(accent, 0.80),
        "line": "#d1d5db",
        "font_id": font_id,
        "font_stack": FONTS[font_id][1],
        "density": density,
        "logo_mm": _clamp(stored.get("logo_mm"), 6, 40, 16),
        "qr_mm": _clamp(stored.get("qr_mm"), 12, 40, 18),
    })

    # Letterhead. `letterhead_mm` reserves space on page 1 only; an uploaded
    # image is printed into it. A client with PRE-PRINTED paper uploads no
    # image and gets the blank space, which is the whole point of reserving it.
    if stored.get("letterhead_enabled"):
        theme["letterhead_mm"] = _clamp(
            stored.get("letterhead_mm"), 10, 200, DEFAULT_LETTERHEAD_MM
        )
        theme["letterhead_image"] = stored.get("letterhead_image_url") or None
        # Full-bleed is only meaningful for a band the importer cropped from
        # the client's own page: it spans the sheet edge to edge and starts at
        # the paper's top edge, reproducing their letterhead at 1:1. A manually
        # uploaded graphic stays centred inside the content box, which is where
        # every existing client's letterhead already prints.
        theme["letterhead_fullbleed"] = bool(stored.get("letterhead_fullbleed"))
    else:
        theme["letterhead_mm"] = 0
        theme["letterhead_image"] = None
        theme["letterhead_fullbleed"] = False

    # How much of the seller block the layouts print. Unset means "decide for
    # me", and the sensible decision depends on whether a letterhead image is
    # actually going to be printed: with one, it already carries the name and
    # address; without one, nothing else on the page does.
    chosen = stored.get("seller_display")
    if chosen not in SELLER_DISPLAY:
        chosen = (LETTERHEAD_SELLER_DISPLAY if theme["letterhead_image"]
                  else DEFAULT_SELLER_DISPLAY)
    _, identity, tax_ids = SELLER_DISPLAY[chosen]
    theme["seller_display"] = chosen
    theme["seller_identity"] = identity      # name + address
    theme["seller_tax_ids"] = tax_ids        # NTN/CNIC + STRN

    return theme


def resolve_layout(settings=None):
    """Return the layout dict for the client's chosen template."""
    stored = settings or {}
    return LAYOUTS_BY_ID.get(stored.get("template"), LAYOUTS_BY_ID[DEFAULT_LAYOUT])


def template_path(settings=None):
    """Jinja path for the chosen layout, or the custom-builder renderer."""
    stored = settings or {}
    if stored.get("template") == "custom" and stored.get("custom_spec"):
        return "invoices/custom.html"
    return "invoices/layouts/%s.html" % resolve_layout(stored)["id"]


def resolve_template_path(settings, legacy_default):
    """The Jinja path app.py should render for this client.

    This is the one place the old and new worlds meet:

      * a new layout id           -> invoices/layouts/<id>.html
      * "custom" with a spec      -> invoices/custom.html
      * a legacy invoice_*.html   -> itself, unchanged
      * NULL / anything unknown   -> `legacy_default`

    The last case is what protects existing clients: a client who has never
    opened the Templates screen has tpl_template = NULL and keeps rendering
    through whatever the per-username mapping already gave them.
    """
    chosen = (settings or {}).get("template")
    if chosen == "custom" and (settings or {}).get("custom_spec"):
        return "invoices/custom.html"
    if chosen in LAYOUTS_BY_ID:
        return "invoices/layouts/%s.html" % chosen
    if chosen in LEGACY_TEMPLATES:
        return chosen
    return legacy_default


# Column -> settings key. Only the template/presentation columns; the older
# tpl_show_* flags are already mapped by app.py and are passed through.
_ROW_KEYS = {
    "tpl_template": "template",
    "tpl_accent_color": "accent_color",
    "tpl_font": "font",
    "tpl_density": "density",
    "tpl_letterhead_enabled": "letterhead_enabled",
    "tpl_letterhead_mm": "letterhead_mm",
    "tpl_letterhead_url": "letterhead_image_url",
    "tpl_letterhead_fullbleed": "letterhead_fullbleed",
    "tpl_seller_display": "seller_display",
    "tpl_logo_mm": "logo_mm",
    "tpl_qr_mm": "qr_mm",
    "tpl_custom_spec": "custom_spec",
    "tpl_custom_name": "custom_name",
}


def settings_from_row(row):
    """Map a `clients` row onto the settings keys the templates read.

    Tolerates a row that predates the migration (missing keys) and a row that
    is a plain tuple-backed mapping, so a deploy where the migration has not
    run yet degrades to defaults rather than raising.
    """
    out = {}
    if not row:
        return out
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    for column, key in _ROW_KEYS.items():
        value = getter(column, None)
        if value is not None:
            out[key] = value
    return out


def gallery():
    """Layout metadata for the picker UI."""
    return [dict(layout) for layout in LAYOUTS]
