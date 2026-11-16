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

The five paper clients carry an eighth, **No. of Reams**, between Quantity and
Price. See "A column that is printed and nothing else" below before adding one
of these to anybody.

Live text laid over a letterhead bar must **never** use `overflow: hidden`. A
longer address would silently cut off the NTN and Reg # printed after it.

### A column that is printed and nothing else

Clients ask for figures that belong on the page and nowhere else. Reams is the
worked example: five paper merchants wanted the count beside the quantity, and
nothing on the invoice is derived from it.

Such a field has to be added in five places, and the fifth is the one that is
easy to miss:

1. **`templates/create-invoice.html`** — the input on the Products step, inside
   `#product-reams-wrapper`, shown by `applyReamsUI()` for the usernames in
   `REAMS_CLIENTS`; a column in the Products and Review tables, guarded by the
   same check; read in `addProductItem()`; carried through
   `normalizeDraftItem()` so a reloaded draft keeps it; and deleted in
   `submitInvoice()` for everyone else, the way `product_code` already is.
2. **`static/css/create-invoice.css`** — the item form's first row is a
   four-column grid that collapses to three when neither Product Code nor
   Reams is showing. Both `:has()` rules must name the new field, or it drops
   onto a line of its own. The extra table column also needs the tighter cell
   padding, or "Actions" clips against the table's right edge at 1518px.
3. **`invoice_form_routes.py`** — `items_list` is a whitelist. A key that is
   not copied into `item` there never reaches the template, however faithfully
   the browser sent it. `REAMS_USERNAMES` gates it so no other client's payload
   grows a field.
4. **the client's template** — the column, *and* the filler rows and the
   in-table totals row, which are written cell by cell. Scale the other widths
   so they still sum to 100.
5. **`app.py`** — add the key to `PDF_ONLY_ITEM_FIELDS`. `submit_fbr()` sends
   the stored payload straight to FBR, whose item schema has no room for it.
   The strip is a copy, not a `pop`: `json_data` is also what the PDF is
   rendered from and what is written to the `invoices` row.

`tests/test_client_templates.py` guards the result two ways —
`ItemsTableAlignment` fails if any row of an items table is not as wide as its
header, and `ReamsColumn` fails if the column disappears, appears on a client
who did not ask for it, or changes any other figure on the sheet.

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

All five of the clients below also print a **No. of Reams** column between
Quantity and Price, at their request; see "A column that is printed and nothing
else" above for what that involves.

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

`templates/invoice_fk_printers.html` — F.K. Printers, username `3520235613477`.
The only client so far who sent a **photograph** instead of a PDF, so step 1
could not run: there is no page to lift objects out of. Recovering the masthead
from the photo was tried first — background-normalise by dividing each channel
by a smoothed local maximum, deskew 0.75°, denoise, upscale, re-steepen the
edges — and it was still too soft to print, because 900 px across 169 mm of
paper is about 135 DPI and no amount of sharpening invents detail. The client
was asked for their logo file, sent one, and the masthead is simply cropped out
of that: `templates/user-invoices/3520235613477-logo.png`, 2081 × 338 px across
169 mm, about 313 DPI, octree at 32 colours. **Ask for the logo file before
trying to rescue a photo** — it took one message and was better than any
amount of processing. The photo is still kept as
`templates/user-invoices/3520235613477.jpeg` because it is the only record of
their layout.

That file has their address, email and phone typed under the services bar.
Those lines are cropped off, as the usual warning above requires: the address
is an FBR field and prints live from the payload.

Their sheet: full-width masthead, centred address / phone lines, underlined
title over the seller's two tax numbers, a ruled buyer box on the left beside a
right-hand column of bold labels with each value sitting on its own underline
rule, an open items table between two heavy rules, right-hand totals, a boxed
amount in words at the left, and "Prepared By :" / "Accounts Manager" rules.
At the client's request the table drops their P.O.No. and P.O. Date columns and
carries the per-line sales tax rate and value inclusive of tax instead. They
also did not want the HS code on the sheet at all — not as a column and not
under the description — so this is the one replica that does not print one. It
is still submitted to FBR on every line, and `RequiredIdentifiers.WAIVED` in
`tests/test_client_templates.py` records the exemption by name; a waiver goes
there only when the client has actually asked for it. A "Time" row prints the
clock time the PDF is produced, from the `generated_time()` Jinja global in
app.py (fixed +05:00, because the host need not be in Pakistan). Custom fields
print under the FBR invoice number, in entry order, each only when it has a
value — this is the first replica to render them at all. Their
one reference line is sometimes a delivery challan and sometimes a purchase
order, so the template prints a "D. Challan No." row for `dnNumber` and a
"P.O. No." row for `poNumber`, each only when that field is filled. The FBR
stamp sits in the band their sheet left between the items table and the
amount-in-words box.

`templates/invoice_saad_enterprises.html` — SAAD Enterprises, username
`3520287506167`. The second client to send a **photograph** instead of a PDF,
and the one that shows what to do when there is no logo file either. Their
letterhead is almost all flat colour — a black banner running off the left
edge to a chevron point, an orange band off the top right, a grey shadow, a
double-ruled services box, and three footer bars — so none of it was lifted.
It is **drawn as inline SVG** from measurements off the photo, which prints
sharp, at true flat colour, and carries none of the photograph's lighting or
skew. Only the St monogram and the SAAD ENTERPRISES wordmark are artwork.

Even those are not photographed pixels. Cropping and sharpening them gave a
mottled, haloed mess — the same dead end F.K. Printers hit. What worked was
separating the crop into *coverage* (how much ink is at this pixel) and *hue*
(orange wordmark or white monogram stroke) and re-drawing the lettering from
those two fields in the brand's own colours on the exact black the SVG paints.
The shapes still come from a ~140 DPI photo and are soft, so this is a
stand-in: **ask for the logo file and re-emit the partial**, and nothing else
in the template has to change. The scratchpad script is in the commit.

Their address, phone and e-mail sit on the footer bars as live text over the
drawn shapes, never baked in — the address is an FBR field.

Their items table carried only S.No., Description, Qty, Unit, Rate and Value.
HS Code, S.Tax Rate, Sales Tax and Value Incl. Sales Tax are added, because
those are required on the face and this client has not asked for any of them
to come off. The identifier column's rules are longer than theirs (42 mm
against 24 mm) so the 27-character FBR invoice number stays on one line.
Time of Supply is theirs to state, so it reads a custom field of that name and
falls back to the print clock only when they leave it empty. A4.

`templates/invoice_power_vision.html` — Power Vision, username
`3740545496385`, and `templates/invoice_bilal_traders.html` — Bilal Traders,
username `3740549685449`. The first two clients who sent **a letterhead and
nothing else**. There is no invoice of theirs to copy, so these are not
replicas: the letterhead goes on top and the body below it is the shared
arrangement the other templates use — label-and-value buyer block, the standard
seven columns, filler rows, an in-table totals row, the two-row summary box and
"Signature & Stamp". `scripts/extract_letterhead_brand.py` builds both partials.

Both letterheads end in a **reversed black bar carrying the seller's address**,
and under it the words "Sales Tax Invoice". Both are cropped off:

* The address is an FBR field — the usual warning above. The bar is *redrawn*
  in the template and the address printed into it live from `data`, so their
  design survives and the sheet can never contradict what was filed. Power
  Vision's mobile number sits in that bar too and is not in the payload, so it
  is fixed text; Bilal Traders' Cell / Ph block is left inside the artwork,
  because a phone number is not an FBR field and it is part of their design.
* "Sales Tax Invoice" is the document's title, not stationery. It is set as
  text in their letterhead's italic serif so a credit or debit note can say
  what it actually is.

Neither letterhead carries an NTN or Sales Tax registration number and a sales
tax invoice must show both, so they print on their own line under the address
bar, live from the payload.

These files are flat black line art at about 262 DPI, which is what artwork
should look like — compare the two clients above who sent photographs. One
wrinkle: an 8-colour octree rounds paper that reads 252-255 to a near-white
grey, which prints as a visible panel behind the masthead. The script snaps
the levels (60/238) before quantizing, so the paper is exactly 255.

Neither client is one of the five paper merchants, so neither carries the
No. of Reams column. Both are A4.

`templates/invoice_bilal_brothers.html` — Bilal Brothers, username
`3740582041351`. A letterhead and nothing else again, so the body is the same
shared arrangement as the two above. What is different is the *source*: they
sent a **photograph** of a blank sheet, about 870 px across 210 mm — a little
over 100 DPI, below even the F.K. Printers photo that could not be rescued.

So almost nothing is lifted from it. Everything on their letterhead except
the crest is type — the "BILAL BROTHERS" wordmark, the "Govt Contractor &
General Order Suppliers" strapline, the two double rules, the four-line
"Deals in : …" paragraph and the footer rule and address line — and all of it
is **set as text**, in the same faces and at the proportions measured off the
sheet (the wordmark is 110 mm across on their original). Set as text it
prints sharp at any size and carries none of the photo's lighting, skew or
JPEG noise. This is the SAAD Enterprises move generalised: **draw what is
flat, lift only what is genuinely artwork.** When a photographed letterhead is
mostly type, that is nearly all of it, and the result is better than any
rescue of the pixels.

`scripts/extract_bilal_brothers_brand.py` lifts the one piece that is artwork,
their crest: deskew 1.23°, divide out the lighting so the paper is flat white
across the frame, upscale, then smoothstep the levels so the ink is solid and
the paper is solid with the edges left soft. Pitching the low level at 120
keeps the shield's outline the thin double rule it is on their sheet; higher
and it fattens to one heavy stroke. The script inside the shield does not
survive — at 134 px across it never would — but it reads as texture, which is
roughly how it reads on their own sheet, and the same four words are set in
type beside it. At about 160 DPI the crest is a **stand-in: ask for their logo
file and re-run the script**; nothing in the template has to change.

The near-white panel wrinkle from Power Vision reappears here for a different
reason: the smoothstep takes clean paper to 255, but the anti-aliased pixels
just under it share an octree node with it and pull the node's average to 254.
The script snaps everything at or above 240 to pure white before quantizing.

The "Deals in" paragraph's four line breaks are written out rather than left
to wrap, so their centred taper is reproduced on any machine and does not
shift with whatever font the server has. Their address prints live in a fixed
footer that repeats on every page; the mobile number beside it is not in the
payload and is fixed text. The blue rubber stamp in the corner of the
photographed sheet is ink they applied by hand, not stationery, and is not
reproduced. Their "Our Ref. No." / "Your's Ref. No." / "Date" rules are the
blank sheet's correspondence fields; on an invoice their place is taken by the
buyer block and the identifiers, which land at the same depth on the page.
Like the two above, their letterhead carries no NTN or Reg #, so both print on
their own line under the masthead. A4, no reams column.

`templates/invoice_aero_alliance.html` — Aero Alliance, username
`3740564149343`, and `templates/invoice_beacon_alliance.html` — Beacon
Alliance, username `3740545582835`. Two more photographed letterheads, 840
and 891 px across 210 mm (about 102 and 108 DPI), so the Bilal Brothers rule
applies again: **draw what is type, lift only what is genuinely artwork.** On
both sheets the whole masthead — wordmark, strapline, "Deals in" lines,
address, phone and e-mail — is type and is set as text at the proportions
measured off the sheet. `scripts/extract_alliance_brands.py` does both.

What is new here is that the artwork is **colour**, which the black-line-art
pipeline cannot handle:

* The lighting is divided out **per channel** rather than on luminance. That
  flattens the lamp's falloff and white-balances the paper in one step, so
  the blue does not come out muddy where the sheet was in shadow.
* JPEG stores chroma at half resolution, so small coloured detail arrives
  washed out. Saturation is restored in HSV, but only on pixels that already
  have colour and are not near-white, so the paper cannot pick up a tint.
* The quantizer gets **64 colours**, not 8: a globe with continents and a
  two-tone swoosh have real tonal range.

Getting the paper white took three passes and all three are needed. The
page-wide division leaves each crop with a little of the lamp's warmth, so
each crop **sets its own white point** from the 88th percentile of its own
pixels — it is more than half paper, so that lands on paper and clips the
rest of the paper to 255. Then a **shoulder** ramps a pixel's *weakest*
channel to white between 214 and 240, which removes a grey cast but spares
coloured ink, because coloured ink always has one weak channel. Without the
shoulder the artwork prints on a cream panel; with it applied to luminance
instead, Beacon's pale continents and the FLUKE yellow would have gone too.

Aero Alliance's logo is one piece — eagle head, logotype and the blue wing —
lifted whole. Their address is a line of *type* in the masthead, not part of
the logo, and prints live. Their Ref. No. / Date rules are the blank sheet's
correspondence fields and are replaced by the buyer block and identifiers,
which land at the same depth. Their letterhead carries no NTN or Reg #, so
both print on their own line. No footer: their sheet has none.

Beacon Alliance has three things the others did not:

* a **watermark** across the middle of the sheet, which is the *same artwork*
  as the logo, scaled up and faded. The template re-uses the `masthead`
  macro at 118 mm and 15% opacity rather than lifting the washed-out pixels,
  which prints far better. `position: fixed` with `z-index: -1`, so it
  repeats and sits behind the text. Everything passes over it as it would on
  their pre-printed sheet — except the FBR stamp, which keeps a white ground
  because a QR code wants clean ground to scan from.
* a **distributor band** at the foot (Pro'sKit / DORMER, elme / eurosaft,
  PPG, TOKU / FLUKE, TOPTUL / Snap-on), emitted as a second macro,
  `partners`, and fixed in the bottom page margin. Their rubber stamp had
  been pressed over its right-hand end, so that rectangle is **masked with
  paper white** before the band is emitted — hand-applied ink must never end
  up looking like printed stationery. Check for this on any photographed
  sheet: the stamp is the one thing on it that is not theirs by design.
* a **two-colour wordmark**, BEACON navy and ALLIANCE maroon, set as two
  spans. Their NTN and STRN are typed on the sheet and all the same still
  print live — they are FBR fields.

Both clients' logos, and that partner band especially, are about 105 DPI and
are **stand-ins: ask for the letterhead file and re-run the script.** Nothing
in either template has to change. Both A4, no reams column.

`templates/invoice_salon_marketings.html` — Salon Marketings, username
`3520223163127`. A cosmetics distributor, and the first replica of a sheet
that **was not a tax invoice at all**: their old software printed a plain
sale bill with no HS code, no sales tax, no NTN and no registration number.
The layout is copied closely — ruled frame round the whole page, SM mark top
left with the company name and contact block beside it, boxed "INVOICE NO:"
top right, grey-headed items table, the amount in words centred under it, and
the Prepared by / Verified by / Remarks rules at the foot.

Dropped from their sheet at the client's request: "by Saleem Cosmetics" under
the company name, and the "Customer Openning Balance" line — this application
files invoices and keeps no customer ledger, so that figure could only ever
have been wrong here.

Dropped as a matter of house rule: their Bonus, Disc(%) and Disc Value
columns, and the serial-number column. Every replica prints the same seven
columns; the client's styling is copied, their column set is not. Nothing in
the create-invoice form feeds a bonus or a line discount, so those columns
could only have printed zeros. Their Code 128 barcode of the invoice number is
not drawn either: nothing in this repo draws one, and the FBR QR below carries
the same identity and more.

Added, because the document has to be a sales tax invoice: the HS Code and
Sales Tax columns, seller and buyer NTN / Reg #, and the three FBR marks.
Their phone number and email are fixed text in the template — neither is in
the payload. Their address is **not**: it is an FBR field and prints live.

This is the only client with a **Sale Person** line and a buyer **Phone** row.
Both are printed-only, like the paper clients' reams count: they are gated on
`SALES_PERSON_USERNAMES` in `invoice_form_routes.py` (mirrored by
`SALES_PERSON_CLIENTS` in `create-invoice.html`), stored with the invoice, and
stripped from the FBR payload by `PDF_ONLY_INVOICE_FIELDS` in `app.py`. Sale
person names are remembered per client in `clients.sales_persons` — a name is
added the first time an invoice is created with it. The buyer's number lives
in `buyers.phone`. Both columns come from
`migrations/2026-10-20_add_sales_persons_and_buyer_phone.sql`; every read of
them is guarded, so an app ahead of its migration just does not offer the
fields. Unlike the paper clients' sheets their items box has **no filler
rows** — their own ends at the last line with the Total straight under it. A4.

They also file **everything under SRO 297(I)/2023**, so every line of every
invoice of theirs carries `sroScheduleNo = 297(I)/2023-Table-I`,
`sroItemSerialNo = 12` and `saleType = Goods as per SRO.297(|)/2023` (the pipe
is FBR's spelling in the transaction-types list, not a typo), and the sandbox
scenario starts on SN024. The form fills and locks the three fields;
`SRO_297_USERNAMES` in `invoice_form_routes.py` writes them again onto every
item, so a stale tab, a restored draft or a direct API call cannot file a line
with a blank or wrong SRO. Same shape as the older `3075270` and
`3520299316743` SRO special cases, which are untouched.

`templates/invoice_zahid_steel.html` — Zahid Steel Industries (Pvt) Ltd,
username `J527785`, users.id `804a5a5a-9409-4883-abee-69881b9a0155`. A steel
mill, and the first client whose letterhead PDF had **no embedded images and
no text objects at all**: the whole sheet is vector outlines, so
`scripts/extract_brand.py --images` finds nothing to lift.
`scripts/extract_zahid_steel_brand.py` renders the page at 600 DPI instead and
crops the two pieces that are genuinely artwork — the `masthead` (ZSI gear
monogram, wordmark and the red ISO 9001 : 2015 line) and the `ornament` (the
half-gear in the bottom right corner). The ornament's right and bottom edges
are the sheet's own edges and are **not trimmed**: the template bleeds it back
off the page with `right: -9mm; bottom: -40mm`, so the designer's crop
survives. The ornament and the footer line are `position: fixed` and repeat on
every page, as pre-printed paper does, which is why `@page` reserves a 40 mm
bottom margin instead of the reference sheet's 10 mm.

**The format is a deliberate copy of the 192-1 sheet**, which belongs to
**Zahid Enterprises Steel Re-Rolling Mills — a separate business**. Only the
format is taken; none of that business's data appears here. That sheet is
rendered by `invoice_template_universal.html`, so this replica simply adopts
its house style rather than inventing one:

* **Arial 13px**, `th, td { border: 1px solid #333; padding: 6px;
  text-align: center }`.
* **`#a7c7e7` section bands with black text, 8px padding — the only fill on
  the sheet.** Every other cell is white; labels are bold black on white.
* **Logo at 50 mm, left; seller block right, on a 50/50 split.** The company name is
  **15px**, not the 13px body size — on 192-1 it is visibly larger than the
  lines under it, and that contrast is most of what the top of the sheet
  reads as. Then address plain, and `NTN:` / `STRN:` / `FBR INVOICE #:` with
  bold labels and plain values. Narrowing the left cell to fit the logo
  pulls the seller block inward and immediately stops the two sheets looking
  alike; the client spotted it at 42/58.
* **Seven-column items table with filler rows to five.** The product
  description reads left; Sr. No. and all five figures centre.
* **AMOUNT IN WORDS left, three-row summary box right.**
* The one departure, asked for by the client: it closes on **"This is an
  FBR-generated copy; no signature required."** and there is **no AUTHORISED
  SIGNATORY** rule or caption. The letterhead footer is also added, which
  192-1 does not have.

Column widths and the summary geometry were **measured off the 192-1 page**,
not guessed. The summary box is then snapped to the items table's own column
rules — it starts at 55% where "Value Excl. ST" starts and its inner rule sits
at the 85% line before "Value Incl. ST" — which lands within a percent of
where 192-1 puts them while making the rules line up exactly. Change an items
column width and both numbers have to follow.

**Getting here took four review rounds, and the lesson is worth more than the
result.** Rounds two and three were spent undoing "improvements" nobody asked
for: a round that centred every cell, a round that removed the filler rows,
retuned colours, a different typeface. Each was a defensible reading of a
terse complaint and each was wrong. What finally settled it was the client
sending the reference PDF back with "he wants exactly this format". **When a
client complains about the look of a replica, ask for a marked-up image or the
exact sheet they want before changing anything** — and read every vague
complaint as narrowly as it can be read.

Two traps this file hit, both worth checking in any replica:

* `.no-border td { border: none }` also strips the borders off a table
  *nested* inside that one — here, the summary box inside the totals row. Use
  `.no-border > tbody > tr > td`.
* The closing block (totals + FBR stamp + note) is one
  `page-break-inside: avoid` unit. Grow the type and it can stop fitting under
  the item rows, jump to the next page and leave half a page blank.
  Five is the most that fits one page (the designed depth of the filler
  box) and six must spill with the block intact. Enlarging the logo from
  42 mm to 50 mm cost exactly one row — header height comes straight out of
  the item box. **This is now pinned by `ZahidSteelPagination` in
  `tests/test_client_templates.py`**, the only test in that module that
  renders real pages, because markup cannot answer "does it fit". It checks
  that 1–5 lines stay on one page, that a sixth spills rather than
  vanishing, that the closing block never splits and the note is never
  orphaned, that the stationery footer reaches every page, and that a
  continued items table repeats its headings. Both halves are
  mutation-checked: growing the logo to 62 mm and removing
  `page-break-inside: avoid` each fail it.

  Two things that test had to get right, worth copying into any other
  pagination test: a wrapped table cell comes back from the PDF as several
  lines, so compare with whitespace stripped or the assertion passes
  vacuously (one of them did); and pass a **real** image for the FBR mark,
  since the template sizes it `height: auto` and a broken image collapses
  the row and changes the fit.

This replica does **not** use the house seven-column items table. The client
asked for the column set from the reference sheet, which puts the HS code in
the buyer grid rather than in a column and carries a Sr. No. The tax rate is
in the "Sales Tax" heading when every line shares one, and on each line when
they do not — that branch is the one to look at if a mixed-rate invoice ever
prints oddly.

**The letterhead's tax numbers are the wrong company's.** It carries NTN
8255820-3 / STRN 32-7787-6203-99-6 — those are **Zahid Enterprises Steel
Re-Rolling Mills'** numbers. Zahid Steel Industries (Pvt) Ltd is NTN
`J527785-7` / STRN `3277876392575`. That is exactly why those two lines are
cropped out of the artwork and printed live from `data` instead; the seller
name and the address are live for the same reason.

One narrow exception, which the client asked for: the **NTN always prints
with its check digit** (`J527785-7`) even when the payload carries the bare
`J527785`, because their seller profile may still hold the short form. The
substitution is keyed on this one company's number, so no other seller's NTN
can be rewritten, and the check digit is derived from the seven characters,
so the sheet and the filing do not disagree. `ZahidSteelCheckDigit` in
`tests/test_client_templates.py` pins all three of those properties. It is a
display fallback, not the fix — run the SQL and it never fires.
**The stationery footer is fixed text, verbatim from the letterhead** —
wording, capitalisation and punctuation. It must NOT interpolate
`data.sellerAddress`: the filed address is longer than the line the
letterhead sets, so it overran toward the gear, and it made a fixed piece of
stationery change shape with the payload. The address is an FBR field and
still prints live, in the seller block at the top where 192-1 carries it.

Its geometry is measured off the letterhead at 600 DPI, not chosen — left
12.7 mm from the paper edge, line 1 **100.1 mm wide**, 4.74 mm baseline to
baseline, last descender 11.6 mm above the paper edge. The size is set from
the line's **width**, not its height: their footer uses a narrower grotesque
than Arial (about 30.3 units of width per unit of cap height, against Arial's
36.9, and the narrowest face installed here is 32.4), so the two cannot both
be matched. Width wins — it is what puts the line in the same place on the
paper. A measuring script that crops to a fixed fraction of the page will
silently clip these lines and report nonsense; measure across the full width
and stop short of the gear.

`zahid_steel_setup.sql` assigns the template *and* corrects `clients.strn` and
the default `business_profiles` row, because a correct template in front of a
stale profile still prints the stale numbers. That script was run twice
against a throwaway Postgres in both the "profile already exists" and "brand
new client" cases. A4.

`templates/invoice_global_surface.html` — Global Surface Technologies,
`users.id ed156fdd-086e-480c-be18-a2c85d9e31b1`. Built from their own
Word-made sheet rather than a letterhead file, so the stationery is the top
and bottom of that page. Arial Black 10.1 pt for the buyer box, the
identifier block and the items headings; Times for everything else; a solid
black heading band, which is the sheet's only fill. Nine columns: their
eight plus HS Code. A4 (their sheet is US Letter).

Three things this build did differently, all reusable:

**Per-client features keyed on the client *id*.** `client_features.py` is the
id-keyed sibling of `advance_tax.py`. The older switches — `REAMS_USERNAMES`,
`SALES_PERSON_USERNAMES`, `ADVANCE_TAX_USERNAMES` — all need a
`users.username`, which costs a query wherever one is not already in hand.
`client_features` takes **any** of `users.id`, `clients.id` or the username
and matches on whichever it was given, so the create-invoice page answers
from `session` alone. Prefer this shape for the next per-client switch.

It currently raises the custom-field cap to four (PO Number, PO Date, DC
Number, DC Date) and turns on the buyer Phone row. Note that
`INVOICE_CUSTOM_FIELDS_MAX = 2` lived in **four** places — `app.py`,
`invoice_form_routes.py` twice (the stored payload and the saved default
names) and the JS in `create-invoice.html`. Raising it means all four, and
the defaults endpoint is the one that bites: its GET and PUT must use the
same cap or a client's saved four names come back truncated to two.

The form cannot import `client_features`, so `app.py` renders
`buyer_phone_for_client` into `create-invoice.html` and `isBuyerPhoneClient()`
ORs it in. A server-rendered flag beats a second copy of a client list, and
the field is then right on first paint instead of after the username fetch.

**Artwork that is photographs needs splitting.** One PNG of their whole
header band was 200 KiB inlined into every invoice. Split into two JPEG
photographs plus the WordArt lettering as a 64-colour PNG it is 130 KiB with
the lettering sharper, laid back out by a three-cell table at the widths
measured off their sheet. Trim **only** the lettering to ink — ink-trimming a
photograph crops whatever is pale at its border and the pieces stop lining
up. See `scripts/extract_global_surface_brand.py`.

**Size a column from measured widths, not by eye.** Each column is the wider
of its widest heading *word* in Arial Black 10.1 pt and its widest plausible
figure in Times Bold 10.6 pt, plus the 6 pt of padding. Probe each string by
rendering it alone on an oversized `@page` — an A4 probe clips it — and read
the text rect back. Done properly this caught HS Code sitting at 8.5 % when
its own values need 9.2 %: it had been overflowing silently since the first
cut. The table of figures is in the template's own comment.

**On "make it fit one page".** The closing block — PO/DC box, amount in
words, summary box, FBR marks — breaks as one unit, so its height decides the
capacity. Stacked instead of laid out side by side it came to ~92 mm and
exactly ONE four-line item fitted a page: the block jumped whole to page two
and left half of page one blank. Side by side it is ~65 mm. Trimming the page
bottom margin to the footer's own measured height, 0.5 pt a side off the cell
padding and 3 mm off the FBR gap bought another ~12 mm, which is one row.

Capacity is nine one-line descriptions, six two-line, three four-line — and
five of the four-line descriptions their reference sheet carries will **not**
fit A4 under their letterhead. Quote the measured number rather than quietly
shrinking a client's type. `GlobalSurfacePagination` pins both ends:
`REQUIRED = 5`, what the client asked for, so a failure reads as the broken
promise; and `CAPACITY = 6`, the measured headroom, a tripwire that fails one
change before the promise does.

`templates/invoice_material_craft.html` — Material Craft, id given
`71a52649-1b77-4748-9a51-fd33ce621000`. The first **letterpad-reserve**
replica: the client feeds their own pre-printed stationery through the
printer, so the template renders nothing at all in the masthead or the
footer and reserves both as page margins. There is no brand partial and
Step 1 does not apply — there is no artwork to extract. Times New Roman
10 pt throughout, 12.5 pt bold for the title. A4.

**Measure the reserve off their sheet; it is the whole template.** Their
masthead ink runs 5.2 → 16.2 mm from the top edge and their footer ink
starts at 276.3 mm, so the page is `margin: 21mm 18mm 23mm 24mm` — their
ink plus the air their own sheet leaves. The top began at 19 mm and the
client asked for 2 mm more after seeing it printed: a sheet that measures
right can still feed a millimetre or two high, and the margin is the only
place that tolerance can live. Get this wrong and the invoice
prints over their logo.

**Pin it on pixels, not on CSS.** `MaterialCraftLetterpad` renders real
pages, rasterises each at 10 px/mm and asserts the first and last inked
row on *every* page, continuation pages included. Asserting the stylesheet
says `21mm` proves nothing about where ink lands. Mutation-check it both
ways — shrinking either margin must fail it.

**A photographed sheet is still measurable.** This one clipped the top edge
of the paper and had a folded bottom-right corner, so the page could not be
found as a quadrilateral. Fit lines to the left, right and bottom edges,
intersect for the two bottom corners, and reconstruct the top from the A4
ratio. The missing strip came out at 1.3 mm, which is itself the proof the
sheet is A4: US Letter would have implied 20 mm of paper missing above a
logo that is visibly at the frame edge.

**Cap height lies about type size; width does not.** A cap-height reading
made the title 15 pt. Rendered, 15 pt is 53.2 mm against their printed
44.5 mm — it is 12.5 pt. Seven body strings then landed within 2 % of
10 pt. Always compare a rendered string's width against the same string on
their sheet; see "Size a column from measured widths" above.

**Cell padding and leading are where a sheet quietly loses rows.** The first
cut used 2 pt padding and 1.4 leading in the ruled grids. Their rows measure
5.3–5.6 mm; that combination gives 6.7 mm. Nothing *looked* wrong and it
cost five line items. 1 pt padding with 1.3 leading reproduces them. The
buyer block keeps 1.4, because its printed line pitch is 5.0 mm.

**Their empty items area is one open cell, not ruled filler rows.** Their
sheet draws no rule between the last item and the totals. So instead of N
filler rows, emit a **single** row whose height is set inline at
`(ROWS - item_count) * 5.3mm`: same depth, their look, and still one
rectangular row for `ItemsTableAlignment`. Check which of the two a client's
sheet actually does before copying the Global Surface pattern.

**`client_features.py` now holds a cap map, not a set.** `FOUR_CUSTOM_FIELDS`
became `CUSTOM_FIELD_CAPS = {id: n}`, because this client briefly needed
five. `custom_fields_max` returns the highest cap any id it was given
names. The create-invoice form needed no change — it reads the number the
server renders, which is the payoff of that pattern.

**A custom field can be routed somewhere other than the field grid.** Four
of the five filled the grid at the top right; the fifth, Delivery Address,
was picked out **by name** and printed in the buyer identifier box, so
re-ordering the fields on the form could not put the plant in the P.O row.
Round 2 moved Delivery Address off the invoice and onto the buyer, which is
where it belonged — but the by-name routing is still the pattern to copy
when a custom field has to land somewhere specific on a sheet.

**A replica may write money in its own notation, and that is not a waiver.**
They write `Rs 102,000-00`. Rather than exempt them from
`RequiredIdentifiers`, the test grew `MONEY_NOTATION` — a per-template
re-spelling applied only to the cases listed in `RequiredIdentifiers.MONEY`,
with a test asserting the re-spelling leaves the digits identical. An
identifier — NTN, STRN, HS code — is never re-spelled.

**Look for a body-text home for the seller's name before reaching for a
waiver.** A blank masthead would have failed
`test_buyer_and_seller_details_survive_a_missing_logo`, except their sheet's
sign-off reads "For &lt;company&gt;," in the body. Printing that live from
`data.sellerBusinessName` satisfies the test honestly, and it is what names
the seller on a PDF that is e-mailed rather than printed on their paper.

HS Code was waived for them in this round — their first sheet's five-column
table had nowhere to put one and the client asked for it exactly as drawn.
Round 2 withdrew the waiver: their second sheet has an HS Code column.

### Material Craft, round 2 — the two-page invoice

They sent a generated invoice that ran to two pages beside the sheet they
wanted. One cause dominated, and it is worth knowing about for any replica:

**A long unit of measure will quietly double your row height.** FBR's own
name for a countable thing is `Numbers, pieces, units` — 22 characters,
which wrapped to four lines in a 19 mm Qty column. Six line items cost
about 78 mm of page that way, and nothing in the markup looks wrong. Their
sheet writes `04 each`. The template now shortens that one FBR unit name
and prints a whole quantity as a padded integer; every other unit prints as
FBR spells it, and a fractional quantity is left exactly as it came,
because rounding one would change what the sheet says was supplied.

**Check whether the client shrank their own type to make it fit.** Their
second sheet is set at about 8.85 pt throughout where the first is 10 pt —
they had scaled the whole page down in Word to get six items on it. Copying
either number blindly is wrong. The page keeps the 10 pt of the sheet they
pointed at, and only the items table drops to 9 pt, which is the size at
which their own longest description fits one line as it does on their
paper. Capacity went from 6 two-line items to **6 promised / 7 measured,
and 12 of their own single-line descriptions** — it was 7/8 until the top
margin grew by 2 mm, which at 9 pt is most of a row and cost exactly one,
and only of a description longer than any on either of their sheets.

**A second sheet from the same client is a second measurement, not a
contradiction.** This one confirmed the letterpad bands exactly — masthead
ink to 16.2 mm, footer from 276.3 mm — so those two margins are now
measured from two independent sheets. It also changed real content: an HS
Code column appeared (so the HS-code waiver came off), the CNIC row went,
and Supplier # changed from 0000332375 to 1006060.

**A value that changes between two of a client's sheets is a field, not a
constant.** Supplier # is the vendor number the *buyer* files the seller
under — it is what that buyer matches the invoice against when paying.
Hardcoding the first one would have printed the wrong number on every other
customer's invoice. It and Delivery Address both moved out of the invoice's
custom fields and onto the **buyer**, where they belong: set once per
customer on the Buyers page, filled in automatically when that customer is
picked, copied onto the invoice so a reprint reproduces the sheet as sent.
Both are printed-only (`PDF_ONLY_INVOICE_FIELDS`).

Adding a third optional buyer column made the `has_phone`-style f-string
guards unmanageable, so they are generalised: `OPTIONAL_BUYER_COLUMNS` plus
`buyer_select_list` / `buyer_optional_values` / `_BUYER_COLUMN_NORMALIZERS`
in `invoice_form_routes.py`. The SELECT keeps the same arity whatever the
schema holds, so callers unpack positionally. Exercised against a throwaway
Postgres in all three schema states — no columns, phone only, and after the
migration applied twice.

**A replica may print a figure in its own notation, and that is not a
waiver.** They write money as `Rs 1,234-56` and quantities as `04`. Rather
than exempt them from `RequiredIdentifiers`, that test grew
`FIGURE_NOTATION`: a per-template, per-field re-spelling applied only to
the fields in `RequiredIdentifiers.FIGURES`, with a test asserting the
re-spelling preserves the digits (trailing zeros may be dropped, nothing
else may change). Identifiers — NTN, STRN, HS code — are never re-spelled,
because a differently-spelled identifier is a different identifier.

**Before "fix the profile", check what FBR already knows them by.** Their
sheet shows NTN 1270230-7 while the profile holds 3520229157309 — their
CNIC. The tell is in their own invoice: the FBR invoice number reads
`3520229157309DI39CBBP165272`, and FBR builds that from the seller's
registration. So changing the profile would file under a different
identifier. `material_craft_setup.sql` carries that UPDATE commented out,
behind a header saying what to confirm first.

`templates/invoice_rubberx.html` — Rubber X Technologies, `users.id`
`af5d3ee4-f799-49a0-84db-813e18fdb9a7`. The first replica of **somebody
else's sheet**: the client asked for F.K. Printers' invoice with their own
branding on it, and sent their own invoice for reference only. So this is
`invoice_fk_printers.html` almost line for line, with three things taken
from their own document because they are theirs — the logo, the Original /
Duplicate / Triplicate marks, and the services line.

**When a client asks for another client's sheet, copy the sheet — but not
the waivers.** F.K. Printers is the one replica that prints no HS code,
because they asked twice for it to go. That waiver is recorded against
their template *by name* in `RequiredIdentifiers.WAIVED`, and it does not
travel with the layout: this client has not asked for it, an HS code is
required on the face of a sales tax invoice, and the column is simply
added back. Ten columns, not nine. Check what is waived before copying
anything.

Their logo is a **322 × 135 px PNG** — the smallest artwork any client has
sent. It works where F.K.'s and SAAD's photographs did not, for one
reason: it is flat three-colour vector-style art (white, `#ff9900`,
`#990000`), so there is no detail to lose, only edges, and edges can be
re-cut. `scripts/extract_rubberx_brand.py` crops, upscales 3× with Lanczos,
re-steepens with an unsharp mask and quantises to 16 colours with octree —
about 14 KiB inlined. At the 45 mm it prints at that is 178 DPI, below the
~300 DPI everyone else runs at, so it is a **stand-in: ask for the vector
file and re-run the script.** Nothing in the template changes; it sizes by
width with `height: auto`.

The near-white panel wrinkle from Power Vision and Bilal Brothers appears
here a third time, and snapping the paper *before* quantising was not
enough on its own. Octree averages the colours in a node, and the palest
edge pixels still land in the paper node, so it came back 254 — a flat
panel one level off the page, clearly visible behind the logo. The script
snaps at 240 **and then rewrites the lightest palette entry to 255 after
quantising**. Do both.

**The services line is a footer, and it is typed, not lifted.** On their
own sheet it runs down the left of the page beside the logo, under an
orange arrow. The client asked for it at the bottom, so it is `position:
fixed` in a 22 mm bottom margin and repeats on every page, as a pre-printed
band would. The list's seven line breaks become interpuncts; the words are
verbatim. The rule above it is their brand orange and the words are set in
the logo's dark red, because 10 px orange on white does not survive print.

**That 22 mm bottom margin is the only structural difference from F.K.'s
sheet, and it is the whole pagination story.** F.K. needs 10 mm. The 12 mm
difference comes straight out of the items box, and F.K.'s filler budget —
a flat 2 units charged per line — overflowed here: three items whose
descriptions wrap to four lines were charged 6 units, padded with 6 more
rows, and the sheet ran to two pages.

**A filler budget must charge an item what it actually costs.** This one
estimates the printed depth from the description's length — the column sets
about 20 characters to the line at 10.5 px Arial — and charges one unit per
line. The estimate only decides how floaty a short invoice looks; what it
must never do is overflow, so the budget is set below the measured maximum.
Swept on real pages at five description depths: at 13 nothing spills, at 14
a three-item invoice of one-line descriptions does, so the budget is **12**,
leaving about 11 mm of headroom for the estimate to be wrong.

Capacity is a function of description depth, not a row count, and is
identical at every budget from 10 to 13 — which is the proof that padding
is never what pushes a line onto a second page:

| description | items on one page |
|---|---|
| one line | 12 or more (no limit found) |
| two lines | 8 |
| three lines — their own longest | 6 |
| four lines | 6 |
| six lines | 4 |

`RubberXPagination` pins both ends and is mutation-checked three ways:
restoring the flat charge, raising the budget to 14, and letting the
closing block split each fail it. The filler test renders the sheet
**twice**, once with the blank rows stripped out of the markup, because the
question is not "does this fit" but "did the padding change the answer" —
a single render cannot tell those apart, and a first cut of that test was
vacuous.

**The closing block is one `page-break-inside: avoid` unit**, as on Zahid
Steel's sheet, and for the same reason: the first overflowing invoice put
the FBR stamp, the amount in words and the totals at the foot of page 1 and
left the sign-off line alone at the top of page 2, which reads as a
second, empty document.

One more thing worth copying: the obvious assertion that the FBR invoice
number is on the closing page is **wrong** for any sheet built this way. It
prints once, with the other identifiers at the top of page 1 — the QR is
what travels with the stamp.

Their seller name prints as live text under the logo. F.K.'s name is legible
in their 169 mm masthead; a 45 mm mark is not a substitute for naming the
seller, and their own sheet types "Supplier Name:" for the same reason. The
address, NTN and Sales Tax No. are live; the phone, fax, the bottom-left
sign-off "Muhamad Basit / Owner" and the services line are fixed text.
**Round 2 took two things off, and both were F.K.'s rather than theirs.**
The Special Excise Duty row in the totals — on an invoice with no federal
excise it only ever printed `0.00`, and FED on a line is still filed to FBR
either way — and F.K.'s two signature rules, "Prepared By :" and "Accounts
Manager". Their own sheet signs off with one unruled bold line at the
bottom left and nothing opposite it, which is now all this one prints. The
lesson from the first build repeats: **when you copy another client's
sheet, every piece of it is a candidate for removal, because none of it was
this client's to begin with.**

That also moved the pagination numbers — the two removals bought back about
5 mm, which is most of a filler unit. The budget stays at 12 and the
capacity table above is unchanged, but **re-sweep after any change to the
closing block**: before, budget 14 broke on a three-item invoice; now it
breaks on a seven-item one, and is still not safe.

`rubberx_setup.sql` assigns the template and optionally seeds two custom
field names, and its footer records what their profile rows should hold —
including a Sales Tax No. that is worth confirming, because the thirteen
digits on their sheet are also their mobile number with one digit added.
A4, no reams column.

### Zahid Brothers — a measured table, and a client who pinned three numbers

`templates/invoice_zahid_brothers.html`, users.id
`74a2012a-8bf8-46e8-94fd-611e748e7c35`. Not Zahid Steel Industries
(`J527785`), who are a different client with a different sheet.

Theirs is an **Excel form**, and the first one here where the table was the
brief: the client asked for the items table "exactly the way they have
added" and for everything else to be fitted around it. So the thirteen
column widths were not chosen, they were **measured off the vertical rules
in their PDF** and written into the template in millimetres:

```python
# the x of every vertical rule, in points, straight out of their file
import pypdfium2 as p, pypdfium2.raw as c
page = p.PdfDocument("templates/user-invoices/zahid_brothers.pdf")[0]
for o in page.get_objects():
    if o.type == c.FPDF_PAGEOBJ_PATH:
        x0, y0, x1, y1 = o.get_pos()
        if x1 - x0 < 2 and y1 - y0 > 4:
            print(round(x0, 1), round(y1 - y0, 1))
```

That gave 6.92 / 13.97 / 18.24 / 18.52 / 15.88 / 8.61 / 9.31 / 10.02 /
14.50 / 16.69 / 15.34 / 8.15 / 18.45 mm — 174.6 mm, their frame — and the
rows at 9.45 mm, the heading at 9.45 and the total at 6.1. Do the same on
any sheet whose geometry the client cares about; it takes a minute and it
is not a thing to eyeball. `ZahidBrothersSheet` asserts the widths and that
they still add to 174.6, so a nudge to one of them fails rather than
shipping a table of the wrong size.

Their page is 215.9 × 329.9 mm. This one is A4 with 17.7 mm side margins,
which puts the 174.6 mm frame where theirs sits. **The height is where it
got tight**: the frame has to carry a masthead where they had two typed
lines, and an FBR band they never had, on a page 33 mm shorter. Ten lines
fit and the eleventh starts a second page — there is no headroom left, so
anything added above or below the table costs a line.

**Clipping is what makes a fixed-height row work.** Their HS Code
Description cell holds the whole of FBR's chapter heading and shows the
three lines that fit, which is why "VEHICLES OTHER THAN RAILWAY
OR...VEHICLE" prints as `VEHICLES / OTHER THAN / RAILWAY`. Reproduced with
`max-height` + `overflow: hidden` rather than a character count, so
whatever FBR returns prints the way theirs does. Every wrapping cell is
clipped the same way — which is also what lets the box be a flat ten rows
with no filler budget, unlike Rubber X and Material Craft above, whose
descriptions set their own row heights.

The exception is **Product Code & Description**, the column the buyer
identifies the goods by: a clipped part number is a different part number.
Their own sheet cut `1183104-1-1BALANCER UNIT new LS - 183104 (MEL)` after
"LS -". Here it is set at 5.6 pt on 1.05 leading instead of 7 pt, which
fits four lines in the same 9.45 mm row and prints the whole of it. Still
clipped past four, so the row can never grow.

It prints the **description alone**. The code in that column is part of the
text they type, not a second field — and the separate Product Code box they
were given printed the application's own `P-0001` series in front of it
(`P-0002-1183104-1-1BALANCER UNIT…`), so the client asked for the box to
go. `MANUAL_PRODUCT_CODES` is now empty and says why; an invoice filed
while the box existed still has a `productCode` on its line and reprints
without it, deliberately.

**Rows are sized border-box.** `height: 9.45 mm` on a cell whose box is the
default `content-box` renders at 9.45 + 1.0 of padding + 0.21 of border =
10.66 mm, and every row of this table did — 12 mm of table that is not on
the client's form, which is what pushed the sheet past the frame it is
ruled to. `box-sizing: border-box` on `.items` and its cells is what makes
the measured figures mean what they say, and it gave back two lines of
capacity: the page now holds twelve, where before the eleventh started a
second page. `ZahidBrothersPagination` measures both the row height and the
capacity, so a change that spends that headroom shows up there rather than
on an invoice. The signature block and the FBR band are wrapped in one
`.closing` div for the same reason — separately, a spilling invoice could
end on a page carrying the QR code and nothing else.

**Three figures are pinned.** The client asked for the supplier's phone,
NTN and sales tax registration number to print the same whatever the
business profile says, and they are `FIXED_PHONE` / `FIXED_NTN` /
`FIXED_STRN` at the top of the template. This is the first replica to put a
seller NTN or STRN anywhere but the payload, and it is worth being plain
about what it costs: **if their registration changes, the sheet will print
the old number while the new one is filed.** The client was told. The name
and the address stay live, and the artwork carries no numbers at all.

It is not a waiver, either. `RequiredIdentifiers.FIXED` checks the sheet
against the pinned value instead of the payload's, so the two identifiers
are still under test and still have to be theirs; `FIXED` refuses to hold a
*figure*, because pinning one would stop the sheet being checked against
the payload at all. Copy that shape if another client ever asks.

**Four rules the client gave, all in the template:**

* **13 digits is a CNIC, anything else is an NTN.** Their sheet has a
  "CNIC No" row and an "NTN if any" row; the payload has one field,
  `buyerNTNCNIC`. That is the rule for splitting it.
* **UOM prints its first term.** FBR's `Numbers, pieces, units` prints as
  `Numbers`. The split is on the first **comma**, not the first word —
  "first word" would print `Square Metre` as "Square", which is a different
  unit.
* **Item Type is the sale type** the line was filed under.
* **Part Unit is theirs to type.** Their old software held it separately
  and they asked for it back, so it is a box on the invoice form
  (`client_features.PART_UNIT`). A line that carries no figure — every line
  filed before the box existed — falls back to the derivation this column
  used to be, Gross Amount ÷ Unit Rate, so the row still multiplies out to
  the figures printed beside it. A typed **0** is a figure and prints as
  one, which is why both the template and `_normalize_part_unit` test for a
  missing value rather than a falsy one.

**The printed-only fields added for them** are the pattern to copy rather
than the reams/sales-person one, because they are keyed on the client's
**id** in `client_features.py` and answered by the server when the page
renders — so the boxes are there on first paint instead of after the
username round trip:

| Field | Switch | Stripped by |
|---|---|---|
| Reference | `prints_invoice_reference_fields` | `PDF_ONLY_INVOICE_FIELDS` |
| Term of Sales | `prints_invoice_reference_fields` | `PDF_ONLY_INVOICE_FIELDS` |
| `hsCodeDescription` | `prints_hs_code_description` | `PDF_ONLY_ITEM_FIELDS` |
| `partUnit` | `types_part_unit` | `PDF_ONLY_ITEM_FIELDS` |
| `productCode` | `types_own_product_code` *(empty)* | `PDF_ONLY_ITEM_FIELDS` |

One more switch is **not** a printed field and files nothing of its own:
`fixed_notified_defaults_to_value` opens the Advanced tax fields' **Fixed
Notified Value** box at the line's value excluding sales tax instead of at
zero. They file 3rd Schedule goods, where FBR charges tax on the notified
or printed retail price and rejects a zero outright (`errorCode 0300`), and
they sell at that price. It is a starting figure only: typing in the box
stops it following for the rest of that line, and the server never
substitutes one, so a line that is not 3rd Schedule has the box cleared and
stays cleared. What is filed is whatever the box holds.

Reference and Term of Sales are **proper fields on the Invoice Details
step, not custom fields**, at the client's request: their sheet has named
boxes, and being offered a blank "name / value" pair to retype the same two
labels into on every invoice is worse than having the two boxes.

The HS code description is read off the same `/pdi/v1/itemdesccode`
response the code was picked from — `setHsDescription` when a code is
chosen from the autocomplete, `lookupHsDescription` when one is typed or
filled in from a saved product — and shown as a hint under the box so they
can see what will print.

**The server answers it too, and that is the one that matters.** Relying on
the browser to have carried the description is what left the column empty:
a code typed rather than picked, a product saved before the column existed
or a draft filed before it did all arrive with a bare `hsCode`.
`fetch_hs_code_description` in `fbr_reference_routes.py` fills it in when
the line comes without one, reading the same per-client `hs_codes_{id}`
cache the autocomplete fills — so the common case costs no round trip, and
a miss costs an empty cell and nothing else, because the column is
printed-only and no figure is derived from it.

**`types_own_product_code` is empty now**, but kept, and the note below is
why it is worth keeping rather than deleting — the next client who wants a
manufacturer's part number needs exactly this switch and nothing else.

**`types_own_product_code` is deliberately NOT `SPECIAL_USERNAMES`.** That
older switch gives a client a Code box *and* blanks the sale type and both
SRO fields on the products page. This client needs the Code box and the
sale type, because Item Type on their sheet is the sale type. So
`code_editable` on the products page is now `special OR
types_own_product_code` while `is_special` stays what it was, and
`create_product` / `update_product` decide the same way.
`ZahidBrothersFields` pins all of that, including that none of the four
fields can reach FBR.

The artwork is their CamScanner photograph of a printed letterhead —
`scripts/extract_zahid_brothers_brand.py`, a mode `extract_brand.py` does
not have. There is nothing to lift (the page is one image) and a `--band`
crop would print the camera's defects at size. Instead the scan is split
into **coverage and hue** and re-inked: how dark a pixel is between paper
and the measured core of the ink becomes the alpha, how red it is picks
between the two printed colours, and the result is composited over white.
Gamma 0.80 on the coverage re-cuts the edges the camera blurred. That turns
RGB (57,57,57) back into black and a desaturated brick back into their red
without thresholding away the hairlines in the script. **Re-ink before
upscaling**, not after, or the halo is interpolated and baked in at twice
the size. Not upscaled at all in the end: 1713 px across 62 mm is ~700 DPI
and doubling it tripled the payload for no difference on paper.

`zahid_brothers_setup.sql` assigns the template, checks that
`products.product_code` exists (the Code box degrades silently without it)
and offers to clear any automatic `P-####` codes, which would otherwise
print on the face of the invoice where the part number belongs. A4, no
reams column, no amount in words — their sheet has never had one.

---

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
