# Invoice compliance profiles

Some buyers run an automated purchase-invoice checker that rejects a supplier
invoice unless specific fields, labels and identifier formats appear on the
printed PDF. This package turns those demands into **configuration** so we
never add per-customer `if` statements to `app.py`.

```
compliance/
├── __init__.py        public API: get_profile / apply_compliance / run_validation
├── registry.py        loads profiles/*.json, matches buyers, merges settings
├── fields.py          catalogue of printable fields + info-row builder
├── rules.py           declarative validator (mirrors the buyer's own checker)
├── normalizers.py     pure helpers (tax ids, addresses, dates)
└── profiles/
    └── fine_printers.json
```

## Safety contract

* A client **without** a profile is untouched. `get_profile()` returns `None`,
  every hook in `app.py` becomes a no-op, and their PDFs render exactly as
  before (verified pixel-identical across five template-setting combinations).
* `apply_compliance()` never raises. Any internal error is logged and the
  payload is returned unchanged, so invoice generation cannot break.
* Validation findings are **advisory** — logged, never blocking.
* Profiles are matched on `users.username` only, so the same profile applies to
  **sandbox and production** invoices.

## Adding a client

Create `compliance/profiles/<client>.json`. Nothing else. No migration, no
deploy-time toggle, no code change.

```jsonc
{
  "name": "Acme Printers",
  "enabled": true,                       // false disables without deleting
  "usernames": ["acme"],                 // users.username, case-insensitive
  "template_name": "invoice_template_universal.html",  // optional pin
  "timezone": "Asia/Karachi",            // used for TIME OF ISSUE
  "currency": "PKR",

  "template": {                          // overlays the client's tpl_* columns
    "show_seller_legal_name": false,     // print "Legal Name (FBR): ..."
    "seller_address_label": "Seller Address:",  // "" = no label (default)
    "show_signature_block": true,        // false removes AUTHORISED SIGNATORY
    "date_format": "%d-%m-%Y",           // omit to keep "15 August 2026"
    "show_hs_code": true,
    "show_sales_tax_rate_column": true,
    "max_item_rows": 5
  },

  "seller": {
    "legal_name": "…",                   // FBR-registered person/company
    "address": "…",                      // hard override, or:
    "address_suffix": ["Lahore", "Punjab", "Pakistan"]
  },

  "normalize": ["seller_strn"],          // print these tax ids as bare digits

  "rows": ["buyer_ntn", "buyer_strn", "status", "po", "currency", "time_of_issue"],
  "labels": { "po": "P.O #" },
  "require": [],                         // these rows print even when empty

  "rules": [ { "field": "…", "rule": "…", "param": "…", "message": "…" } ],

  "buyers": {
    "<buyer STRN>": {
      "match": ["<STRN>", "<NTN>"],      // digit-normalised lookup
      "match_names": ["Old Typed Name"], // fallback match
      "canonical_name": "…",             // exact strings the buyer's AP expects
      "canonical_address": "…",
      "canonical_ntn": "4200892-1",      // dashes preserved on purpose
      "rows": [...], "labels": {...}, "require": [...], "rules": [...]
    }
  }
}
```

Omit any key you do not need — every one is optional.

### Available row keys

`buyer_ntn`, `buyer_strn`, `buyer_province`, `status`, `fbr_invoice`, `po`,
`purchase_order`, `dn`, `dc`, `cnic`, `hs_code`, `currency`, `time_of_issue`,
`sale_type`, `delivery_date`. Add more in `fields.py::FIELD_CATALOG`.

### Available rule types

| rule                | param                    | passes when                              |
|---------------------|--------------------------|------------------------------------------|
| `present`           | –                        | value is non-empty                       |
| `matches`           | regex                    | regex matches the value                  |
| `digits_len`        | integer                  | digit count equals the param             |
| `min_tokens`        | integer                  | at least N word tokens                   |
| `contains_all`      | space-separated tokens   | all tokens present                       |
| `forbid_tokens`     | space-separated tokens   | none of the tokens present               |
| `equals_normalized` | expected id              | digit-only comparison matches            |
| `arithmetic`        | –                        | subtotal + tax == total                  |

Rule fields: `supplier_name`, `supplier_ntn`, `supplier_strn`,
`supplier_address`, `buyer_name`, `buyer_ntn`, `buyer_strn`, `buyer_address`,
`po`, `invoice_date`, `time_of_issue`, `invoice_number`, `fbr_invoice`,
`currency`.

## Previewing a change

```bash
python3 scripts/render_invoice_preview.py scripts/sample_invoice_1634.json /tmp/preview.pdf
python3 -m unittest discover -s tests -v
```

The preview renders through the same template and profile the app uses, with no
database or FBR credentials required.
