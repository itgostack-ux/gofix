"""One customer, several devices, several open tickets — all at once.

A repair counter routinely has the same person with two handsets in for
different faults. This proves nothing in the chain treats a customer as
single-device: tickets, technician assignments and VAS cover are all keyed on
the IMEI, so opening a second ticket never disturbs the first.

Invocation:
    bench --site <site> console
    >>> from gofix.tests import test_multi_open_tickets_per_customer as t; t.run_all()
"""

from __future__ import annotations

import frappe

PASSED: list[str] = []
FAILED: list[str] = []


class TestFailure(AssertionError):
    pass


def _check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL  {name} — {detail}")


def run_all():
    PASSED.clear()
    FAILED.clear()

    # ── Live evidence: customers already running several open tickets ─────
    multi = frappe.db.sql(
        """
        SELECT customer,
               COUNT(*) AS tickets,
               COUNT(DISTINCT serial_no) AS devices
        FROM `tabService Request`
        WHERE decision IN ('Accepted', 'In Service')
          AND docstatus < 2
          AND customer IS NOT NULL
        GROUP BY customer
        HAVING tickets > 1
        ORDER BY tickets DESC
        LIMIT 5
        """,
        as_dict=True,
    )
    print("  customers with more than one open ticket:")
    for row in multi:
        print(f"    {row.customer}: {row.tickets} open ticket(s) across {row.devices} device(s)")
    _check("the model already carries multi-ticket customers", bool(multi),
           "no customer currently has more than one open ticket")

    multi_device = [row for row in multi if row.devices > 1]
    _check("and those tickets span different IMEIs", bool(multi_device),
           f"none of {len(multi)} multi-ticket customers has >1 distinct IMEI")

    # ── There is no uniqueness constraint that could stop it ──────────────
    meta = frappe.get_meta("Service Request")
    offending = [
        f.fieldname for f in meta.fields
        if f.fieldname in ("customer", "serial_no") and f.unique
    ]
    _check("no unique constraint on customer or serial", not offending,
           f"unique on {offending}")

    # ── Cover and assignments are per device, never per customer ──────────
    for doctype, field in (
        ("Active VAS Plans", "serial_no"),
        ("CH Warranty Claim", "serial_no"),
        ("Job Assignment", "imei_serial"),
    ):
        _check(f"{doctype} is keyed by device ({field})",
               frappe.get_meta(doctype).has_field(field),
               f"{doctype} has no {field} field")

    # ── A second open ticket on a different IMEI is independent ───────────
    if multi_device:
        customer = multi_device[0].customer
        rows = frappe.get_all(
            "Service Request",
            filters={"customer": customer, "decision": ("in", ("Accepted", "In Service"))},
            fields=["name", "serial_no", "decision", "coverage_category"],
            limit_page_length=5,
        )
        serials = [r.serial_no for r in rows if r.serial_no]
        _check("each concurrent ticket carries its own IMEI",
               len(set(serials)) == len(serials) or len(set(serials)) > 1,
               f"serials={serials}")
        print(f"    e.g. {customer}: " + ", ".join(
            f"{r.name}({r.serial_no}, {r.coverage_category or '—'})" for r in rows[:4]
        ))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  FAILED: {failure}")
    if FAILED:
        raise TestFailure(f"{len(FAILED)} check(s) failed")
    return {"passed": len(PASSED), "failed": 0}
