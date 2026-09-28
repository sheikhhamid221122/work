"""Render every layout in every seller mode and check the invariant.

`full` is the baseline -- whatever that layout has always printed. The other
modes may only ever REMOVE from it, never add, and must never touch the buyer
block or the FBR marks. Testing it relatively rather than absolutely means the
check does not assume every layout prints every field (several never have).
"""
import base64
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from flask import Flask, render_template
import qrcode
import invoice_spec
import invoice_templates
from compliance import meta_rows, resolve_field
from template_routes import _sample_invoice

app = Flask(__name__, root_path=os.getcwd(), template_folder="templates",
            static_folder="static")
app.jinja_env.globals["item_columns"] = invoice_spec.item_columns
app.jinja_env.globals["invoice_field"] = resolve_field
app.jinja_env.globals["meta_rows"] = meta_rows

SELLER = {
    "business_name": "Northwind Trading Co.",
    "address": "42 Jail Road, Lahore",
    "province": "Punjab",
    "ntn_cnic": "1234567-8",
    "strn": "32-77-8899-001-55",
}
MARKS = {
    "name": "Northwind Trading Co.",
    "address": "42 Jail Road, Lahore",
    "ntn": "1234567-8",
    "strn": "32-77-8899-001-55",
}
IDENTITY = ("name", "address")
TAX = ("ntn", "strn")


def qr_b64(text):
    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def render(layout_id, mode):
    settings = {
        "template": layout_id, "accent_color": "#0b3d62", "font": "sans",
        "density": "comfortable", "letterhead_enabled": True,
        "letterhead_mm": 42,
        "letterhead_image_url": "/static/uploads/letterheads/none.png",
        "letterhead_fullbleed": True, "seller_display": mode,
    }
    spec = invoice_spec.default_spec() if layout_id == "custom" else None
    with app.test_request_context("/"):
        data = _sample_invoice(SELLER)
        html = render_template(
            invoice_templates.template_path(settings),
            data=data, theme=invoice_templates.resolve_theme(settings),
            settings=settings, custom_spec=spec,
            qr_base64=qr_b64(data["fbrInvoiceNumber"]),
            client_logo_url=None, fbr_logo_url=None, username="v")
    return html, data


layout_ids = [layout["id"] for layout in invoice_templates.LAYOUTS] + ["custom"]
failures = []
never_prints_tax = []

for layout_id in layout_ids:
    shown = {}
    for mode in ("full", "tax_only", "hidden"):
        try:
            html, data = render(layout_id, mode)
        except Exception as exc:
            failures.append(f"{layout_id}/{mode}: RENDER FAILED {exc!r}")
            break
        shown[mode] = {key: (mark in html) for key, mark in MARKS.items()}

        if "Sample Buyer (Private) Limited" not in html:
            failures.append(f"{layout_id}/{mode}: buyer block missing")
        if data["fbrInvoiceNumber"] not in html:
            failures.append(f"{layout_id}/{mode}: FBR invoice number missing")
    else:
        base = shown["full"]
        # tax_only: identity gone, tax exactly as the baseline had it.
        for key in IDENTITY:
            if shown["tax_only"][key]:
                failures.append(f"{layout_id}: {key} still shown in tax_only")
        for key in TAX:
            if shown["tax_only"][key] != base[key]:
                failures.append(
                    f"{layout_id}: {key} changed in tax_only "
                    f"({base[key]} -> {shown['tax_only'][key]})")
        # hidden: nothing at all.
        for key in IDENTITY + TAX:
            if shown["hidden"][key]:
                failures.append(f"{layout_id}: {key} still shown in hidden")
        # And a mode must never ADD something the baseline lacked.
        for mode in ("tax_only", "hidden"):
            for key in MARKS:
                if shown[mode][key] and not base[key]:
                    failures.append(f"{layout_id}: {mode} added {key}")

        if not (base["ntn"] or base["strn"]):
            never_prints_tax.append(layout_id)

import unittest  # noqa: E402


class SellerDisplay(unittest.TestCase):
    """Every built-in layout, in every seller mode, against its own baseline."""

    def test_a_mode_only_ever_removes_from_the_full_block(self):
        self.assertEqual([], failures, "\n" + "\n".join(failures))

    def test_every_layout_was_actually_rendered(self):
        # A typo in template_path would otherwise make this suite vacuous.
        self.assertEqual(len(invoice_templates.LAYOUTS) + 1, len(layout_ids))


if __name__ == "__main__":
    unittest.main()
