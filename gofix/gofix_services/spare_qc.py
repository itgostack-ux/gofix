# Copyright (c) 2026, GoStack and contributors

"""Incoming inspection for spares raised against a repair.

A spare reached the bench with nothing standing between "it arrived" and "it
is inside the customer's phone". A part that was dead on arrival, damaged in
transit or simply the wrong one was only discovered when a technician tried
to fit it, and by then the ticket had already been promised a turnaround.

Every spare on a job is now inspected before it can be fitted:

    requested -> Reserved / Awaiting Procurement -> Pending QC
                                                      |
                              Passed  ----------------+---------------  Failed
                                 |                                        |
                          technician may fit                   moved to the store's
                                                               Damaged bin, line closed,
                                                               replacement may be raised

Two rules decide what this module will and will not do:

**Fitting is gated on the QC result, not on who recorded it.** The person who
will fit the part is the one who inspects it, so the gate cannot be a
segregation-of-duties control; it is a "look at it before you open the phone"
control. ``assert_fit_allowed`` is the single authority and both the server
and every screen ask it, so a hidden button and a refused click are the same
rule -- the same shape ``gofix_services.actions`` uses for repair buttons.

**Damage is attributed by stage, not by person.** A part that never passed QC
cannot have been broken by the technician fitting it, because it was never
fitted. So "damaged at QC" and "damaged by technician" are decided by *when*
the damage was found, which is a fact in the log, rather than by asking
someone to pick a category about their own work. ``QC Failed`` is the former;
``Damaged by Technician`` on the Spare Parts Usage recovery is the latter.

Nothing here writes a spare's history as a field on the line. Lines only ever
show their current state, so a part that failed QC and was replaced would
leave no trace once its replacement was fitted. Every step appends a
``GoFix Spare Event Log`` row instead -- durable, append-only, one writer.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

# Why a part failed incoming inspection. Deliberately excludes anything that
# can only happen while fitting -- a part damaged on the bench passed QC, so it
# is a Spare Parts Usage recovery, not a QC defect. Keeping the two vocabularies
# apart is what lets the register split "arrived broken" from "we broke it".
QC_DEFECT_TYPES = (
    "DOA (Dead on Arrival)",
    "Manufacture Defect",
    "Transit Damage",
    "Wrong Spare",
    "Other",
)

QC_PENDING = "Pending QC"
QC_PASSED = "Passed"
QC_FAILED = "Failed"

# A line in one of these has nothing to inspect yet: it has not arrived.
_NOT_YET_RECEIVED = ("Awaiting Procurement", "Pending")

# A line in one of these is finished with; re-inspecting it changes nothing.
_CLOSED_STATUSES = ("Consumed", "Returned", "Damaged")


def log_event(
    service_request,
    event_type,
    spare_line=None,
    spare_item=None,
    item_name=None,
    qty=None,
    serial_no=None,
    defect_type=None,
    from_warehouse=None,
    to_warehouse=None,
    stock_entry=None,
    spare_usage=None,
    replaces_line=None,
    remarks=None,
    company=None,
    actor=None,
):
    """Append one row to the spare's history.

    The only writer. Everything that happens to a spare goes through here so
    the rows have one shape and one meaning, and so a caller cannot invent an
    event type the register does not know about.

    Never raises into its caller. A history row failing to save must not roll
    back the stock movement or the QC decision it is describing -- losing the
    note is bad, losing the transfer is worse -- so a failure is logged for
    someone to find and the caller carries on.
    """
    try:
        if not company and service_request:
            company = frappe.db.get_value("Service Request", service_request, "company")
        doc = frappe.new_doc("GoFix Spare Event Log")
        doc.update({
            "service_request": service_request,
            "spare_line": spare_line,
            "company": company,
            "event_type": event_type,
            "event_time": now_datetime(),
            "actor": actor or frappe.session.user,
            "spare_item": spare_item,
            "item_name": item_name,
            "qty": flt(qty) if qty is not None else None,
            "serial_no": serial_no,
            "defect_type": defect_type,
            "from_warehouse": from_warehouse,
            "to_warehouse": to_warehouse,
            "stock_entry": stock_entry,
            "spare_usage": spare_usage,
            "replaces_line": replaces_line,
            "remarks": remarks,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc.name
    except Exception:
        frappe.log_error(
            title="GoFix spare event log write failed",
            message=frappe.get_traceback(),
        )
        return None


def qc_state(row) -> str:
    """The QC status of a line, treating an unset value as pending.

    Lines created before this shipped have no value at all, and an empty
    string must mean "not inspected yet" rather than silently passing the
    gate below.
    """
    return (row.get("qc_status") if hasattr(row, "get") else getattr(row, "qc_status", None)) or QC_PENDING


def fit_permission(row) -> dict:
    """May this spare be fitted? ``{allowed, reason}``.

    One authority, asked by the server gate and by every screen, so a hidden
    button and a refused click always agree.
    """
    status = (row.get("status") if hasattr(row, "get") else getattr(row, "status", None)) or ""
    qc = qc_state(row)

    if status in _NOT_YET_RECEIVED:
        return {"allowed": False, "reason": _("This spare has not arrived yet.")}
    if qc == QC_FAILED:
        return {"allowed": False,
                "reason": _("This spare failed QC. Raise a replacement instead.")}
    if qc != QC_PASSED:
        return {"allowed": False,
                "reason": _("This spare has not passed QC yet. Inspect it before fitting it.")}
    return {"allowed": True, "reason": None}


def assert_fit_allowed(row) -> None:
    """Refuse to fit a spare the rule above will not allow."""
    verdict = fit_permission(row)
    if not verdict["allowed"]:
        frappe.throw(verdict["reason"], title=_("Spare Not Cleared"))


def _damaged_bin(company: str, source_wh: str | None) -> str:
    """The store's own Damaged bin for the warehouse the part came from.

    Deliberately does NOT fall back to the company's central damaged warehouse.
    That warehouse is at the hub, and this bench forbids a direct Material
    Transfer between locations -- ``enforce_no_direct_material_transfer_submit``
    requires the in-transit flow so stock cannot jump source -> store without
    transit visibility. Booking a part straight into the hub's damaged stock
    would claim it has already travelled while it is still in a tray on the
    bench, and if it never arrived nothing would reveal that.

    So a failed part stops at its own store. Getting it from there to the hub
    is a real movement on a manifest -- the audit-verified damaged-stock
    transfer -- not something this function should do behind anyone's back.

    Never the source bin: that is where sellable stock lives, and leaving a
    part that just failed inspection there puts it back on the shelf for the
    next repair.
    """
    group = frappe.db.get_value("Warehouse", source_wh, "parent_warehouse") if source_wh else None
    if group:
        store_bin = frappe.db.get_value(
            "Warehouse",
            {"parent_warehouse": group, "is_group": 0, "disabled": 0,
             "name": ("like", "%-Damaged - %")},
            "name",
        )
        if store_bin:
            return store_bin

    frappe.throw(
        _("{0} has no Damaged bin alongside it, so there is nowhere to quarantine "
          "this part without moving it to another location. Leaving it in {0} would "
          "put a part that just failed inspection back on the shelf. Add a Damaged "
          "bin for this store.").format(source_wh or _("the source warehouse")),
        title=_("Damaged Bin Not Configured"),
    )


def _quarantine_stock(sr, row, company: str) -> str | None:
    """Move the failed part out of sellable stock and into the Damaged bin.

    A Material Transfer, not a receipt: the unit already exists in the ledger
    and is changing bin, not appearing. Returns the Stock Entry name.

    A non-stock or zero-qty line has nothing to move; the QC verdict is still
    recorded, because the decision is what matters and the ledger simply has
    nothing to say about it.
    """
    source_wh = row.get("warehouse")
    qty = flt(row.get("qty"))
    if not source_wh or qty <= 0:
        return None
    if not frappe.db.get_value("Item", row.get("spare_item"), "is_stock_item"):
        return None

    target = _damaged_bin(company, source_wh)
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.company = company
    se.remarks = _("Failed spare QC on {0}: {1}").format(
        sr.name, row.get("qc_defect_type") or _("no defect type given"))
    se.append("items", {
        "item_code": row.get("spare_item"),
        "qty": qty,
        "uom": row.get("uom"),
        "s_warehouse": source_wh,
        "t_warehouse": target,
    })
    frappe.has_permission("Stock Entry", "create", throw=True)
    # The role gate above authorises the human. The warehouses are server-chosen
    # policy, not user input, so the document itself is inserted unscoped.
    se.flags.ignore_permissions = True
    se.flags.ignore_write_scope = True
    # Source and target are two bins of the SAME store -- _damaged_bin only ever
    # returns a sibling under the same parent warehouse, and refuses otherwise.
    # Nothing travels, so there is no transit to make visible and the in-transit
    # guard does not apply. Asserted rather than assumed: if the bins ever stop
    # being siblings this must fail loudly, not quietly move stock between
    # locations behind the logistics flow's back.
    if frappe.db.get_value("Warehouse", source_wh, "parent_warehouse") != frappe.db.get_value(
        "Warehouse", target, "parent_warehouse"
    ):
        frappe.throw(
            _("{0} and {1} are at different locations. Moving stock between locations "
              "goes on a manifest through logistics, not a direct transfer.").format(
                source_wh, target),
            title=_("Use In-Transit Transfer"),
        )
    se.flags.ignore_procurement_guardrails = True
    se.insert(ignore_permissions=True)
    se.submit()
    return se.name


def record_qc(sr, spare_row_name: str, result: str, defect_type=None, remarks=None) -> dict:
    """Pass or fail one spare line's incoming inspection.

    On a pass the line becomes fittable and nothing moves.

    On a fail the line is closed as Damaged, the part is transferred into the
    store's Damaged bin, any reservation it held is released, and a
    replacement may be raised against the same job.
    """
    if result not in (QC_PASSED, QC_FAILED):
        frappe.throw(_("QC result must be {0} or {1}.").format(QC_PASSED, QC_FAILED))

    row = None
    for line in sr.get("spare_lines") or []:
        if line.name == spare_row_name:
            row = line
            break
    if row is None:
        frappe.throw(
            _("{0} is not a spare on Service Request {1}.").format(spare_row_name, sr.name),
            frappe.PermissionError,
        )

    if qc_state(row) != QC_PENDING:
        frappe.throw(
            _("This spare was already inspected ({0}) by {1}. Raise a replacement "
              "rather than re-inspecting it.").format(qc_state(row), row.qc_by or _("someone")),
            title=_("Already Inspected"),
        )
    if (row.status or "") in _NOT_YET_RECEIVED:
        frappe.throw(
            _("This spare has not arrived yet, so there is nothing to inspect."),
            title=_("Not Received"),
        )
    if (row.status or "") in _CLOSED_STATUSES:
        frappe.throw(
            _("This spare is already {0} and cannot be inspected.").format(row.status),
            title=_("Line Closed"),
        )

    if result == QC_FAILED:
        if not defect_type or defect_type not in QC_DEFECT_TYPES:
            frappe.throw(
                _("Say why it failed. One of: {0}.").format(", ".join(QC_DEFECT_TYPES)),
                title=_("Defect Type Required"),
            )
        if not (remarks or "").strip():
            frappe.throw(
                _("A failed spare is written off against the store, so the reason has "
                  "to be recorded."),
                title=_("Remarks Required"),
            )

    company = sr.get("company")
    now = now_datetime()
    row.qc_status = result
    row.qc_by = frappe.session.user
    row.qc_on = now
    row.qc_defect_type = defect_type if result == QC_FAILED else None
    row.qc_remarks = (remarks or "").strip() or None

    stock_entry = None
    if result == QC_FAILED:
        stock_entry = _quarantine_stock(sr, row, company)
        source_wh = row.get("warehouse")
        row.status = "Damaged"
        sr.flags.ignore_validate_update_after_submit = True
        sr.save(ignore_permissions=True)
        log_event(
            sr.name, "QC Failed", spare_line=row.name, spare_item=row.spare_item,
            item_name=row.item_name, qty=row.qty, defect_type=defect_type,
            from_warehouse=source_wh, remarks=remarks, company=company,
        )
        if stock_entry:
            log_event(
                sr.name, "Moved to Damaged Bin", spare_line=row.name,
                spare_item=row.spare_item, item_name=row.item_name, qty=row.qty,
                defect_type=defect_type, from_warehouse=source_wh,
                to_warehouse=frappe.db.get_value(
                    "Stock Entry Detail", {"parent": stock_entry}, "t_warehouse"),
                stock_entry=stock_entry, company=company,
            )
    else:
        sr.flags.ignore_validate_update_after_submit = True
        sr.save(ignore_permissions=True)
        log_event(
            sr.name, "QC Passed", spare_line=row.name, spare_item=row.spare_item,
            item_name=row.item_name, qty=row.qty, remarks=remarks, company=company,
        )

    return {
        "ok": True,
        "spare_line": row.name,
        "qc_status": result,
        "line_status": row.status,
        "stock_entry": stock_entry,
        "can_raise_replacement": result == QC_FAILED,
        "message": (
            _("Spare cleared for fitting.") if result == QC_PASSED
            else _("Spare failed QC and was moved to the Damaged bin. "
                   "Raise a replacement to continue the repair.")
        ),
    }


def history(service_request: str, spare_line=None, limit=200) -> list:
    """Everything that has happened to this job's spares, newest first."""
    filters = {"service_request": service_request}
    if spare_line:
        filters["spare_line"] = spare_line
    return frappe.get_all(
        "GoFix Spare Event Log",
        filters=filters,
        fields=["name", "event_time", "event_type", "actor", "spare_line", "spare_item",
                "item_name", "qty", "serial_no", "defect_type", "from_warehouse",
                "to_warehouse", "stock_entry", "spare_usage", "replaces_line", "remarks"],
        order_by="event_time desc, creation desc",
        limit_page_length=limit,
    )
