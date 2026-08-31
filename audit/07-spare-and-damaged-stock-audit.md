# 07 — Spare & Damaged-Stock Audit (Phase 2)

**Scope:** §9 damaged-spare invoicing control, §10 spare history preservation.
**Method:** every finding was reproduced by executing the real code paths against the live
site, not by reading. Evidence is the `test_damaged_spare` suite — **33 assertions, 0 failures**.

**Requirement being audited (user, verbatim):** *"if something is damaged, still keep it as
damaged in ticket but dont show on invoice. maintain proper history tracking so we know all
spares technician used in a ticket."*

---

## How billing actually selects spares

`ServiceRequest.get_service_invoice_items()` does **not** read the ticket's `SR Spare Line`
rows. It queries `Spare Parts Usage` — the execution record — with:

```python
docstatus = 1 AND status = "Active" AND part_status IN ("Consumed", "Issued")
AND deleted = 0 AND is_defective = 0      # is_defective added by this audit
```

So there are two parallel status vocabularies, and that is where the defects live:

| | `SR Spare Line.status` (the ticket) | `Spare Parts Usage.part_status` (execution) |
|---|---|---|
| options | Pending, Reserved, Issued, Consumed, Returned, **Damaged**, Awaiting Procurement, In Transit, Delivered, Sold | Reserved, Issued, Consumed, Returned, **Defective** |

**Observation (not a defect):** `part_status = "Issued"` appears in the billing filter but is
unreachable — `Spare Parts Usage` refuses to submit unless the part is `Consumed`
(*"A spare usage can only be submitted when the part is Consumed"*), and the filter also
requires `docstatus = 1`. Harmless today; worth removing if the rule ever changes.

---

## FINDING-SPARE-001 — the ticket showed a status its own field does not offer

**Severity:** High (data integrity)
**Status:** Fixed

**Reproduction.** Mark a consumed spare damaged in the Ops Hub. Read the SR Spare Line.

**Expected.** `SR Spare Line.status == "Damaged"`.
**Actual.** `SR Spare Line.status == "Defective"` — a value **not in that field's option list**.

**Root cause.** `SparePartsUsage.sync_to_service_request()` and `_unsync_from_service_request()`
copied `part_status` straight onto the ticket row:

```python
frappe.db.set_value("SR Spare Line", ..., {"status": self.part_status, ...})
```

`frappe.db.set_value` bypasses Select validation, so the invalid value was written silently.
The ticket then displayed a status that is not one of its own options, and any filter or report
looking for `"Damaged"` missed it entirely.

This is the same class of defect as the device-condition vocabulary mismatch fixed earlier in
this session: two lists that must agree, with nothing forcing them to.

**Fix.** A single explicit mapping, `SR_LINE_STATUS_BY_PART_STATUS`, applied by both sync
paths — `Defective → Damaged`, everything else identity.
`spare_parts_usage.py`.

**Test.** *"every execution status maps to a status the TICKET field actually offers"*,
*"Defective maps to Damaged"*, and a regression guard asserting `"Defective"` is **not** a
valid ticket-line status.

---

## FINDING-SPARE-002 — a damaged spare was marked `deleted`, erasing it from the ticket's history

**Severity:** High (directly contradicts the stated requirement)
**Status:** Fixed

**Reproduction.** Consume a spare, mark it damaged, read `Service Request.total_spares_used_count`.

**Expected.** The spare still counts as a spare the technician used.
**Actual.** It did not. `SparePartsUsage.recover_spare()` set `self.deleted = 1` for **all
three** dispositions — including *Good - Back to Stock* and *Damaged by Technician*.

**Why that matters.** `deleted` is the soft-delete flag meaning *"this row was raised in
error"*. The controller already maintains the correct separation:

```python
total_count    = deleted:0                                    # every spare touched
billable_count = deleted:0 + Active + part_status IN (Consumed, Issued)
```

Setting `deleted = 1` on recovery removed the damaged part from **`total_spares_used_count`**
as well as from billing — so the ticket could no longer say what the technician had actually
used. It also hides the row from the seven other listings that filter `deleted = 0`.

**Fix.** `recover_spare()` no longer sets `deleted`. Billing already excludes the row twice
over — `part_status` (`Defective`/`Returned`) and `status` (`Moved to Dispose Stock` /
`Moved to Main Stock`) — and **every** downstream guard filters on `part_status` too, so
nothing that should exclude it stops excluding it. Verified against all ten `deleted = 0`
call sites before changing.

**Consequential fix.** `orchestration.py` billing gate 4 counted pending spare approvals with
`requires_approval = 1 AND approval_status = "Pending" AND deleted = 0`. With `deleted` no
longer set on recovery, a disposed spare carrying a stale Pending approval would have blocked
billing forever. `status: "Active"` added, matching every other gate in that file.

**Test.** *"the usage row is NOT soft-deleted"*, *"it still counts in
total_spares_used_count"*, *"but NOT in billable_spares_count"*.

---

## FINDING-SPARE-003 — billing trusted a status that is not re-validated after submit

**Severity:** Critical (revenue / customer-trust)
**Status:** Fixed

**Reproduction.** On a submitted `Spare Parts Usage`, set `is_defective = 1` while leaving
`part_status = "Consumed"`, then build the invoice.

**Expected.** A part flagged defective never reaches a customer invoice.
**Actual (before fix).** The spare was **billed to the customer.**

**Root cause.** `SparePartsUsage.validate()` carries the invariant
`if self.is_defective: self.part_status = "Defective"`. But `validate()` only runs while the
document is a draft — a submitted document takes Frappe's *update-after-submit* path, where
`validate()` is never re-run. So `is_defective` and `part_status` can drift apart on exactly
the documents that are eligible for billing, and the invoice filter was reading only
`part_status`.

**Exposure.** Lower than it first appears, and stated precisely: none of `part_status`,
`is_defective`, `status` or `deleted` are `allow_on_submit`, so an ordinary REST or Desk edit
is refused with `UpdateAfterSubmitError` (asserted by the suite). The drift is reachable only
from server-side code that sets `ignore_validate_update_after_submit` — which
`recover_spare()` and `mark_defective()` legitimately do. Those two set `part_status`
correctly today, so this was a latent hazard rather than a live leak; any future caller taking
the same bypass would have billed damaged stock.

**Fix.** Billing asserts the flag independently rather than inferring it:
`is_defective: 0` added to the invoice's `Spare Parts Usage` filter, with the reasoning
recorded at the call site. §9 requires invoice generation to *revalidate current spare status*
and *not trust the caller* — this makes that literally true.
`service_request.py`.

**Test.** *"a submitted usage's status fields cannot be edited through the normal path"*
(→ `UpdateAfterSubmitError`), *"part_status is NOT auto-corrected after submit"*, and
*"BUT the invoice still refuses it, because billing checks is_defective too"*.

---

## What now holds (all executed, not asserted)

| Requirement | Evidence |
|---|---|
| A consumed spare **is** billable | on the invoice at its sales price |
| Marking it damaged removes it from the invoice | `part_status → Defective`, `status → Moved to Dispose Stock`, invoice empty |
| The **ticket** still shows it as **Damaged** | `SR Spare Line.status == "Damaged"` |
| It stays in the ticket's history | usage row present, not soft-deleted, counted in `total_spares_used_count` |
| …but not in the billable count | `billable_spares_count == 0` |
| The reason and the person are recorded | `is_defective`, `defect_type`, `recovery_disposition = "Damaged by Technician"`, narration/remarks, `added_by_user` |
| The stock movement is traceable | `recovery_stock_entry` linked to the disposal transfer |
| Reserved / Returned / Defective never bill | invoice empty for all three |
| Draft usage never bills | excluded on `docstatus` |
| Moved-to-stock usage never bills | excluded for both Main Stock and Dispose Stock |
| Damaged stock cannot be forced back on | normal path refused; invoice refuses regardless |

---

## Still open in this area (not yet audited)

- the **forward** leg: request → approve → issue → receive by technician (§8 items 1-6)
- return-to-hub receipt and acknowledgement, and the quarantine/scrap warehouse mapping
- vendor RMA / supplier-return disposition beyond the `Faulty - Supplier Return` branch
- partial-quantity damage (one of three fitted units) — the suite covers whole-line damage only
- serialised spares: `installed_part_serial` / `removed_part_serial` round-tripping
- concurrency: a spare being marked damaged while an invoice is being built
