# GoFix defect record

Defects found by the pre-go-live verification suite, what was done about them, and
how each was proved fixed. Newest first.

Re-runnable evidence:

```bash
# server tier — 107 scenarios
bench --site erpnext.local console
>>> from gofix.tests import test_golive_lifecycle as T; T.run_all()

# browser tier — 32 scenarios, screenshots per scenario
env/bin/python <playwright surface suite>
```

---

## GF-001 — The Ops Hub billing summary crashed on every ticket with service lines

**Status:** Fixed 10 Sep 2026 · **Severity:** Blocker · **Found by:** browser tier (U9.1)

### What happened

`get_invoice_summary` read three fields off `Service Request Service Item` that the
doctype does not have. Any submitted ticket carrying a service line raised
`AttributeError` on the first attribute, so the billing summary never rendered.

> **Correction.** An earlier version of this record said the same fault also killed
> **Create Invoice**. It does not. `create_ops_hub_invoice` is refused at the top —
> *"Repairs are billed at the POS counter, not from the Ops Hub"* — so execution never
> reaches the bad field read. That second call site was **latent, not live**. It is
> fixed here anyway, because the function is still reachable by name and the read
> would fail the moment the refusal were lifted.

```
21 of 21 tickets with service lines CRASH (21 of 67 submitted)
AttributeError: 'ServiceRequestServiceItem' object has no attribute 'service_item_name'

reads   service_item_name   rate            amount
has     item_name           estimated_cost  actual_cost
```

The second call site looked defended — `row.service_item_name or row.item_name` — but
that fallback could never run: reading the missing attribute raises before `or` is
evaluated. Both sites were equally dead.

### Fix

`gofix_services/page/gofix_ops_hub/gofix_ops_hub.py` — both call sites now read
`item_name`, and take the rate from `actual_cost or estimated_cost` (actual wins once
the work is done, the estimate stands in until then).

Checked before choosing that mapping: on every existing row `estimated_cost ==
actual_cost`, and the rows are VAS plan sales, so these are customer prices and not
cost-to-company. Billing from them does not bill at cost.

### Proved fixed

* 21 of 21 previously-crashing tickets return a summary; **0 crashes**.
* Lines carry a name and a non-zero rate (`Apple Care Protect+ 1 Year`, ₹2,000).
* Browser scenario U9.1 went **HTTP 500 → HTTP 200**.
* Audited every other reader of `service_items`: one more site, and it only reads
  `service_item`, which is a real field.

---

## GF-002 — A quote of ₹0, and a bill that did not match the quote

**Status:** Fixed 10 Sep 2026 · **Severity:** Blocker · **Found by:** server verification

Two separate defects reported as one. They are recorded separately because the fixes
are unrelated.

### GF-002a — an estimate of nothing was recorded as a quote

`create_estimate_version` had no lower bound. When nothing on the ticket had a price —
no repair chosen, or no `GoFix Pricing Rule` matched — it wrote a ₹0 version, set
`estimated_cost = 0`, marked approval pending and paused the repair. The customer then
approved nothing at all.

```
Estimate Version, by status:
  Customer Approved   11 rows   10 of them at Rs 0
  Customer Rejected   16 rows   16 of them at Rs 0
```

**Fix.** `gofix_services/orchestration.py` refuses to create a version whose total is
zero — **except** when `warranty_ctx["covered"]` is true. That exception matters: a
rework inside its own workmanship warranty is legitimately ₹0, and rejecting it would
have broken the warranty path to fix the reporting one.

**Proved fixed.** 3 of 3 unpriced tickets refused with *"There is nothing to quote yet"*;
3 of 3 priced tickets still create versions normally (₹2,990 / ₹2,300 / ₹920) — no
false positives.

### GF-002b — billing did not honour the approved quote

The estimate was snapshotted at approval and then not consulted. Billing re-derived
its own total, so the customer agreed to one number and was billed another, with
nothing in the system objecting.

```
8 of 25 confirmed tickets re-price against today's card
  SR-260818-10545   agreed 3,500   card   200
  SR-260818-10550   agreed 8,900   card   920
  SR-260818-10554   agreed 6,000   card 3,440
```

**Fix.** `assert_bill_matches_agreed_quote()` in
`gofix_services/doctype/service_request/service_request.py`, called from
`ServiceRequest.create_service_invoice` once the invoice lines are known. If the latest
**Customer Approved** estimate version is non-zero and the bill differs by more than
`quote_billing_tolerance` (default ₹1), billing is refused with *"Raise a revised
estimate and have the customer approve it before billing."*

A divergence is not an error to absorb — it is a revision nobody raised, and the
estimate-version machinery already exists to raise one.

`latest_approved_estimate()` takes the **highest version number** among approved
versions: a revision supersedes what came before, and only the newest approval is the
current agreement. `Pending` and `Sent to Customer` are offers nobody has accepted and
do not count.

> **The gate was first written in the wrong place.** It went into
> `create_ops_hub_invoice`, which is refused at its first statement — so the gate sat in
> unreachable code and enforced nothing. It has been moved to
> `create_service_invoice`, which is the live path, and the test below asserts that
> placement rather than trusting it.

**Proved fixed.** Eight behaviours, all as intended:

| Case | Result |
|---|---|
| Bill equals the approved ₹2,000 | allowed |
| Bill of ₹920 against an approved ₹2,000 | **refused** |
| ₹2,000.50 against ₹2,000 (inside ₹1 tolerance) | allowed |
| ₹2,500 less a ₹500 discount | allowed — the discount counts |
| Two lines summing to ₹2,000 | allowed |
| No approved estimate at all | allowed |
| Warranty rework approved at ₹0 | allowed |
| Newest approval wins over an older one and a higher `Pending` | v2 @ ₹2,500 |

Placement asserted by source inspection: present in `create_service_invoice`, absent
from `create_ops_hub_invoice`.

### Still open on GF-002b

**The POS counter path is not covered.** Repairs are billed through the POS cart
(`ch_pos/api/pos_api.py`, which stamps `custom_gofix_service_request` on the invoice),
and that path does not call `create_service_invoice`. The gate therefore protects the
Service Request form route only. Extending it to the cart is the remaining work, and
until it is done a repair billed at the counter can still diverge from its approved
quote.

---

## Regression check for GF-001 and GF-002

| | Result |
|---|---|
| Server tier | 93 pass / 4 fail / 10 blocked — **unchanged** (the 4 fails are store master data) |
| Browser tier | **32 pass / 0 fail**, up from 31/1 |
| Lint | 11 pre-existing findings in `gofix_ops_hub.py`, **no new ones** |

---

## Not code — site configuration worth recording

### Every server-created Material Issue and Material Receipt failed for 8 days

Site configuration, not app code, but it blocked spare fitment on `erpnext.local`.
**Fixed 11 September 2026** — see Resolution.

Two Property Setter rows on `Stock Entry Detail` contradicted each other:

| Field | Property | Value | Created |
|---|---|---|---|
| `t_warehouse` | `reqd` | `1` | 2026-09-01 22:59 |
| `s_warehouse` | `reqd` | `1` | 2026-09-01 22:59 |
| `t_warehouse` | `mandatory_depends_on` | `eval:parent.purpose != "Material Issue"` | 2026-09-02 23:32 |

ERPNext's `validate_warehouse` **deliberately clears** `d.t_warehouse` for a Material
Issue (`stock_entry.py:974`), and symmetrically clears `d.s_warehouse` for a Material
Receipt (`stock_entry.py:978`). Frappe's mandatory check then rejects the field ERPNext
just cleared, because `reqd = 1`. The setter demands a value ERPNext guarantees is blank.

**`mandatory_depends_on` cannot fix this.** It is client-side only —
`base_document.py:972` selects mandatory fields by `reqd == 1` and never reads it. The
form looks correct in the browser and the server still refuses.

```
383 Material Issue rows      — all 383 have no t_warehouse
180 Material Receipt rows    — all 180 have no s_warehouse
last Stock Entry to post     — 2 September, the morning after the property setters appeared
```

Symptom at the counter:

```
Stock Entry Creation Failed
Could not issue MSB000001-Original x1.0 from GF-PALAVAKKAM-Sellable - GF:
[Stock Entry, GFTNMT26000014]: t_warehouse
```

**Provenance — the two rows do not have the same origin.** An earlier revision of this
note said both were site-local and that no app shipped them. That is wrong for
`t_warehouse`, and it is why deleting the row did not make the problem go away:

| Row | Origin | Deleting it is |
|---|---|---|
| `t_warehouse-reqd` | **shipped** in `ch_erp15/ch_erp15/custom/stock_entry_detail.json`, added by `5b52099` (1 Sep, "Stock Entry Mandatory Feilds and do validations") | temporary — `migrate.py:180` calls `sync_customizations()`, which re-creates it. The DB row's `modified` of 9 Sep 21:39 is exactly that resurrection. |
| `s_warehouse-reqd` | site-local, no app file | permanent |

This is the `custom/*.json` channel described in `CLAUDE.md` — a Customize Form snapshot
re-applied on every migrate, which will resurrect what you delete.

**Resolution (applied 11 Sep 2026):** the fix is two parts, and the first is the one that
makes it stick.

1. Remove the `Stock Entry Detail-t_warehouse-reqd` object from
   `ch_erp15/ch_erp15/custom/stock_entry_detail.json`, so migrate stops re-applying it.
2. Delete both live rows and clear the doctype cache:

```python
frappe.delete_doc("Property Setter", "Stock Entry Detail-t_warehouse-reqd", force=1)
frappe.delete_doc("Property Setter", "Stock Entry Detail-s_warehouse-reqd", force=1)
frappe.clear_cache(doctype="Stock Entry Detail"); frappe.db.commit()
```

No replacement validation is needed: ERPNext's `validate_warehouse` already enforces the
source/target rule per purpose and throws `Source warehouse is mandatory for row 1`.

Verified after the fix: Material Issue, Material Receipt and Material Transfer all clear
the mandatory check, re-running `sync_customizations_for_doctype` on the edited file no
longer re-creates the row, and the native guard still fires.

**Check production for the same two rows before go-live**, and remove the snapshot entry
there too — deleting the row alone will not survive the next migrate.
