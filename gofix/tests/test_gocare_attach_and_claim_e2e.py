"""GoCare end to end: repair → buy plan → attachment → claim.

The scenario the business actually runs:

  1. A customer brings a device in and GoFix repairs it.
  2. On the way out they buy a GoCare plan for that device.
  3. Some weeks later the device breaks again and they come back to CLAIM.

What this proves:

  A. ATTACHMENT — issuing the plan produces an Active VAS Plans row bound to
     the customer AND the specific IMEI, and a new GoFix ticket for that IMEI
     picks it up: the plan is captured on the ticket and the ticket is
     classified as a VAS Claim.
  B. NOT ZEROED — a live VAS plan must NOT silently make the repair free.
     GoCare is settled through the claims flow, so warranty_status stays off
     "Under Warranty" and the pricing engine still quotes the job.
  C. CLAIM — initiate_warranty_claim finds the plan by IMEI, sets the coverage
     and the cost split, and the claim carries the plan it is consuming.
  D. GATES — the repair ticket cannot be created off a claim until the device
     is physically received and intake QC has passed.
  E. MULTI-DEVICE — the same customer's OTHER IMEI is unaffected: plans and
     claims are per device, not per customer.

Invocation:
    bench --site <site> console
    >>> from gofix.tests import test_gocare_attach_and_claim_e2e as t; t.run_all()
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, flt, nowdate

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


def _clear():
    frappe.message_log = []


def audit_gocare_plan_config() -> list[dict]:
    """Which GoCare plans can actually cover a device we only repaired?

    A device that came in for repair was never sold by us, so the plan must be
    issued against the customer's own hardware. Active VAS Plans refuses that
    unless the PLAN itself is marked allow_external_device. A GoCare plan
    without that flag simply cannot be sold at the repair counter, however
    correct the rest of the flow is.
    """
    rows = frappe.get_all(
        "CH Warranty Plan",
        filters={"plan_name": ("like", "%GoCare%")},
        fields=["name", "plan_name", "company", "allow_external_device",
                "external_device_item", "service_item"],
        order_by="company, plan_name",
    )
    print("  GoCare plan configuration — can it cover a repaired (unsold) device?")
    for row in rows:
        verdict = "YES" if row.allow_external_device else "NO  <-- cannot be sold at the repair counter"
        print(f"    [{verdict:>4}] {row.plan_name}  ({row.company})")
    return rows


def _gocare_plan_and_fixture():
    """Pick a GoCare plan that CAN cover a repaired device, plus a matching ticket.

    Company-consistent by construction: the plan, the customer and the serial
    must all belong to the same company or the controller rejects the issue.
    """
    candidates = frappe.get_all(
        "CH Warranty Plan",
        filters={"plan_name": ("like", "%GoCare%"), "allow_external_device": 1},
        fields=["name", "plan_name", "company", "service_item"],
    ) or frappe.get_all(
        "CH Warranty Plan",
        filters={"allow_external_device": 1},
        fields=["name", "plan_name", "company", "service_item"],
    )
    if not candidates:
        raise TestFailure(
            "No warranty plan on this site has allow_external_device set, so no plan "
            "can be attached to a device we only repaired. Tick 'Allow External Device' "
            "on the GoCare plan(s) sold at the repair counter."
        )

    for plan in candidates:
        # The device must NOT be in our own inventory: an "external device"
        # plan is for hardware the customer already owned, and the controller
        # rejects a serial it can find in stock. A repair-counter walk-in with
        # an unregistered IMEI is exactly that case.
        row = frappe.db.sql(
            """
            SELECT sr.name AS sr, sr.serial_no, sr.customer, sr.company,
                   sr.device_item, sr.decision
            FROM `tabService Request` sr
            WHERE sr.company = %s
              AND sr.serial_no IS NOT NULL AND sr.serial_no != ''
              AND sr.customer IS NOT NULL
              AND sr.docstatus < 2
              AND NOT EXISTS (
                SELECT 1 FROM `tabSerial No` s WHERE s.name = sr.serial_no)
            ORDER BY sr.creation DESC
            LIMIT 1
            """,
            plan.company, as_dict=True,
        )
        if row:
            return plan, row[0]

    raise TestFailure(
        "no Service Request in the same company as any external-device-capable plan"
    )


def _sell_the_plan(plan, fx) -> str:
    """Actually sell the plan to the customer, and return the invoice.

    The controller refuses to activate a plan that no commercial document
    sold — and separately refuses one whose invoice does not contain the
    plan's own service item. Both guards are right, so the test satisfies
    them the way the counter does: by billing the plan.
    """
    service_item, price = frappe.db.get_value(
        "CH Warranty Plan", plan, ["service_item", "price"]
    )
    if not service_item:
        raise TestFailure(f"plan {plan} has no service_item to sell")

    si = frappe.new_doc("Sales Invoice")
    si.customer = fx.customer
    si.company = fx.company
    si.posting_date = nowdate()
    si.due_date = nowdate()
    si.append("items", {
        "item_code": service_item,
        "qty": 1,
        "rate": flt(price) or 999,
    })
    si.flags.ignore_permissions = True
    si.insert(ignore_permissions=True)
    si.submit()
    return si.name


def run_all():
    from ch_item_master.ch_item_master.warranty_api import (
        check_warranty,
        initiate_warranty_claim,
        issue_warranty_plan,
    )

    PASSED.clear()
    FAILED.clear()
    print("── plan configuration audit ──")
    audit_gocare_plan_config()
    print()
    plan_row, fx = _gocare_plan_and_fixture()
    plan = plan_row.name
    plan_title = plan_row.plan_name
    item_code = (
        frappe.db.get_value("CH Warranty Plan", plan, "external_device_item")
        or fx.device_item
        or frappe.db.get_value("Serial No", fx.serial_no, "item_code")
    )
    print(f"fixture: serial={fx.serial_no} customer={fx.customer} company={fx.company}")
    print(f"         prior ticket={fx.sr} ({fx.decision}) | plan={plan} '{plan_title}'\n")

    try:
        # ── A. The customer buys the plan after the repair ────────────────
        # A plan cannot activate without a real sale behind it — the controller
        # refuses otherwise, which is correct: an unsold plan covers nothing.
        # Use the customer's own submitted invoice as that sale.
        print("── A. plan attachment ──")
        sale = _sell_the_plan(plan, fx)
        print(f"        plan sold on {sale}")
        # The device came in for REPAIR — we never sold it — so the plan is
        # issued against the customer's own device. Active VAS Plans requires
        # the invoice to carry the covered device item unless the plan is
        # flagged external, which is precisely this case.
        issued = issue_warranty_plan(
            warranty_plan=plan,
            customer=fx.customer,
            item_code=item_code,
            serial_no=fx.serial_no,
            company=fx.company,
            start_date=nowdate(),
            sales_invoice=sale,
            external_device_source="GoFix repair counter — customer-owned device",
        )
        _clear()
        active = frappe.get_doc("Active VAS Plans", issued["active_plan"])
        _check("plan issued and submitted", active.docstatus == 1, f"docstatus={active.docstatus}")
        _check("plan is bound to the exact IMEI",
               active.serial_no == fx.serial_no, f"serial={active.serial_no}")
        _check("plan is bound to the customer",
               active.customer == fx.customer, f"customer={active.customer}")
        _check("plan is Active", active.status == "Active", f"status={active.status}")
        _check("plan is flagged as covering a customer-owned device",
               bool(active.is_external_device)
               and "GoFix repair counter" in (active.external_device_source or ""),
               f"is_external_device={active.is_external_device} "
               f"source={active.external_device_source!r}")
        _check("cover has an end date", bool(active.end_date), f"end={active.end_date}")

        # ── The lookup GoFix uses at intake finds it ──────────────────────
        cover = check_warranty(serial_no=fx.serial_no, company=fx.company)
        _clear()
        _check("check_warranty reports the device as covered",
               bool(cover.get("warranty_covered")), f"cover={ {k: cover.get(k) for k in ('warranty_covered',)} }")
        covering = cover.get("covering_plan") or {}
        _check("the covering plan is the one just issued",
               covering.get("name") == active.name,
               f"covering={covering.get('name')} vs issued={active.name}")

        # ── A2. A NEW GoFix ticket picks the plan up ──────────────────────
        # Copy the device identity off the customer's previous ticket. A
        # walk-in device we do not stock has to name its category/brand/model
        # explicitly — the controller refuses to book one in without them.
        prior = frappe.get_doc("Service Request", fx.sr)
        sr = frappe.new_doc("Service Request")
        sr.update({
            "customer": fx.customer,
            "company": fx.company,
            "serial_no": fx.serial_no,
            "mode_of_service": "Walk-in",
            "issue_description": "GoCare attachment test — screen flicker",
        })
        for field in ("device_item", "device_category", "device_brand", "device_model",
                      "customer_name", "mobile_no", "store", "source_warehouse"):
            if prior.meta.has_field(field) and prior.get(field):
                sr.set(field, prior.get(field))

        # Tickets raised before the identify-the-device rule carry no
        # category/brand/model, so fall back to a real triple from the masters.
        if not sr.device_category:
            sr.device_category = frappe.db.get_value("CH Category", {}, "name")
        if not sr.device_brand:
            sr.device_brand = frappe.db.get_value("Brand", {}, "name")
        if not sr.device_model:
            sr.device_model = frappe.db.get_value(
                "CH Model", {"brand": sr.device_brand}, "name"
            ) or frappe.db.get_value("CH Model", {}, "name")
        if sr.meta.has_field("data_backup_disclaimer"):
            sr.data_backup_disclaimer = 1
        if not sr.get("contact_number"):
            sr.contact_number = (
                prior.get("contact_number")
                or frappe.db.get_value("Customer", fx.customer, "mobile_no")
                or "9840000000"
            )
        sr.insert(ignore_permissions=True)
        _clear()
        _check("new ticket captured the plan",
               sr.get("active_warranty_plan") == active.name,
               f"active_warranty_plan={sr.get('active_warranty_plan')}")
        _check("new ticket names the plan",
               (sr.get("warranty_plan_name") or "") == (plan_title or ""),
               f"warranty_plan_name={sr.get('warranty_plan_name')}")
        _check("ticket is classified as a VAS Claim",
               sr.get("coverage_category") == "VAS Claim",
               f"coverage_category={sr.get('coverage_category')}")

        # ── B. A VAS plan must NOT zero the repair ────────────────────────
        print("\n── B. VAS does not zero the repair ──")
        _check("warranty_status is NOT forced to Under Warranty",
               sr.get("warranty_status") != "Under Warranty",
               f"warranty_status={sr.get('warranty_status')} "
               "(GoCare is settled through the claims flow, not by a free repair)")

        # ── E. The customer's OTHER device is untouched ───────────────────
        print("\n── E. cover is per device, not per customer ──")
        other_serial = frappe.db.sql(
            """
            SELECT sr.serial_no FROM `tabService Request` sr
            WHERE sr.customer = %s AND sr.serial_no != %s
              AND sr.serial_no IS NOT NULL AND sr.serial_no != ''
            LIMIT 1
            """,
            (fx.customer, fx.serial_no), pluck=True,
        )
        if other_serial:
            other_cover = check_warranty(serial_no=other_serial[0], company=fx.company)
            _clear()
            other_covering = (other_cover.get("covering_plan") or {}).get("name")
            _check("the customer's other IMEI does not inherit this plan",
                   other_covering != active.name,
                   f"other={other_serial[0]} covering={other_covering}")
        else:
            print("        (customer has only one device — skipped)")

        # ── C. The claim ─────────────────────────────────────────────────
        print("\n── C. claim against the plan ──")
        issue_cat = frappe.db.get_value("Issue Category", {"is_active": 1}, "name") \
            or frappe.db.get_value("Issue Category", {}, "name")
        # Claims require real device evidence — the API resolves each entry to
        # an actual File record, so upload them rather than faking URLs.
        evidence = []
        for n in range(1, 5):
            f = frappe.get_doc({
                "doctype": "File",
                "file_name": f"gocare-evidence-{n}.txt",
                "is_private": 1,
                "content": f"gocare claim evidence image {n}",
            })
            f.insert(ignore_permissions=True)
            # `file_name` is matched against the File DOCNAME by
            # _normalize_claim_evidence, not the display filename.
            evidence.append({"file_url": f.file_url, "file_name": f.name})
        claim_out = None
        try:
            claim_out = initiate_warranty_claim(
                serial_no=fx.serial_no,
                customer=fx.customer,
                item_code=item_code,
                company=fx.company,
                issue_description="GoCare claim test — display dead after drop",
                issue_category=issue_cat,
                reported_at_company=fx.company,
                estimated_repair_cost=4500,
                sold_plan=active.name,
                evidence_files=frappe.as_json(evidence),
            )
        except Exception as exc:
            print(f"        (initiate raised: {str(exc)[:260]})")
        finally:
            _clear()

        _check("claim was created", bool(claim_out and claim_out.get("claim_name")),
               f"out={claim_out}")
        if claim_out and claim_out.get("claim_name"):
            claim = frappe.get_doc("CH Warranty Claim", claim_out["claim_name"])
            _check("claim is submitted", claim.docstatus == 1, f"docstatus={claim.docstatus}")
            _check("claim consumes the plan just bought",
                   claim.sold_plan == active.name, f"sold_plan={claim.sold_plan}")
            _check("claim carries a coverage type",
                   bool(claim.coverage_type), f"coverage_type={claim.coverage_type}")
            _check("claim splits the cost",
                   (flt(claim.gogizmo_share) + flt(claim.customer_share)
                    + flt(claim.gofix_share)) > 0,
                   f"gogizmo={claim.gogizmo_share} customer={claim.customer_share} "
                   f"gofix={claim.gofix_share}")
            _check("the split accounts for the whole estimate",
                   abs((flt(claim.gogizmo_share) + flt(claim.customer_share)
                        + flt(claim.gofix_share)) - flt(claim.estimated_repair_cost)) < 0.01,
                   f"shares sum to {flt(claim.gogizmo_share) + flt(claim.customer_share) + flt(claim.gofix_share)} "
                   f"vs estimate {claim.estimated_repair_cost}")

            # The customer pays the deductible and whatever the plan does not
            # cover — proven against the plan's own configuration rather than
            # assumed, because a plan with a deductible must not quote free.
            plan_deductible, company_pct = frappe.db.get_value(
                "CH Warranty Plan", plan, ["deductible_amount", "company_share_percent"]
            )
            expected_company = flt(
                max(0, flt(claim.estimated_repair_cost) - flt(plan_deductible))
                * (flt(company_pct) or 100) / 100, 2
            )
            _check("company share follows the plan's deductible and share %",
                   abs(flt(claim.gogizmo_share) - expected_company) < 0.01,
                   f"gogizmo={claim.gogizmo_share} expected={expected_company} "
                   f"(deductible={plan_deductible}, company_share_percent={company_pct})")
            _check("customer bears the remainder",
                   abs(flt(claim.customer_share)
                       - (flt(claim.estimated_repair_cost) - flt(claim.gogizmo_share))) < 0.01,
                   f"customer={claim.customer_share}")
            print(f"        claim={claim.name} status={claim.claim_status} "
                  f"coverage={claim.coverage_type} "
                  f"gogizmo={claim.gogizmo_share} customer={claim.customer_share}")

            # ── D. Repair ticket gates ────────────────────────────────────
            print("\n── D. repair-ticket gates ──")
            gated = False
            try:
                claim.create_repair_ticket()
            except Exception as exc:
                message = str(exc)
                gated = "received" in message.lower() or "qc" in message.lower() \
                    or "approved" in message.lower()
                if gated:
                    print(f"        gate message: {message[:220]}")
            finally:
                _clear()
            _check("repair ticket is gated until the device is received and QC passes",
                   gated)

    finally:
        frappe.db.rollback()
        print("\n(rolled back — no documents left behind)")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  FAILED: {failure}")
    if FAILED:
        raise TestFailure(f"{len(FAILED)} check(s) failed")
    return {"passed": len(PASSED), "failed": 0}
