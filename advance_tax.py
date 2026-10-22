"""Advance Tax u/s 236(h) -- a printed-only line on one client's invoice.

Salon Marketings collect advance income tax under section 236(h) of the
Income Tax Ordinance from their registered buyers. It is 0.5% of the value
*including* sales tax, it is shown on the invoice the buyer receives, and the
amount in words at the foot of that invoice is the figure after it.

Three things make it unlike every other figure on the sheet:

  * **It is never filed.** FBR's Digital Invoicing schema is a *sales tax*
    document and has no element for an income-tax collection, so the rate
    travels with the invoice as a PDF-only field (`advanceTaxPercent` is
    listed in `PDF_ONLY_INVOICE_FIELDS` in app.py) and submit_fbr strips it
    before the payload goes out. Nothing here changes `totalExcl`,
    `totalTax`, `totalInclusive` or any line item -- those are what was filed
    and must stay that way, or the printed sheet and the FBR record disagree.
  * **It applies to registered buyers only**, which is what the client asked
    for. An unregistered buyer's invoice is unchanged.
  * **The rate is the user's to set.** 0.5% is the default and what the field
    is pre-filled with; the form lets it be changed per invoice, and 0
    suppresses the line entirely.

Everything derived here is recomputed at render time from the stored rate, so
re-printing an old invoice reproduces the sheet exactly.

Money is Decimal with ROUND_HALF_UP throughout -- the house rule for this
repo, and the same rounding `percentOf()` in create-invoice.html uses, so the
figure the user saw on the review step is the figure that prints.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# The clients whose invoice carries the line. invoice_form_routes.py reads
# this set rather than keeping its own; the one copy that cannot import it is
# ADVANCE_TAX_CLIENTS in templates/create-invoice.html, which must list the
# same usernames or the form offers a field the sheet does not print.
ADVANCE_TAX_USERNAMES = {
    "3520223163127",  # Salon Marketings
}

# As the client's own sheet labels it.
LABEL = "Advance Tax u/s 236(h)"

# Section 236(h) is 0.5% for a buyer on the Active Taxpayers List. It is a
# default, not a rule: the form pre-fills it and the user can type another.
DEFAULT_PERCENT = Decimal("0.5")

# A rate outside this range is a typo, not a tax.
MAX_PERCENT = Decimal("100")

# Keys this module adds to the payload. They are derived, so they are dropped
# from the copy that is cached and filed -- only `advanceTaxPercent`, the
# figure the user actually chose, is stored with the invoice.
DERIVED_KEYS = (
    "advanceTaxLabel",
    "advanceTaxRateLabel",
    "advanceTaxAmount",
    "totalWithAdvanceTax",
    "showAdvanceTax",
)

# The buyer registration spellings that mean "registered". Same set as the
# `status` row resolver in compliance/fields.py.
_REGISTERED = {"registered", "r", "yes", "1", "true"}


def prints_advance_tax(username):
    """True for the clients whose replica has the line."""
    return str(username or "").strip() in ADVANCE_TAX_USERNAMES


def normalize_percent(value, default=None):
    """The rate as a Decimal, or `default` for anything unusable.

    Accepts what the form and the stored invoice can hold: a number, "0.5",
    "0.5%", or a blank. Kept to 2 decimal places, which is as fine as
    `percentOf()` in create-invoice.html computes, so server and browser
    cannot disagree.
    """
    if value is None:
        return default
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text:
        return default
    try:
        percent = Decimal(text)
    except (InvalidOperation, ValueError):
        return default
    if percent < 0 or percent > MAX_PERCENT:
        return default
    return percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def rate_label(percent):
    """"0.5%" -- the rate as it prints, without trailing zeros."""
    text = format(Decimal(percent).normalize(), "f")
    return "{}%".format(text)


def buyer_is_registered(data):
    """The client collects 236(h) from registered buyers only."""
    raw = (
        (data or {}).get("buyerRegistrationType")
        or ((data or {}).get("buyerData") or {}).get("buyerRegistrationType")
        or ((data or {}).get("buyerData") or {}).get("registration_type")
        or ""
    )
    return str(raw).strip().lower() in _REGISTERED


def amount_in_words(total):
    """PKR amount in words, in the phrasing app.py already uses.

    Mirrors the three `num2words` blocks in app.py; this one runs after them,
    on the larger total, whenever the advance tax line applies.
    """
    from num2words import num2words

    words = num2words(round(float(total), 2), to="currency", lang="en", currency="USD")
    return words.replace("dollars", "rupees").replace("cents", "paisa") + " only"


def apply(data, username):
    """Add the advance tax line to an invoice payload, if it has one.

    Called by every route that renders an invoice PDF, right after the totals
    and the amount in words are computed. For a client without the line, or a
    buyer who is not registered, or a rate of zero, it is a no-op and the
    payload is returned untouched.

    Never raises: a failure here must not stop an invoice being printed.
    """
    if not isinstance(data, dict):
        return data
    try:
        if not prints_advance_tax(username) or not buyer_is_registered(data):
            return data

        percent = normalize_percent(data.get("advanceTaxPercent"), DEFAULT_PERCENT)
        if percent is None or percent <= 0:
            return data

        try:
            inclusive = Decimal(str(data.get("totalInclusive") or 0))
        except (InvalidOperation, ValueError):
            return data
        if inclusive <= 0:
            return data

        advance = (inclusive * percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        grand_total = (inclusive + advance).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        data["advanceTaxPercent"] = float(percent)
        data["advanceTaxLabel"] = LABEL
        data["advanceTaxRateLabel"] = rate_label(percent)
        data["advanceTaxAmount"] = float(advance)
        data["totalWithAdvanceTax"] = float(grand_total)
        data["showAdvanceTax"] = True

        # The figure the buyer pays is the one spelled out at the foot of the
        # sheet. totalInclusive itself is left alone: it is what was filed.
        data["amountInWords"] = amount_in_words(grand_total)
    except Exception as exc:                      # never break invoicing
        print("[advance_tax] skipped: {}".format(exc))
    return data


def strip_derived(payload):
    """Remove the derived keys from a payload about to be cached or filed.

    `advanceTaxPercent` deliberately survives: it is the user's input, it is
    stored with the invoice so the PDF can be re-rendered, and app.py drops it
    from the FBR request through PDF_ONLY_INVOICE_FIELDS.
    """
    if isinstance(payload, dict):
        for key in DERIVED_KEYS:
            payload.pop(key, None)
    return payload
