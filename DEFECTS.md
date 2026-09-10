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
`AttributeError` on the first attribute, so the billing summary never rendered. The
same fault sat in `create_ops_hub_invoice`, which meant **Create Invoice** and the
POS "bill this repair" line were dead on the same tickets.

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

**Fix.** An agreed-quote gate in `create_ops_hub_invoice`, alongside the existing
below-cost gate and following the same doctrine: refuse, and name the step that
unblocks it. If the latest **Customer Approved** estimate version is non-zero and the
bill differs by more than `quote_billing_tolerance` (default ₹1), billing is refused
with *"Raise a revised estimate and have the customer approve it before billing."*

A divergence is not an error to absorb — it is a revision nobody raised, and the
estimate-version machinery already exists to raise one.

`_latest_approved_estimate()` takes the **highest version number** among approved
versions: a revision supersedes what came before, and only the newest approval is the
current agreement. `Pending` and `Sent to Customer` are offers nobody has accepted and
do not count.

**Proved fixed.** Finds v1 @ ₹3,500 on a real ticket and matches the database; picks
v2 @ ₹2,500 over v1 @ ₹1,000 and ignores a higher `Pending` v3; returns `None` when
nothing is approved.

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

Not a code defect, and not fixed here, but it blocked spare fitment on
`erpnext.local` and the diagnosis is worth keeping.

Two Property Setter rows on `Stock Entry Detail` contradict each other:

| Field | Property | Value | Created |
|---|---|---|---|
| `t_warehouse` | `reqd` | `1` | 2026-09-01 22:59 |
| `t_warehouse` | `mandatory_depends_on` | `eval:parent.purpose != "Material Issue"` | 2026-09-09 21:39 |
| `s_warehouse` | `reqd` | `1` | 2026-09-01 22:59 |

ERPNext's `validate_warehouse` **deliberately clears** `d.t_warehouse` for a Material
Issue (`stock_entry.py:974`). Frappe's mandatory check then rejects the field ERPNext
just cleared, because `reqd = 1`.

**`mandatory_depends_on` cannot fix this.** It is client-side only —
`base_document.py:973` selects mandatory fields by `reqd == 1` and never reads it. The
form looks correct in the browser and the server still refuses.

```
383 Material Issue rows      — all 383 have no t_warehouse
180 Material Receipt rows    — all 180 have no s_warehouse
last Material Issue to post  — 2 September, the morning after the property setter appeared
```

Symptom at the counter:

```
Stock Entry Creation Failed
Could not issue MSB000001-Original x1.0 from GF-PALAVAKKAM-Sellable - GF:
[Stock Entry, GFTNMT26000014]: t_warehouse
```

**Resolution:** delete both `reqd` property setters. They are site-local data — no app
ships them, and `ch_erp15`'s fixtures carry only the `mandatory_depends_on` row.
ERPNext's own per-purpose validation is what should be enforcing this.

```python
frappe.delete_doc("Property Setter", "Stock Entry Detail-t_warehouse-reqd", force=1)
frappe.delete_doc("Property Setter", "Stock Entry Detail-s_warehouse-reqd", force=1)
frappe.clear_cache(doctype="Stock Entry Detail"); frappe.db.commit()
```

**Check production for the same two rows before go-live.**
