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


def prints_buyer_supplier_fields(*ids) -> bool:
    """Whether this client's buyers carry a supplier number and delivery address.

    Gates the two inputs on the Buyers page and the invoice form, and whether
    either value is allowed onto the invoice at all. Both are printed-only:
    `PDF_ONLY_INVOICE_FIELDS` in app.py keeps them out of the FBR call.
    """
    return bool(_keys(*ids) & BUYER_SUPPLIER_FIELDS)
