# Copyright (c) 2026, GoFix and contributors

"""Loaners, feedback, bench capacity, and lapsed certifications.

Four small capabilities that share a shape: the data model for each already
existed somewhere in the app, and what was missing was the piece that used it.
Custody tracking existed but nothing lent a device. WhatsApp delivery existed
but nobody was ever asked how it went. Technician grades and skills drove
routing but no authorisation ever expired. Damaged stock accumulated with
nothing to show an inspector.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, nowdate

LOANER_OUT = ("Issued",)


# ── loaner devices ───────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def issue_loaner(service_request, serial_no, remarks=None) -> dict:
	"""Hand a courtesy device to the customer, against this ticket.

	Recorded on the ticket and in the custody log, which is the same trail the
	customer's own device leaves. A loaner that is never booked back in is the
	usual way one goes missing, so `outstanding_loaners` reads off this.
	"""
	sr = frappe.get_doc("Service Request", service_request)
	sr.check_permission("write")

	serial_no = (serial_no or "").strip()
	if not serial_no:
		frappe.throw(_("Enter the loaner's serial or IMEI."), title=_("Validation Error"))

	if sr.get("loaner_status") in LOANER_OUT:
		frappe.throw(
			_("A loaner ({0}) is already out on this ticket.").format(sr.get("loaner_serial_no")),
			title=_("Loaner Already Issued"),
		)

	held = frappe.db.get_value(
		"Service Request",
		{"loaner_serial_no": serial_no, "loaner_status": "Issued", "name": ("!=", service_request)},
		"name",
	)
	if held:
		frappe.throw(
			_("Loaner {0} is already out with repair {1}.").format(serial_no, held),
			title=_("Loaner Unavailable"),
		)

	sr.db_set({
		"loaner_status": "Issued",
		"loaner_serial_no": serial_no,
		"loaner_issued_at": now_datetime(),
	}, update_modified=True)
	sr.add_comment(
		"Info",
		_("Loaner {0} issued to the customer. {1}").format(serial_no, remarks or ""),
	)
	return {"ok": True, "serial_no": serial_no}


@frappe.whitelist(methods=["POST"])
def return_loaner(service_request, condition=None, remarks=None) -> dict:
	"""Book the courtesy device back in."""
	sr = frappe.get_doc("Service Request", service_request)
	sr.check_permission("write")

	if sr.get("loaner_status") not in LOANER_OUT:
		frappe.throw(_("No loaner is currently out on this ticket."), title=_("Nothing to Return"))

	sr.db_set({
		"loaner_status": "Returned",
		"loaner_returned_at": now_datetime(),
	}, update_modified=True)
	sr.add_comment(
		"Info",
		_("Loaner {0} returned{1}. {2}").format(
			sr.get("loaner_serial_no"),
			_(" in {0} condition").format(condition) if condition else "",
			remarks or "",
		),
	)
	return {"ok": True}


def assert_loaner_returned(sr, action: str) -> None:
	"""Do not close a ticket while our device is still with the customer."""
	if not frappe.get_meta("Service Request").get_field("loaner_status"):
		return
	if sr.get("loaner_status") not in LOANER_OUT:
		return

	frappe.throw(
		_("Cannot {0}: loaner {1} is still out with the customer. Book it back in first, "
		  "or mark it Not Returned so it stops counting as available.").format(
			action, sr.get("loaner_serial_no") or "—"
		),
		title=_("Loaner Still Out"),
	)


@frappe.whitelist()
def outstanding_loaners(company=None) -> list:
	"""Every courtesy device currently out, oldest first."""
	filters = {"loaner_status": "Issued"}
	if company:
		filters["company"] = company
	return frappe.get_all(
		"Service Request",
		filters=filters,
		fields=["name", "customer_name", "loaner_serial_no", "loaner_issued_at", "decision"],
		order_by="loaner_issued_at asc",
	)


# ── customer feedback ────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def record_feedback(service_request, csat=None, nps=None, comment=None) -> dict:
	"""Store what the customer said after collecting the device.

	Whitelisted so the WhatsApp reply handler or a follow-up page can post it
	back; both scores are optional because a customer who answers one and
	ignores the other has still told you something.
	"""
	sr = frappe.get_doc("Service Request", service_request)
	sr.check_permission("write")

	updates = {"feedback_received_at": now_datetime()}
	if csat not in (None, ""):
		score = cint(csat)
		if not 1 <= score <= 5:
			frappe.throw(_("Satisfaction score must be between 1 and 5."), title=_("Validation Error"))
		# Frappe's Rating field stores a 0–1 fraction, not the star count.
		updates["csat_score"] = score / 5.0
	if nps not in (None, ""):
		score = cint(nps)
		if not 0 <= score <= 10:
			frappe.throw(_("Recommend score must be between 0 and 10."), title=_("Validation Error"))
		updates["nps_score"] = score
	if comment:
		updates["feedback_comment"] = comment

	sr.db_set(updates, update_modified=True)
	sr.add_comment("Info", _("Customer feedback recorded."))
	return {"ok": True}


@frappe.whitelist()
def feedback_summary(company=None, from_date=None, to_date=None) -> dict:
	"""CSAT average and NPS for a period.

	NPS is promoters minus detractors as a percentage, which is the standard
	definition — a score of 0 is neutral, not absent.
	"""
	conditions = ["IFNULL(sr.feedback_received_at, '') != ''"]
	params = {}
	if company:
		conditions.append("sr.company = %(company)s")
		params["company"] = company
	if from_date:
		conditions.append("DATE(sr.feedback_received_at) >= %(from_date)s")
		params["from_date"] = from_date
	if to_date:
		conditions.append("DATE(sr.feedback_received_at) <= %(to_date)s")
		params["to_date"] = to_date

	row = frappe.db.sql(
		f"""
		SELECT
			COUNT(*)                                                   AS responses,
			AVG(NULLIF(sr.csat_score, 0)) * 5                          AS csat_avg,
			SUM(CASE WHEN sr.nps_score >= 9 THEN 1 ELSE 0 END)         AS promoters,
			SUM(CASE WHEN sr.nps_score BETWEEN 0 AND 6
			         AND sr.nps_score IS NOT NULL THEN 1 ELSE 0 END)   AS detractors,
			SUM(CASE WHEN sr.nps_score IS NOT NULL THEN 1 ELSE 0 END)  AS nps_responses
		FROM `tabService Request` sr
		WHERE {' AND '.join(conditions)}
		""",
		params, as_dict=True,
	)[0]

	rated = cint(row.nps_responses)
	nps = ((cint(row.promoters) - cint(row.detractors)) / rated * 100) if rated else None
	return {
		"responses": cint(row.responses),
		"csat_avg": round(flt(row.csat_avg), 2) if row.csat_avg else None,
		"nps": round(nps, 1) if nps is not None else None,
		"promoters": cint(row.promoters),
		"detractors": cint(row.detractors),
	}


# ── technician certification ─────────────────────────────────────────────────

def certification_valid(employee: str, on_date=None) -> bool:
	"""Is this technician's authorisation still in date?

	No expiry recorded means valid. Most technicians hold no time-limited
	authorisation, and treating a blank as expired would empty the roster.
	"""
	if not employee:
		return False
	if not frappe.get_meta("Employee").get_field("gofix_certification_expiry"):
		return True

	expiry = frappe.db.get_value("Employee", employee, "gofix_certification_expiry")
	if not expiry:
		return True
	return getdate(expiry) >= getdate(on_date or nowdate())


@frappe.whitelist()
def expiring_certifications(within_days: int = 30, company=None) -> list:
    """Technicians whose authorisation has lapsed or is about to."""
    if not frappe.get_meta("Employee").get_field("gofix_certification_expiry"):
        return []

    filters = {
        "gofix_certification_expiry": ("<=", add_days(nowdate(), cint(within_days))),
        "status": "Active",
    }
    if company:
        filters["company"] = company
    return frappe.get_all(
        "Employee",
        filters=filters,
        fields=["name", "employee_name", "gofix_certification_expiry", "gofix_certification_body"],
        order_by="gofix_certification_expiry asc",
    )


# ── bench capacity ───────────────────────────────────────────────────────────

@frappe.whitelist()
def bench_capacity(warehouse, on_date=None) -> dict:
	"""Booked work against available bench hours for one store on one day.

	Available hours come from the technicians assigned to that store; booked
	hours from the estimated minutes on solutions already scheduled. This is
	what makes a promised completion date something better than a guess.
	"""
	from gofix.config import get_float_setting

	on_date = getdate(on_date or nowdate())

	technicians = frappe.get_all(
		"Employee",
		filters={"gofix_service_warehouse": warehouse, "status": "Active"},
		fields=["name", "employee_name", "gofix_daily_bench_hours", "gofix_certification_expiry"],
	)
	available = 0.0
	certified = []
	for tech in technicians:
		if not certification_valid(tech.name, on_date):
			continue
		certified.append(tech.name)
		available += flt(tech.gofix_daily_bench_hours) or 7.5

	booked_minutes = flt(frappe.db.sql(
		"""
		SELECT COALESCE(SUM(sl.estimated_minutes), 0)
		FROM `tabSR Solution Line` sl
		JOIN `tabService Request` sr ON sr.name = sl.parent
		WHERE sl.parenttype = 'Service Request'
		  AND sl.status NOT IN ('Cancelled', 'Skipped', 'Completed')
		  AND sr.decision NOT IN ('Cancelled', 'Rejected', 'Withdrawn', 'Expired', 'Delivered')
		  AND COALESCE(NULLIF(sr.current_location, ''), sr.source_warehouse) = %(wh)s
		  AND (
			DATE(COALESCE(sr.appointment_datetime, sr.service_date)) = %(on_date)s
			OR sr.decision IN ('Accepted', 'In Service')
		  )
		""",
		{"wh": warehouse, "on_date": on_date},
	)[0][0])

	booked = booked_minutes / 60.0
	ratio = (booked / available) if available else None
	warn_at = get_float_setting("capacity_warning_ratio", 0.9, minimum=0)

	return {
		"date": str(on_date),
		"warehouse": warehouse,
		"technicians": len(certified),
		"available_hours": round(available, 2),
		"booked_hours": round(booked, 2),
		"utilisation": round(ratio, 3) if ratio is not None else None,
		"over_capacity": bool(ratio is not None and ratio >= warn_at),
		"lapsed_certifications": len(technicians) - len(certified),
	}


@frappe.whitelist(methods=["POST"])
def book_appointment(service_request, slot, source="Walk-in") -> dict:
	"""Book a slot, warning when the bench for that day is already full."""
	sr = frappe.get_doc("Service Request", service_request)
	sr.check_permission("write")

	slot_dt = get_datetime(slot)
	warehouse = sr.get("current_location") or sr.get("source_warehouse")
	capacity = bench_capacity(warehouse, slot_dt.date()) if warehouse else {}

	sr.db_set({
		"appointment_datetime": slot_dt,
		"appointment_source": source,
	}, update_modified=True)
	sr.add_comment("Info", _("Appointment booked for {0} ({1}).").format(
		frappe.utils.format_datetime(slot_dt), source
	))

	if capacity.get("over_capacity"):
		frappe.msgprint(
			_("Booked, but {0} is already at {1}% of its bench capacity that day "
			  "({2}h booked against {3}h available). The promised date is at risk.").format(
				warehouse,
				int(round(capacity["utilisation"] * 100)),
				capacity["booked_hours"],
				capacity["available_hours"],
			),
			title=_("Bench Nearly Full"),
			indicator="orange",
		)

	return {"ok": True, "slot": str(slot_dt), "capacity": capacity}
