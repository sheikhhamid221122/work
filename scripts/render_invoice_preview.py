"""
Offline invoice preview / regression tool.

Renders a JSON payload through the SAME template + compliance profile the Flask
app uses, without needing a database or FBR credentials. Use it to eyeball a
client's layout after changing a profile, and to diff against an approved PDF.

    python3 scripts/render_invoice_preview.py scripts/sample_invoice_1634.json out.pdf
"""

import base64
import datetime
import json
import os
import sys
from io import BytesIO

import qrcode
from jinja2 import Environment, FileSystemLoader
from num2words import num2words
from weasyprint import HTML

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from compliance import (  # noqa: E402
    apply_compliance,
    get_profile,
    merge_template_settings,
    resolve_template_name,
    run_validation,
)


def datetimeformat(value):
    """Mirror of the app's Jinja filter (default date presentation)."""
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
    except Exception:
        return value


def comma_format(value):
    try:
        return "{:,.2f}".format(float(value))
    except (ValueError, TypeError):
        return value


def data_uri(relative_path):
    if not relative_path:
        return ""
    path = os.path.join(ROOT, relative_path)
    if not os.path.exists(path):
        print("[preview] asset not found: {}".format(relative_path))
        return ""
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
    with open(path, "rb") as fh:
        return "data:image/{};base64,{}".format(ext, base64.b64encode(fh.read()).decode())


def compute_totals(data):
    excl = tax = 0.0
    for item in data.get("items", []):
        item_excl = float(str(item.get("valueSalesExcludingST", 0)).replace(",", ""))
        item_tax = float(str(item.get("salesTaxApplicable", 0)).replace(",", ""))
        qty = float(str(item.get("quantity", 1)).replace(",", "")) or 1
        excl += item_excl
        tax += item_tax
        item.setdefault("unitrate", item_excl / qty)
    data["totalExcl"] = round(excl, 2)
    data["totalTax"] = round(tax, 2)
    data["totalInclusive"] = round(excl + tax, 2)
    data["totalFurtherTax"] = 0
    data["showFurtherTax"] = False
    words = num2words(data["totalInclusive"], to="currency", lang="en", currency="USD")
    data["amountInWords"] = words.replace("dollars", "rupees").replace("cents", "paisa") + " only"
    return data


def main(payload_path, out_path):
    with open(payload_path, encoding="utf-8") as fh:
        doc = json.load(fh)

    username = doc.get("username")
    data = compute_totals(doc["data"])
    settings = dict(doc.get("settings") or {})

    profile = get_profile(username)
    if profile:
        settings = merge_template_settings(settings, profile)
        data = apply_compliance(data, settings, profile,
                                custom_fields_max=doc.get("custom_fields_max"))
        findings = run_validation(data, profile)
        print("[preview] profile '{}' applied, {} finding(s)".format(
            profile.get("name"), len(findings)))
    else:
        print("[preview] no compliance profile for {!r} — default rendering".format(username))

    qr_base64 = ""
    if data.get("fbrInvoiceNumber"):
        buffer = BytesIO()
        qrcode.make(data["fbrInvoiceNumber"]).save(buffer)
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()

    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")))
    env.filters["datetimeformat"] = datetimeformat
    env.filters["comma_format"] = comma_format
    env.globals["url_for"] = lambda *args, **kwargs: ""

    template_name = resolve_template_name(username, "invoice_template_universal.html")
    html = env.get_template(template_name).render(
        data=data,
        qr_base64=qr_base64,
        client_logo_url=data_uri(doc.get("logo_path")),
        fbr_logo_url=data_uri(doc.get("fbr_logo_path")),
        username=username,
        settings=settings,
        invoice_custom_fields_max=doc.get("custom_fields_max", 2),
        signature_note_above_authorised=data.get("signatureNote"),
    )
    HTML(string=html, base_url=ROOT).write_pdf(out_path)
    print("[preview] wrote {} using {}".format(out_path, template_name))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
