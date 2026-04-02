# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_hours, cint


class GoFixSLARule(Document):
	pass


def get_sla_rule(issue_category, priority, company=None, warranty_plan=None, warranty_status=None):
	"""Return the best-matching SLA rule for the given criteria."""
	filters = {"is_active": 1}
	if company:
		filters["company"] = ["in", [company, "", None]]

	rules = frappe.get_all("GoFix SLA Rule",
		filters=filters,
		fields=["name", "issue_category", "priority", "target_hours",
				"warning_pct", "escalation_1_role", "escalation_2_role",
				"warranty_plan", "warranty_status",
				"escalation_1_email", "escalation_2_email", "send_email_alert"],
		order_by="issue_category desc, priority desc")

	# Best match: exact category+priority+warranty > category+priority > category > catch-all
	best = None
	best_score = -1
	for r in rules:
		score = 0
		cat_match = (not r.issue_category) or r.issue_category == issue_category
		pri_match = (not r.priority) or r.priority == priority
		plan_match = (not r.warranty_plan) or r.warranty_plan == warranty_plan
		wstatus_match = (not r.warranty_status) or r.warranty_status == warranty_status

		if not (cat_match and pri_match and plan_match and wstatus_match):
			continue

		if r.issue_category:
			score += 4
		if r.priority:
			score += 2
		if r.warranty_plan or r.warranty_status:
			score += 1

		if score > best_score:
			best = r
			best_score = score

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
				"company", "source_warehouse", "warranty_plan", "warranty_status"])

	for sr in open_srs:
		if not sr.received_datetime:
			continue
		sla = get_sla_rule(
			sr.issue_category, sr.priority, sr.company,
			warranty_plan=sr.get("warranty_plan"),
			warranty_status=sr.get("warranty_status"),
		)
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
	"""Send in-app + optional email escalation notification for SLA breach."""
	key = f"sla_escalation_{level}_{sr_name}"
	if frappe.cache.get_value(key):
		return  # already sent

	role = sla.escalation_1_role if level == 1 else sla.escalation_2_role
	users = frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"},
						   pluck="parent")

	message = _("SLA Breach (Level {0}): Service Request {1} — {2:.1f}h elapsed (target: {3}h)").format(
		level, sr_name, elapsed, sla.target_hours)

	for user in users:
		frappe.publish_realtime("msgprint",
			{"message": message, "alert": True},
			user=user)

	# Send email if configured
	if sla.send_email_alert:
		email_addr = sla.escalation_1_email if level == 1 else sla.escalation_2_email
		recipients = [u for u in users if "@" in (u or "")]
		if email_addr:
			recipients.append(email_addr)
		if recipients:
			try:
				frappe.sendmail(
					recipients=list(set(recipients)),
					subject=f"GoFix SLA Breach Level {level}: {sr_name}",
					message=(
						f"<b>SLA Breach Alert — Level {level}</b><br><br>"
						f"Service Request: <b>{sr_name}</b><br>"
						f"Elapsed: <b>{elapsed:.1f} hours</b><br>"
						f"Target: <b>{sla.target_hours} hours</b><br>"
						f"SLA Rule: {sla.name}<br><br>"
						f"Please take immediate action."
					),
					reference_doctype="Service Request",
					reference_name=sr_name,
					now=True,
				)
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"SLA alert email failed for {sr_name}")

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
