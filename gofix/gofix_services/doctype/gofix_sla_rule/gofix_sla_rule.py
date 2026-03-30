# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_hours, cint


class GoFixSLARule(Document):
	pass


def get_sla_rule(issue_category, priority, company=None):
	"""Return the best-matching SLA rule for the given criteria."""
	filters = {"is_active": 1}
	if company:
		filters["company"] = ["in", [company, "", None]]

	rules = frappe.get_all("GoFix SLA Rule",
		filters=filters,
		fields=["name", "issue_category", "priority", "target_hours",
				"warning_pct", "escalation_1_role", "escalation_2_role"],
		order_by="issue_category desc, priority desc")

	# Best match: exact category+priority > category only > priority only > catch-all
	best = None
	for r in rules:
		cat_match = (not r.issue_category) or r.issue_category == issue_category
		pri_match = (not r.priority) or r.priority == priority
		if cat_match and pri_match:
			if r.issue_category and r.priority:
				return r  # exact match — best possible
			if not best:
				best = r
			elif r.issue_category and not best.issue_category:
				best = r
	return best


def check_gofix_sla_breach():
	"""Scheduled task: check all active Service Requests against SLA rules.

	Runs every 15 minutes. Sends warnings and escalation notifications.
	"""
	open_srs = frappe.get_all("Service Request",
		filters={
			"decision": ["in", ["Accepted", "In Service"]],
			"docstatus": 1,
		},
		fields=["name", "issue_category", "priority", "received_datetime",
				"company", "source_warehouse"])

	for sr in open_srs:
		if not sr.received_datetime:
			continue
		sla = get_sla_rule(sr.issue_category, sr.priority, sr.company)
		if not sla:
			continue

		elapsed = time_diff_in_hours(now_datetime(), sr.received_datetime)
		if sla.target_hours <= 0:
			continue
		pct = (elapsed / sla.target_hours) * 100

		if pct >= 120 and sla.escalation_2_role:
			_send_sla_alert(sr.name, sla, level=2, elapsed=elapsed)
		elif pct >= 100 and sla.escalation_1_role:
			_send_sla_alert(sr.name, sla, level=1, elapsed=elapsed)
		elif pct >= (sla.warning_pct or 80):
			_send_sla_warning(sr.name, sla, elapsed=elapsed)


def _send_sla_alert(sr_name, sla, level, elapsed):
	"""Send in-app escalation notification for SLA breach."""
	key = f"sla_escalation_{level}_{sr_name}"
	if frappe.cache.get_value(key):
		return  # already sent

	role = sla.escalation_1_role if level == 1 else sla.escalation_2_role
	users = frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"},
						   pluck="parent")

	for user in users:
		frappe.publish_realtime("msgprint",
			{"message": _("SLA Breach (Level {0}): Service Request {1} — {2:.1f}h elapsed (target: {3}h)").format(
				level, sr_name, elapsed, sla.target_hours),
			 "alert": True},
			user=user)

	frappe.cache.set_value(key, 1, expires_in_sec=3600)


def _send_sla_warning(sr_name, sla, elapsed):
	"""Send warning to assigned technician that SLA is approaching."""
	key = f"sla_warning_{sr_name}"
	if frappe.cache.get_value(key):
		return

	# Find assigned tech from latest Job Assignment
	tech_user = frappe.db.get_value("Job Assignment",
		{"service_request": sr_name, "assignment_status": ["in", ["Open", "In Progress"]]},
		"user", order_by="creation desc")

	if tech_user:
		frappe.publish_realtime("msgprint",
			{"message": _("SLA Warning: {0} is at {1:.0f}% of target ({2}h). Please expedite.").format(
				sr_name, (elapsed / sla.target_hours) * 100, sla.target_hours),
			 "alert": True},
			user=tech_user)

	frappe.cache.set_value(key, 1, expires_in_sec=900)  # re-warn after 15 min
