"""Per-client printing features that are decided by *which client* it is.

A handful of clients' replicas print something nobody else's does -- another
pair or three of custom fields, a buyer's phone number. The switches of that
kind (`REAMS_USERNAMES`, `SALES_PERSON_USERNAMES`, `advance_tax`) are keyed on
`users.username`, which is fine where a username is already in hand but costs
a round trip to the database everywhere else.

This module is keyed on the client's **id** instead, and accepts either of the
two the application has: `session["user_id"]` (`users.id`) and
`session["client_id"]` (`clients.id`). Both are UUIDs from different tables, a
collision between them is not a thing that happens, and every caller already
holds at least one of them -- so the create-invoice page can decide what to
offer without asking the database anything. A username is accepted too, so a
caller that only has that one is not stuck.

Nothing here changes what is filed. Both features below are printed-only: the
custom fields are label/value pairs on the face of the sheet, and the buyer's
phone is stripped from the FBR payload by `PDF_ONLY_INVOICE_FIELDS` in app.py.
"""

from __future__ import annotations

# Global Surface Technologies. Their sheet carries PO Number, PO Date, DC
# Number and DC Date above the total, and a buyer's phone row in the buyer
# box -- so they get four custom fields where everyone else gets two, and the
# phone field the Salon Marketings build added.
GLOBAL_SURFACE = (
    "ed156fdd-086e-480c-be18-a2c85d9e31b1",
)

# Material Craft. Their sheet carries P.O #, P.O Date, D Challan # and DC
# Date in the grid at the top right -- four custom fields. Their Delivery
# Address and Supplier # rows began here as a fifth field and were moved to
# the buyer, which is what they are properties of; see BUYER_SUPPLIER_FIELDS.
MATERIAL_CRAFT = (
    "71a52649-1b77-4748-9a51-fd33ce621000",
)

# Zahid Brothers. Their sheet is an Excel form with named boxes rather than
# a free column of label/value pairs: Reference and Term of Sales are rows on
# it, so they are proper fields on the invoice form rather than custom fields,
# and the items table has a Product Code & Description column they type their
# own codes into. See templates/invoice_zahid_brothers.html.
ZAHID_BROTHERS = (
    "74a2012a-8bf8-46e8-94fd-611e748e7c35",
)

# Two custom fields is the limit everywhere it is not raised here. A raised
# cap is a per-client number and not a new default, because the field pairs
# are laid out two to a line and a client who does not use them should not be
# offered rows of empty ones.
DEFAULT_CUSTOM_FIELDS_MAX = 2
CUSTOM_FIELD_CAPS = {}
CUSTOM_FIELD_CAPS.update({key: 4 for key in GLOBAL_SURFACE})
CUSTOM_FIELD_CAPS.update({key: 4 for key in MATERIAL_CRAFT})

# Clients whose buyers carry a supplier number and a delivery address, and
# whose replica prints them. Both are properties of the customer rather than
# of the sale -- the vendor number that buyer files this client under, and
# the plant the goods go to -- so they live on the buyer and fill themselves
# in when one is picked. One set because this client asked for the pair
# together; if another ever wants one and not the other, split it in two,
# the way the phone was split out of the sales-person set.
BUYER_SUPPLIER_FIELDS = frozenset(MATERIAL_CRAFT)

# Clients whose replica prints the buyer's phone number. Separate from the
# sales-person set in invoice_form_routes.py, which used to stand in for this
# one: the two started together and are not the same question.
BUYER_PHONE = frozenset(GLOBAL_SURFACE)

# Clients whose invoice has a Reference row and a Term of Sales row. Both are
# printed-only -- FBR's invoice schema has neither, and PDF_ONLY_INVOICE_FIELDS
# in app.py strips them before the payload goes out. They are proper fields on
# the form rather than custom fields because that is what the client asked
# for: their sheet has named boxes for them and nothing else, so being offered
# a pair of blank "name / value" rows to retype the same two labels into on
# every invoice is worse than having the two boxes.
INVOICE_REFERENCE_FIELDS = frozenset(ZAHID_BROTHERS)

# Clients who type their own product codes, and whose invoice prints the code
# in front of the description.
#
# This is the narrow half of what `SPECIAL_USERNAMES` in invoice_form_routes.py
# does. That set also blanks the sale type and both SRO fields on the products
# page, which this client needs -- their invoice has an Item Type column and
# it is the sale type -- so they are not in it. Codes only.
#
# EMPTY, deliberately. Zahid Brothers were the only members and asked for the
# box to go: the code their sheet's "Product Code & Description" column shows
# is part of the description they type ("1183104-1-1BALANCER UNIT new LS -"),
# not a second field, and offering a separate box printed the application's
# own P-0001 series in front of it. The machinery is left standing because
# the next client to want a manufacturer's part number needs exactly this and
# nothing else; add their id here and the box, the column prefix and the
# server-side gate all come back.
MANUAL_PRODUCT_CODES = frozenset()

# Clients whose items table has a Part Unit column that is a figure of its
# own rather than one derived from the line.
#
# Their old software carried it as a separate number and their sheet prints
# it beside the quantity, so it is theirs to type. Until now the template
# derived it (Gross Amount / Unit Rate) because nothing in the payload held
# one; that derivation stays as the fallback for a line with no figure, which
# is every line filed before this box existed.
#
# Printed-only: FBR's item schema has no such field and PDF_ONLY_ITEM_FIELDS
# in app.py drops it before the call.
PART_UNIT = frozenset(ZAHID_BROTHERS)

# Clients whose invoice form starts Fixed Notified Value / Retail Price at the
# line's value excluding sales tax instead of at zero.
#
# They file 3rd Schedule goods, where FBR charges tax on the notified or
# printed retail price and rejects a zero outright (error 0300), and they sell
# at that price -- so the value excluding tax is the right opening figure on
# every line they write. It is a DEFAULT and nothing more: the box stays
# editable, a figure typed over it is kept, and the server never substitutes
# one. A line that is not 3rd Schedule should have the box cleared, exactly as
# before.
FIXED_NOTIFIED_FROM_VALUE = frozenset(ZAHID_BROTHERS)

# Clients whose items table has an HS Code Description column, filled from
# the description FBR's /pdi/v1/itemdesccode returns beside the code. Printed
# only: it is FBR's own text for the code that is already being filed, so
# there is nothing to send back, and PDF_ONLY_ITEM_FIELDS in app.py drops it.
HS_CODE_DESCRIPTION = frozenset(ZAHID_BROTHERS)


def _keys(*values) -> set:
    """The identifiers a caller passed, as comparable strings."""
    return {str(value).strip() for value in values if value is not None and str(value).strip()}


def custom_fields_max(*ids) -> int:
    """How many custom field pairs this client may put on an invoice.

    Pass any mix of users.id, clients.id and username; whichever the caller
    happens to have is enough. A caller holding two identifiers for the same
    client gets the same answer from either, so the highest cap any of them
    names is the one that applies.
    """
    caps = [CUSTOM_FIELD_CAPS[key] for key in _keys(*ids) if key in CUSTOM_FIELD_CAPS]
    return max(caps) if caps else DEFAULT_CUSTOM_FIELDS_MAX


def prints_buyer_phone(*ids) -> bool:
    """Whether this client's invoice has a row for the buyer's phone number."""
    return bool(_keys(*ids) & BUYER_PHONE)


def prints_invoice_reference_fields(*ids) -> bool:
    """Whether this client's invoice form offers Reference and Term of Sales.

    Gates the two inputs on the invoice form and whether either value is
    allowed onto the invoice at all. Both are printed-only.
    """
    return bool(_keys(*ids) & INVOICE_REFERENCE_FIELDS)


def types_own_product_code(*ids) -> bool:
    """Whether this client types product codes rather than being given one.

    Everyone else's products are numbered P-0001 upwards by
    `master_data.reserve_codes`; these clients' codes are the manufacturer's
    and are printed on the invoice line, so the Code box on the products page
    is theirs to fill in.
    """
    return bool(_keys(*ids) & MANUAL_PRODUCT_CODES)


def prints_hs_code_description(*ids) -> bool:
    """Whether this client's invoice prints FBR's description of the HS code."""
    return bool(_keys(*ids) & HS_CODE_DESCRIPTION)


def types_part_unit(*ids) -> bool:
    """Whether this client types a Part Unit figure for each line.

    Gates the box on the invoice form and whether a figure is allowed onto
    the invoice at all. Printed-only; a line without one falls back to the
    derivation the template has always used.
    """
    return bool(_keys(*ids) & PART_UNIT)


def fixed_notified_defaults_to_value(*ids) -> bool:
    """Whether Fixed Notified Value opens at the line's value excluding tax.

    A starting figure on the form only -- see FIXED_NOTIFIED_FROM_VALUE. What
    is filed is whatever the box holds when the line is added, the same as it
    is for every other client.
    """
    return bool(_keys(*ids) & FIXED_NOTIFIED_FROM_VALUE)


def prints_buyer_supplier_fields(*ids) -> bool:
    """Whether this client's buyers carry a supplier number and delivery address.

    Gates the two inputs on the Buyers page and the invoice form, and whether
    either value is allowed onto the invoice at all. Both are printed-only:
    `PDF_ONLY_INVOICE_FIELDS` in app.py keeps them out of the FBR call.
    """
    return bool(_keys(*ids) & BUYER_SUPPLIER_FIELDS)
