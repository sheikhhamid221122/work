# Building a client's invoice template

TaxLinkPro gives each client a hand-built replica of the invoice they already
send, with the FBR compliance marks added. There is no self-serve template
builder — clients are onboarded one at a time, and a bespoke template produces
a better result than any settings screen.

This is the procedure. It was written after building the first one (Apple
International, username `3520224169621`), and every warning in it comes from
something that actually went wrong.

---

## What you need from the client

One PDF of an invoice they already send. That is all. Save it as:

```
templates/user-invoices/<username>.pdf
```

The PDF is not just a reference picture — their logo and letterhead artwork are
*inside* it, and step 1 pulls them out at print resolution.

---

## Step 1 — Extract their artwork

```bash
python scripts/extract_brand.py templates/user-invoices/<username>.pdf \
    --images --band 40 --username <username> --emit-brand
```

Without `--out` it also drops the raw PNGs into `extracted/<username>/` in the
repo root. Delete that folder afterwards, because the brand partial is the part
that matters.

This writes `templates/brand/<username>.html` containing their masthead and
strapline **inlined as base64 data URIs**, exposed as Jinja macros:

```jinja
{% import "brand/<username>.html" as brand %}
{{ brand.masthead("Client Name") }}
{{ brand.strapline("Their strapline") }}
```

### Why inline and not `client_logo_url`

Because `client_logo_url` does not work reliably. It is a database column, and
app.py renders invoice PDFs on three different routes that do **not** all turn
it into an absolute URL. WeasyPrint is handed the HTML with no `base_url`, so
`/static/...` silently resolves to nothing — the logo appears on one route and
a typed fallback prints on another. Artwork that travels inside the template
cannot fail that way.

`tests/test_client_templates.py` asserts the rendered output is byte-identical
with and without a logo URL. Keep it that way.

### How the two modes differ

| Mode | What it does | When |
|---|---|---|
| `--images` | Lifts each image object from the masthead at 600 DPI | The logo was pasted in as a picture — most Excel/Word invoices |
| `--band 40` | Crops the whole top 40 mm at 600 DPI | The masthead is vector art, live text or a bordered table with no single image to lift |

Auto-classification is by shape: wide-and-short near the top is the masthead,
tall-and-narrow is a strapline. Override with `--masthead FILE` /
`--strapline FILE` when it guesses wrong.

### ⚠ Check the extracted masthead for baked-in text

**Excel-built letterheads usually paste the entire header as one image** —
wordmark, rules, *and* the seller's Reg # / NTN / address as pixels.

Do not use that whole. Those are FBR fields that must match what is actually
submitted; frozen into a picture, the printed invoice can silently contradict
the payload if their registration ever changes.

Crop the artwork away from the text and leave the numbers as live text from
`data`. For Apple International that meant cropping at the ribbon and masking
the text that flanked the bow — see the commit for how.

### When the letterhead is several images, or full-bleed

Some letterheads are built from several stacked pictures running off the edge
of the sheet. Paper Land's, for example, is a bar with a wordmark plus two
ornament tiles. `--images` finds each piece separately, and `--band` also
catches the live title text lying on top of them. The fix is to render the page
with only its image objects:

```python
import pypdfium2 as p, pypdfium2.raw as c
page = p.PdfDocument(pdf)[0]
for o in list(page.get_objects()):
    if o.type != c.FPDF_PAGEOBJ_IMAGE:
        page.remove_obj(o)
page.gen_content()
im = page.render(scale=600/72).to_pil().convert("RGB")
band = im.crop((0, 0, im.width, int(78 / 25.4 * 600)))   # top 78 mm
```

Mask any baked-in text by filling its box with the colour behind it (sample a
clean pixel), then pass the result to the script with `--masthead FILE`. Print
the masked field back over the artwork as live text from `data`.

When the letterhead is vector art or typed text rather than pictures, keep
everything and remove only the text objects that carry FBR fields. Hannan
Traders' wordmark is vector shapes and their tagline is typed text, and only
the address/NTN/Reg line in their bar had to go. Filter by position: drop
`FPDF_PAGEOBJ_TEXT` objects whose top edge is below the tagline, then render and
crop as above. Nothing needs masking, because the text never reaches the
pixels.

When the sheet has **no images at all** and the masthead is only typed text,
still render it to an image. Remove every object that extends below the
masthead, render, trim to the ink, and pass the result with `--masthead`. Their
typeface then prints the same on a server that doesn't have it, and the brand
partial the tests require exists. Paper Experts is the example.

Small decorative shapes, such as a bar or a chevron beside a footer, are
simpler as a few lines of inline `<svg>` in the template than as an image. Take
the coordinates from the objects' positions in the PDF. WeasyPrint renders
inline SVG.

Place a full-bleed band with `position: fixed`. Use negative `top`/`left` equal
to the page margins, `width: 210mm` and `z-index: -1`. The artwork is opaque
white wherever it has no ornament, and without the negative z-index it paints
over the text laid on top of it.

### ⚠ Check the colours after inlining

The partial quantizes to 16 colours with median cut. On artwork that is mostly
pale shading, median cut spends the whole palette on greys, and a black
wordmark prints olive. Re-emit with `--octree`, and raise `--colours` (64 for
Paper Land) if faint shading then flattens into a flat tint.

### ⚠ Never lift the embedded stream directly

It is tempting to pull the image object's raw bytes. Don't. Transparency is
stored as a *separate* soft-mask object, so the raw stream comes back with the
masked area filled — black, on the first file this was tried on. pdfium's
per-object render applies the mask but collapses to the size the object
occupies on the page, throwing away resolution.

Cropping a high-DPI render of the whole page resolves masks, blending and
vector content, and lets you choose the resolution. That is what the script
does.

---

## Step 2 — Build the template

Create `templates/invoice_<client>.html`. Copy the client's layout faithfully;
the reference for the data contract is `invoice_template3.html`.

### The render contract

app.py passes:

| Variable | What it is |
|---|---|
| `data` | the invoice payload (see below) |
| `qr_base64` | the QR PNG, base64, no data: prefix |
| `client_logo_url` | **ignore it** — use the brand partial |
| `fbr_logo_url` | the Digital Invoicing logo, from the `fbr` table |
| `username`, `settings`, `theme` | rarely needed in a bespoke template |

Filters: `| comma_format` (thousands + 2dp) and `| datetimeformat`
(`YYYY-MM-DD` → `01 September 2026`).

Fields on `data`: `sellerBusinessName`, `sellerLegalName`, `sellerAddress`,
`sellerNTNCNIC`, `sellerSTRN`, `buyerBusinessName`, `buyerAddress`,
`buyerNTNCNIC`, `buyerSTRN`, `buyerRegistrationType`, `invoiceRefNo`,
`invoiceDate`, `fbrInvoiceNumber`, `totalExcl`, `totalTax`, `totalInclusive`,
`amountInWords`, and `items[]` with `productDescription`, `hsCode` /
`hs_code`, `uoM`, `quantity`, `unitrate`, `rate`, `valueSalesExcludingST`,
`salesTaxApplicable`, `totalValues`.

### Non-negotiable: what every replica must print

The three FBR marks:

1. **the FBR invoice number** — with the other identifiers, where a reader
   looks for it. Print it **once**; the QR already carries it, and repeating it
   under the stamp is clutter.
2. **the FBR Digital Invoicing logo**
3. **the QR code**

Put the logo and the QR **on one line, side by side**, as a single stamp:

```html
<table class="fbr">
  <tr>
    <td class="qr"><img src="data:image/png;base64,{{ qr_base64 }}" alt="FBR QR code"></td>
    {% if fbr_logo_url %}
      <td class="di"><img src="{{ fbr_logo_url }}" alt="FBR Digital Invoicing System"></td>
    {% endif %}
  </tr>
</table>
```

```css
.fbr td { border: none; vertical-align: middle; padding: 0 5mm 0 0; }
.fbr .qr img { width: 26mm; height: 26mm; display: block; }
.fbr .di img { width: 26mm; height: auto; display: block; }
```

Guard the whole stamp on `{% if data.fbrInvoiceNumber and qr_base64 %}` and
print "Not yet submitted to FBR" otherwise — never a QR that encodes nothing.

**Make the invoice number bold.** It is the only place it appears, and a
monospace face at small sizes prints noticeably lighter than the values around
it. 10px Courier bold reads correctly and still fits 27 characters on one line.

Also required on the face of the invoice: seller and buyer NTN and STRN, HS
code, quantity, rate, value excluding tax, sales tax, and the totals.

### Sensible departures from the client's original

Some are worth making, but say so in the template's header comment:

- **Group thousands.** Excel invoices often have no number format at all;
  `150779` is easy to misread.
- **Ignore spreadsheet artefacts.** A blue fill behind a header cell is usually
  a selected cell, not a design choice.
- **Take the unit of measure from the goods**, not hard-coded, and only show it
  in the header when every line shares one unit.
- **Pad the items table with blank rows** to the depth of their printed box, so
  a one-line invoice does not leave the totals row floating up the page.

### One table structure for every client

Every replica uses the same seven columns in the same order: Description, HS
Code, Quantity (unit underneath), Price, Value Ex. Sales tax, Sales Tax, V.Incl
Sales Tax. Copy the client's *styling* (rules, fills, fonts, widths), not their
column set. When their sheet puts the HS code inside the description or in a
different order, as Hannan Traders' did, use the standard columns anyway. That
was the client's own request.

Live text laid over a letterhead bar must **never** use `overflow: hidden`. A
longer address would silently cut off the NTN and Reg # printed after it.

---

## Step 3 — Register it

Two small edits, both easy to forget and both silent when missed:

1. **`invoice_templates.py`** — add the filename to `LEGACY_TEMPLATES`.
   Without this, `resolve_template_path` will not return it and the template is
   unreachable no matter what the database says.

2. **`tests/test_client_templates.py`** — add it to `CLIENT_TEMPLATES`. This is
   what puts the new client under the compliance guard. The guard is
   mutation-checked: deleting the QR and logo from a template fails it.

   Add it to `BrandArtwork.WITH_PARTIALS` in the same file too, mapped to
   `brand/<username>.html`. That is the list the inlined-artwork tests walk;
   leave it out and nothing checks that the logo survives a bad logo URL.

---

## Step 4 — Assign it

```sql
UPDATE clients c
   SET tpl_template = 'invoice_<client>.html'
  FROM users u
 WHERE u.id = c.user_id
   AND u.username = '<username>';
```

Join through `users`. The id quoted in conversation is usually `users.id`,
while `tpl_template` lives on `clients`. Matching on username is unambiguous.

Verify:

```sql
SELECT u.username, c.tpl_template
  FROM clients c JOIN users u ON u.id = c.user_id
 WHERE u.username = '<username>';
```

To roll back, set it to `NULL` — the client falls straight back to the
per-username chain in app.py, unchanged.

**Restart the Flask server** after step 3. `LEGACY_TEMPLATES` lives in Python
memory; a running process will not know the new filename exists and will
silently fall back to the default. Watch the terminal — app.py prints
`Selected template: ...` on every render.

---

## Then check it

```bash
python -m pytest tests/ -q
```

And look at a real PDF. Render one with the client's actual figures and compare
against the invoice they sent, side by side.

---

## WeasyPrint notes

Things that cost time on the first template:

- **`writing-mode: vertical-rl` is ignored.** Vertical text renders
  horizontally and runs off the sheet. Use `transform: rotate(90deg)` with
  `transform-origin: 0 0` — `left` then becomes the *right* edge of the
  printed strip, and the box extends left by its height and down by its width.
  Better still: if the artwork is already vertical, place it as-is and rotate
  nothing.
- **`position: fixed` repeats on every page** — correct for anything that would
  be pre-printed on their stationery.
- **No `base_url`** on the PDF routes. Every image must be a data URI or an
  absolute URL.
- **Table cells have no gutter.** A long buyer address will run straight into
  the label in the next column; add `padding-right` explicitly.
- **Don't fix both width and height** on extracted artwork — it will stretch.
  Set one and leave the other `auto`.
- **Arial Black has no italic face.** A heavy italic title falls back to
  upright. Use bold italic Arial a size up instead.
- **Check a long invoice.** A fixed letterhead needs a top page margin deep
  enough that page 2 starts below it.
- **Rows split across page breaks** unless told not to, which leaves a
  wrapped description on one page and its figures on the other. Every
  replica needs `.items tr { page-break-inside: avoid; }`.

---

## Worked example

`templates/invoice_apple_international.html` — Apple International, username
`3520224169621`. Replica of their Excel sheet: centred masthead, Reg #/NTN
line, underlined title, ORIGINAL / DUPLICATE mark, label-and-value buyer block,
seven-column items table with filler rows and an in-table totals row, a small
two-row summary box, "Signature & Stamp", and their stationery strapline down
the right margin. The FBR stamp sits in the empty band their original already
left below the summary box, so nothing of theirs had to move.

Their sheet is 210 × 305 mm, not A4; the template uses A4 because that is what
printers load. Worth asking each client.

`templates/invoice_ak_international.html` — AK International, username
`3520212803454`. The same Excel family as Apple International's, but the
masthead image (wordmark plus a Bulleh Shah distributor badge) carries no text,
so it was used whole with no masking. The seller's address, NTN and Reg # are a
centred footer on their sheet. They are rendered as `position: fixed` in the
bottom page margin, fed from `data`. The mobile number is not in the payload,
so it is fixed text. There is no strapline. The FBR stamp sits in the band they
left between the items table and the summary box. Their dates print as
`01-Sep-2026`, formatted in the template rather than through the shared filter.

`templates/invoice_paper_land.html` — Paper Land, username `4242880`. The
letterhead is full-bleed and built from three images: a grey ornament down the
top left, and a lime bar with the "Paper Land" wordmark. It was rendered from
the image objects alone into one 210 × 78 mm band. Their address and cell
number were pixels in the bar, so they were masked with the bar's colour
(199, 215, 56) and are printed back over it as live text. The partial was
emitted with `--octree --colours 64`. NTN # / Reg # are live text under the
bar. The buyer block and summary labels use a serif face (Cambria), as theirs
do. The FBR stamp sits between the items table and the summary box.

`templates/invoice_hannan_traders.html` — M/S Hannan Traders, username
`3520230962516`. The wordmark is vector art, the tagline is typed text, and
only the distributor badge is an image. Their address, NTN and Reg # were typed
text in a blue bar, so the band was rendered with just that text removed and
the fields are printed back over the bar live. Their sheet is US Letter, so
positions were scaled by 210/216. Their table led with "Quantity in KG" and put
the HS code in the description. At their request it uses the standard seven
columns, keeping their heavy header rules and pale green (#e2efd9) totals row.
The FBR invoice number sits in the left-hand value column, under NTN, because
their right-hand column is too narrow for 27 characters.

`templates/invoice_paper_experts.html` — Paper Experts, username
`3520261094743`. The sheet has no images. The masthead ("PAPER EXPERTS / The
Paper You Need") is typed serif text, rendered to a 16 KB image. The title is
small, brown (#984806) and right-aligned. The FBR invoice number went into the
empty band their buyer block already had between Address and Registration #.
Also replicated: an income-tax note highlighted line by line in #9cc2e5 (fixed
text); a double rule over "Signature & Stamp"; the summary box's bottom rule
running on to the right; and a centred footer with the address, NTN and Reg
live and the cell number fixed. A green-and-grey mark beside the footer is
drawn as inline SVG. US Letter, scaled by 210/216.

---

## Quick reference

```bash
# 1. artwork
python scripts/extract_brand.py templates/user-invoices/<username>.pdf \
    --images --band 40 --username <username> --emit-brand

#    check it; if the black prints tinted, re-emit with --octree [--colours 64]

# 2. build templates/invoice_<client>.html, importing brand/<username>.html

# 3. register: LEGACY_TEMPLATES + CLIENT_TEMPLATES + WITH_PARTIALS

# 4. assign
#    UPDATE clients c SET tpl_template = 'invoice_<client>.html'
#      FROM users u WHERE u.id = c.user_id AND u.username = '<username>';

# 5. restart Flask, then:
python -m pytest tests/ -q
```
