"""Per-client printing features that are decided by *which client* it is.

A handful of clients' replicas print something nobody else's does -- an extra
pair of custom fields, a buyer's phone number. The existing switches of that
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

# Two custom fields is the limit everywhere it is not raised here. Four is
# what Global Surface's four PO/DC fields need; it is a per-client number and
# not a new default, because the field pairs are laid out two to a line and a
# client who does not use them should not see four empty rows offered.
DEFAULT_CUSTOM_FIELDS_MAX = 2
FOUR_CUSTOM_FIELDS = frozenset(GLOBAL_SURFACE)

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
    happens to have is enough.
    """
    return 4 if _keys(*ids) & FOUR_CUSTOM_FIELDS else DEFAULT_CUSTOM_FIELDS_MAX


def prints_buyer_phone(*ids) -> bool:
    """Whether this client's invoice has a row for the buyer's phone number."""
    return bool(_keys(*ids) & BUYER_PHONE)
