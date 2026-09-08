"""Quality check, on the Service Request.

Why this moved
--------------
The quality verdict lived on the Sales Order while everything it was a verdict
*about* -- the device, the issues, the solutions performed, the parts fitted --
lived on the Service Request. The checklist was even built by reading the
request's own solution lines and then written onto the order. So the document
that knew what work was done was not the document that recorded whether the work
was good, and under the single-document model there is no order to write to at
all.

QC is now recorded on the request. Where a legacy Sales Order still exists the
verdict is mirrored onto it, best effort, so the 198 older repairs and the
reports that read them stay consistent. Reads always come from the request.

The rules themselves are unchanged: a pass must answer every check and carry no
failures, and a fail is never gated -- a fail is how a device gets sent back for
rework, so blocking it on "the work is not finished" would strand the ticket.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

QC_PASS = "Pass"
QC_FAIL = "Fail"


def _as_doc(sr):
    return frappe.get_doc("Service Request", sr) if isinstance(sr, str) else sr


# ── Checklist construction ───────────────────────────────────────────────────

def build_checklist_rows(company, solution_lines) -> list:
    """The QC rows a repair deserves, per solution, OEM service-centre style.

    Each performed solution gets the checks of the template matching its issue
    category, stamped with ``linked_solution`` so a failure routes rework to
    exactly that solution and technician. One generic outgoing-inspection pack
    (the template with no issue category) is appended once, unlinked -- the
    final whole-device look.

    Pure: it reads templates and returns rows. Both the request-based flow and
    the legacy order-based one build their checklist from here, so the two can
    never drift into checking different things.
    """
    filters = {"is_active": 1}
    if company:
        filters["company"] = ["in", [company, "", None]]

    templates = frappe.get_all("GoFix QC Template", filters=filters,
                               fields=["name", "issue_category"])
    by_category = {t.issue_category: t.name for t in templates if t.issue_category}
    generic = next((t.name for t in templates if not t.issue_category), None)

    cache = {}

    def checks_of(name):
        if name not in cache:
            cache[name] = frappe.get_doc("GoFix QC Template", name).checks
        return cache[name]

    rows, seen = [], set()

    def add(template_name, solution=None, category=None):
        for check in checks_of(template_name):
            key = (solution or "", check.check_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "check_name": check.check_name,
                "is_mandatory": check.is_mandatory,
                "is_critical": getattr(check, "is_critical", 0),
                "check_type": check.get("check_type", "Pass-Fail"),
                "linked_solution": solution or "",
                "linked_issue_category": category or "",
                "result": "",
            })

    for sol in solution_lines:
        template = by_category.get(sol.get("issue_category"))
        if template:
            add(template, solution=sol.get("repair_solution"),
                category=sol.get("issue_category"))

    if generic:
        add(generic)
    return rows


def _solution_lines(sr_name) -> list:
    return frappe.get_all(
        "SR Solution Line",
        filters={"parent": sr_name, "status": ["not in", ["Cancelled"]]},
        fields=["repair_solution", "issue_category"],
        order_by="idx")


def populate_checklist(sr, force=False) -> int:
    """Fill the request's checklist from the templates its solutions call for.

    ``force`` clears first, which is what rework needs: the previous pass
    certified a smaller scope and its ticks must not be inherited.
    """
    sr = _as_doc(sr)
    if not sr.meta.get_field("qc_checklist"):
        return 0
    if sr.get("qc_checklist") and not force:
        return 0

    rows = build_checklist_rows(sr.get("company"), _solution_lines(sr.name))
    if not rows and not force:
        return 0

    sr.set("qc_checklist", [])
    for row in rows:
        sr.append("qc_checklist", row)

    sr.flags.ignore_billing_lock = True
    sr.flags.ignore_validate_update_after_submit = True
    sr.save(ignore_permissions=True)
    return len(rows)


def invalidate_checklist(sr) -> int:
    """Blank the recorded answers, keeping the rows.

    A verdict recorded before a late issue was found certified a smaller scope.
    The template does not need rebuilding, only re-answering.
    """
    sr = _as_doc(sr)
    names = frappe.get_all("GoFix QC Checklist",
                           filters={"parent": sr.name, "parenttype": "Service Request"},
                           pluck="name")
    for name in names:
        frappe.db.set_value("GoFix QC Checklist", name,
                            {"result": "", "remarks": "", "fail_reason": ""},
                            update_modified=False)
    return len(names)


def checklist_rows(sr_name, limit=None) -> list:
    """The checklist as the hub renders it, request first, order as fallback.

    Legacy repairs answered their checks on the Sales Order and those answers
    are the real record for those tickets, so they are still shown.
    """
    fields = ["name", "check_name", "result", "remarks", "linked_solution",
              "fail_reason", "rework_required", "rework_iteration"]
    rows = frappe.get_all("GoFix QC Checklist",
                          filters={"parent": sr_name, "parenttype": "Service Request"},
                          fields=fields, order_by="idx asc",
                          limit_page_length=limit or 0)
    if rows:
        return rows
    so = frappe.db.get_value("Service Request", sr_name, "service_order")
    if not so:
        return []
    return frappe.get_all("GoFix QC Checklist", filters={"parent": so},
                          fields=fields, order_by="idx asc",
                          limit_page_length=limit or 0)


# ── The QC step ──────────────────────────────────────────────────────────────

def open_qc(sr) -> dict:
    """Move a repair into QC: reset the verdict and lay out the checks."""
    sr = _as_doc(sr)
    from gofix.gofix_services.doctype.service_request.service_request import (
        get_unresolved_issue_gaps)

    gaps = get_unresolved_issue_gaps(sr)
    if not gaps["ready_for_qc"]:
        return {"ok": False, "open_solutions": gaps["open_solutions"]}

    is_rework = cint(sr.get("rework_count")) > 0
    _stamp(sr, {"qc_status": "Awaiting"})
    count = populate_checklist(sr, force=is_rework)
    return {"ok": True, "checks": count}


def save_results(sr, checklist) -> int:
    """Record the answers the technician entered against each check."""
    sr = _as_doc(sr)
    saved = 0
    existing = {r.name: r for r in (sr.get("qc_checklist") or [])}
    by_check = {r.check_name: r for r in (sr.get("qc_checklist") or [])}
    for entry in checklist or []:
        row = existing.get(entry.get("name")) or by_check.get(entry.get("check_name"))
        if not row:
            continue
        frappe.db.set_value("GoFix QC Checklist", row.name, {
            "result": entry.get("result", row.result),
            "remarks": entry.get("remarks", row.remarks or ""),
        }, update_modified=False)
        saved += 1
    return saved


def assert_can_pass(sr) -> None:
    """Every check answered, none failing. Only a pass is gated."""
    checks = checklist_rows(_as_doc(sr).name)
    if not checks:
        # No template matched this ticket's solutions, so there is nothing to
        # answer. Blocking here would strand the repair -- the checklist can
        # never appear and the invoice can never be raised. The verdict is
        # recorded on the sign-off alone.
        frappe.msgprint(
            _("No QC checklist applies to this ticket, so the pass is recorded "
              "on your sign-off alone."), indicator="orange", alert=True)
        return

    unanswered = [c.check_name for c in checks if not (c.result or "").strip()]
    if unanswered:
        frappe.throw(_("QC cannot pass with unanswered checks: {0}.").format(
            ", ".join(unanswered)), title=_("QC Checklist Incomplete"))

    failed = [c.check_name for c in checks if (c.result or "") == QC_FAIL]
    if failed:
        frappe.throw(
            _("These checks are marked Fail, so QC cannot be passed: {0}. "
              "Record a QC Fail instead, or re-check them after rework.").format(
                ", ".join(failed)), title=_("Failed Checks Present"))


def record_verdict(sr, result, remarks=None) -> dict:
    """Write the verdict on the request, and mirror it onto a legacy order."""
    sr = _as_doc(sr)
    if result not in (QC_PASS, QC_FAIL):
        frappe.throw(_("QC result must be Pass or Fail."), title=_("Validation Error"))

    updates = {
        "qc_status": result,
        "qc_checked_by": frappe.session.user,
        "qc_datetime": now_datetime(),
    }
    if remarks:
        updates["qc_remarks"] = remarks

    # A fail is a rework. The count drove the max-rework alert and the forced
    # checklist rebuild, both of which read it off the Sales Order -- so under
    # the single-document model it has to be counted here or every repair looks
    # like a first attempt forever.
    if result == QC_FAIL:
        count = cint(sr.get("rework_count")) + 1
        updates["rework_count"] = count
        _warn_on_repeated_rework(sr, count)

    if result == QC_PASS and sr.meta.get_field("qc_pass_datetime"):
        updates["qc_pass_datetime"] = updates["qc_datetime"]

    _stamp(sr, updates)

    # A pass means the work is finished, so it can finally be costed. The
    # figures are provisional against the approved estimate until the repair is
    # billed, at which point registering the invoice recomputes them.
    if result == QC_PASS:
        try:
            from gofix.gofix_services.costing import update_service_costing

            update_service_costing(sr)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"qc: costing failed for {sr.name}")

    sr.add_comment("Comment", _("QC {0} by {1}.{2}").format(
        result, frappe.session.user, f" {remarks}" if remarks else ""))
    return {"ok": True, "qc_status": result}


def _stamp(sr, updates) -> None:
    """Write QC state without tripping submit rules or the billing lock.

    QC is a certification of work, not a commercial term, so it is allowed to
    move on a billed repair -- the lock exists to stop the price and the scope
    changing after the customer has paid.
    """
    sr = _as_doc(sr)
    sr.flags.ignore_billing_lock = True
    sr.db_set(updates, update_modified=True)
