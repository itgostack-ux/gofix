"""One message per store, per day: what is stuck, and whose it is.

Twenty-nine devices sat in shops for more than a week with no technician on
them, and nobody had been told. Not for want of an alerting system -- the SLA
sweep runs every fifteen minutes against eighteen active rules, finds the
breaches, and resolves four Service Managers to tell. It had raised 11
escalations and produced **zero** durable records, because `_send_sla_alert`
only ever does two things:

  * `publish_realtime("msgprint")` -- a toast in a browser that is probably not
    open. Miss it and it is gone.
  * `sendmail(delayed=True)` -- and outgoing email on this bench has failed
    43,862 times.

So the alert was real, the audience was right, and the message evaporated.

The other half of the lesson is in the same table: "Stock transfer stuck in
transit - BMTNMT26000005" appears **1,159 times** for one transfer, because its
suppression key expires every hour and the condition never clears. An alert
repeated 1,159 times is not an alert, it is wallpaper, and the next real one is
invisible behind it.

This is built against both failures:

* **Durable.** A Notification Log per recipient -- it waits in the bell until
  someone opens it. The realtime toast is a bonus for whoever is looking.
* **One per store per day.** Every stuck job in a single digest, deduped on
  (store, date), so a store with forty stalled jobs gets one message and not
  forty. Nothing repeats within the day.
* **Drafts count.** Sixteen of the twenty-nine were unsubmitted tickets, which
  the SLA sweep filters out with `docstatus: 1`. A device on the shelf is the
  shop's problem whether or not the paperwork was finished -- arguably more so.
* **The reason, not the elapsed time.** "No technician assigned for 8 days" is
  actionable; "SLA breach, 292h elapsed" is a number.
* **A store that reaches nobody is itself reported.** Scope must fail closed,
  but failing closed silently is how a store falls off the map for a month.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, time_diff_in_hours, today

# How long a job may sit in each state before the store is asked about it.
# Deliberately generous: this is the "nobody has looked at this" net under the
# SLA, not a second SLA.
THRESHOLDS = {
	"draft": 4,             # a device taken in on a ticket never submitted
	"unassigned": 24,       # accepted, no technician
	"awaiting_customer": 48,  # estimate with the customer, no answer
	"in_repair": 72,        # a technician holds it and nothing has moved
	"qc_pending": 24,       # repaired, waiting on the check
	"billable": 24,         # passed QC, not billed
	"uncollected": 120,     # billed, customer has not collected
}

REASON_LABEL = {
	"draft": _("Ticket never submitted"),
	"unassigned": _("No technician assigned"),
	"awaiting_customer": _("Waiting for the customer to approve"),
	"in_repair": _("With a technician, no movement"),
	"qc_pending": _("Repaired, waiting for QC"),
	"billable": _("Passed QC, not billed"),
	"uncollected": _("Billed, not collected"),
}

# Order matters: the first matching reason is the one the store must act on.
_ORDER = ("draft", "unassigned", "awaiting_customer", "in_repair",
          "qc_pending", "billable", "uncollected")


def _classify(r) -> str | None:
	"""Why this job is not moving, in the order a store would act on it."""
	if r.delivered_datetime:
		return None
	if r.docstatus == 2:
		return None
	if r.docstatus == 0:
		return "draft"
	if r.service_invoice_submitted:
		return "uncollected"
	if r.qc_status == "Pass":
		return "billable"
	if r.job_count and not r.qc_status:
		# A technician has it. Whether that is QC-pending or simply stalled
		# depends on the job being finished.
		return "qc_pending" if r.job_done else "in_repair"
	if cint(r.estimate_approval_pending) and not cint(r.customer_confirmed):
		return "awaiting_customer"
	if not r.job_count:
		return "unassigned"
	return None


def stuck_jobs(company: str | None = None, store: str | None = None,
               warehouse: str | None = None) -> list:
	"""Every open repair that has stopped moving, with why and for how long.

	Uses `frappe.db.sql` rather than `get_list` on purpose: this runs from the
	scheduler with no user, and the caller scopes it explicitly by store. The
	whitelisted wrapper below is the one that applies the user's permissions.
	"""
	conds, vals = ["IFNULL(sr.delivered_datetime,'') = ''", "sr.docstatus < 2"], {}
	if company:
		conds.append("sr.company = %(company)s")
		vals["company"] = company
	if warehouse:
		conds.append("sr.source_warehouse = %(warehouse)s")
		vals["warehouse"] = warehouse

	rows = frappe.db.sql(f"""
		SELECT sr.name, sr.docstatus, sr.company, sr.source_warehouse,
		       sr.customer_name, sr.contact_number, sr.decision, sr.qc_status,
		       sr.estimate_approval_pending, sr.customer_confirmed,
		       sr.promised_completion_datetime,
		       IFNULL(sr.received_datetime, sr.creation) AS since,
		       sr.creation,
		       (SELECT COUNT(*) FROM `tabJob Assignment` ja
		         WHERE ja.service_request = sr.name
		           AND ja.assignment_status != 'Cancelled') AS job_count,
		       (SELECT COUNT(*) FROM `tabJob Assignment` ja
		         WHERE ja.service_request = sr.name
		           AND ja.assignment_status = 'Completed') AS job_done,
		       (SELECT COALESCE(e.employee_name, ja.service_engineer, ja.user)
		         FROM `tabJob Assignment` ja
		         LEFT JOIN `tabEmployee` e ON e.name = ja.service_engineer
		         WHERE ja.service_request = sr.name
		           AND ja.assignment_status != 'Cancelled'
		         ORDER BY ja.creation DESC LIMIT 1) AS technician,
		       (SELECT COUNT(*) FROM `tabSales Invoice` si
		         WHERE si.name = sr.service_invoice AND si.docstatus = 1)
		         AS service_invoice_submitted
		FROM `tabService Request` sr
		WHERE {' AND '.join(conds)}
	""", vals, as_dict=True)

	now = now_datetime()
	out = []
	for r in rows:
		reason = _classify(r)
		if not reason:
			continue
		hours = flt(time_diff_in_hours(now, r.since))
		if hours < THRESHOLDS[reason]:
			continue
		out.append({
			"name": r.name,
			"reason": reason,
			"label": REASON_LABEL[reason],
			"hours": round(hours, 1),
			"days": round(hours / 24.0, 1),
			"customer": r.customer_name or "",
			"contact": r.contact_number or "",
			"technician": r.technician or "",
			"warehouse": r.source_warehouse or "",
			"company": r.company,
			"overdue_promise": bool(
				r.promised_completion_datetime
				and r.promised_completion_datetime < now),
		})
	out.sort(key=lambda x: (_ORDER.index(x["reason"]), -x["hours"]))
	return out


# ──────────────────────────────────────────────────────────────────────────

def _store_of(warehouse: str) -> str | None:
	return frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")


def _recipients(company: str, store: str | None) -> list:
	"""Who runs this store. Fails closed, but never silently -- see the caller."""
	from gofix.config import get_business_role_users

	roles = ("Service Manager", "Store Manager", "CH Zonal Sales Manager")
	try:
		return sorted(get_business_role_users(roles, company=company, store=store))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "standup recipients")
		return []


def _digest_html(store_label: str, jobs: list) -> str:
	by_reason: dict = {}
	for j in jobs:
		by_reason.setdefault(j["reason"], []).append(j)

	parts = [
		f"<p><b>{frappe.utils.escape_html(store_label)}</b> — "
		+ _("{0} repair(s) have stopped moving.").format(len(jobs)) + "</p>"
	]
	for reason in _ORDER:
		group = by_reason.get(reason)
		if not group:
			continue
		parts.append(f"<p style='margin:8px 0 2px'><b>{REASON_LABEL[reason]}"
		             f" ({len(group)})</b></p><ul style='margin:0;padding-left:18px'>")
		for j in group[:10]:
			who = f" · {frappe.utils.escape_html(j['technician'])}" if j["technician"] else ""
			late = " · <b>past the promised date</b>" if j["overdue_promise"] else ""
			parts.append(
				f"<li>{j['name']} — {frappe.utils.escape_html(j['customer'])}"
				f" — {j['days']}d{who}{late}</li>")
		if len(group) > 10:
			parts.append("<li>" + _("and {0} more").format(len(group) - 10) + "</li>")
		parts.append("</ul>")
	return "".join(parts)


def daily_store_standup(dry_run: bool = False) -> dict:
	"""One digest per PERSON per day, covering every store they answer for.

	Grouping by store was the obvious shape and it was wrong: a Service Manager
	covering nine stores would have got nine messages every morning, which is the
	1,159-identical-alerts failure rebuilt in a nicer wrapper. The unit of
	attention is a person, not a location -- so each recipient gets one message,
	their stores listed inside it, worst first. Sixteen stores and nineteen
	people means nineteen messages, not a hundred and thirty-five.
	"""
	stuck = stuck_jobs()
	if not stuck:
		return {"stores": 0, "jobs": 0, "notified": 0, "unreachable": []}

	# Group by the store a customer would name, not by warehouse id.
	by_store: dict = {}
	for j in stuck:
		by_store.setdefault((j["company"], j["warehouse"]), []).append(j)

	# Then invert: who needs to hear about which stores.
	for_user: dict = {}
	unreachable = []
	for (company, warehouse), jobs in by_store.items():
		store = _store_of(warehouse) if warehouse else None
		label = store or warehouse or company
		users = _recipients(company, store)
		if not users:
			# Failing closed is right; failing closed in silence is how a store
			# drops off the map. Surfaced in the return value and the log.
			unreachable.append({"store": label, "company": company, "jobs": len(jobs)})
			continue
		for user in users:
			for_user.setdefault(user, []).append((label, jobs))

	stamp = today()
	notified = 0
	for user, blocks in for_user.items():
		blocks.sort(key=lambda b: -len(b[1]))
		total = sum(len(jobs) for _label, jobs in blocks)
		subject = (_("{0} repair(s) stuck at {1} store(s)").format(total, len(blocks))
		           if len(blocks) > 1
		           else _("{0}: {1} repair(s) stuck").format(blocks[0][0], total))
		html = "".join(_digest_html(label, jobs) for label, jobs in blocks)

		# One per user per day. The record itself is the dedupe, not the cache:
		# a cache key set inside a transaction that then rolls back would
		# suppress the whole day having delivered nothing. The cache is only a
		# fast path in front of it.
		key = f"gofix:standup:{stamp}:{user}"
		if frappe.cache.get_value(key):
			continue
		# Explicit SQL, not db.exists with a range filter: asked that way it
		# matched this user's digest from ANY day, so a store that was told
		# yesterday was never told again.
		already = frappe.db.sql("""
			SELECT 1 FROM `tabNotification Log`
			WHERE for_user = %s AND document_type = 'Service Request'
			  AND subject LIKE %s AND DATE(creation) = %s LIMIT 1""",
			(user, "%stuck%", stamp))
		if already:
			continue
		if dry_run:
			notified += 1
			continue
		try:
			frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": "Service Request",
				"document_name": blocks[0][1][0]["name"],
				"subject": subject,
				"email_content": html,
			}).insert(ignore_permissions=True)
			# A toast as well, for whoever happens to be looking. The log is what
			# makes it survive not being looked at.
			frappe.publish_realtime(
				"msgprint", {"message": subject, "alert": True}, user=user)
			frappe.cache.set_value(key, 1, expires_in_sec=26 * 3600)
			# The scheduler commits per job; committing here too means a later
			# recipient's failure cannot roll back the ones already told.
			frappe.db.commit()
			notified += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), "standup notify")

	if unreachable and not dry_run:
		frappe.log_error(
			frappe.as_json(unreachable),
			"GoFix standup: stores with stuck jobs and nobody to tell")

	return {"stores": len(by_store), "jobs": len(stuck), "recipients": len(for_user),
	        "notified": notified, "unreachable": unreachable}


@frappe.whitelist()
def my_standup(company=None, warehouse=None) -> dict:
	"""The same list, for a screen. Permission-checked, unlike the sweep."""
	frappe.has_permission("Service Request", "read", throw=True)
	if warehouse:
		from gofix.scope_guard import assert_warehouse

		assert_warehouse(warehouse=warehouse)
	jobs = stuck_jobs(company=company, warehouse=warehouse)
	counts: dict = {}
	for j in jobs:
		counts[j["reason"]] = counts.get(j["reason"], 0) + 1
	return {
		"jobs": jobs[:100],
		"total": len(jobs),
		"counts": counts,
		"labels": {k: str(v) for k, v in REASON_LABEL.items()},
	}
