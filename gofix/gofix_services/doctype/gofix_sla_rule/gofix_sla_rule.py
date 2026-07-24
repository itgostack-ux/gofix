# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, time_diff_in_hours, validate_email_address

from gofix.config import get_business_role_users, get_business_user_emails, get_int_setting


class GoFixSLARule(Document):
	pass


_SLA_RULE_FIELDS = [
	"name", "company", "issue_category", "priority", "target_hours", "warning_pct",
	"escalation_1_role", "escalation_2_role", "warranty_plan", "warranty_status",
	"escalation_1_email", "escalation_2_email", "send_email_alert",
]
_UNSET = object()


def _load_active_sla_rules():
	rule_limit = min(get_int_setting("sla_rule_limit", 2000, minimum=1), 10000)
	rows = frappe.get_all(
		"GoFix SLA Rule",
		filters={"is_active": 1},
		fields=_SLA_RULE_FIELDS,
		order_by="company desc, issue_category desc, priority desc, name asc",
		limit=rule_limit + 1,
	)
	if len(rows) > rule_limit:
		frappe.log_error(
			f"More than {rule_limit} active GoFix SLA rules exist. Increase SLA Rule Limit or archive obsolete rules.",
			"GoFix SLA rule limit reached",
		)
	return rows[:rule_limit]


def _select_sla_rule(rules, issue_category, priority, company=None, warranty_plan=None, warranty_status=None):
	best = None
	best_score = -1
	for rule in rules:
		if rule.company and rule.company != company:
			continue
		if rule.issue_category and rule.issue_category != issue_category:
			continue
		if rule.priority and rule.priority != priority:
			continue
		if rule.warranty_plan and rule.warranty_plan != warranty_plan:
			continue
		if rule.warranty_status and rule.warranty_status != warranty_status:
			continue

		score = 0
		if rule.company:
			score += 8
		if rule.issue_category:
			score += 4
		if rule.priority:
			score += 2
		if rule.warranty_plan or rule.warranty_status:
			score += 1
		if score > best_score:
			best = rule
			best_score = score
	return best


def get_sla_rule(issue_category, priority, company=None, warranty_plan=None, warranty_status=None):
	"""Return the best-matching SLA rule for the given criteria."""
	return _select_sla_rule(
		_load_active_sla_rules(),
		issue_category,
		priority,
		company,
		warranty_plan,
		warranty_status,
	)


def check_gofix_sla_breach():
	"""Evaluate a rotating, bounded SLA batch with preloaded rule and scope data."""
	batch_limit = min(get_int_setting("sla_scheduler_batch_limit", 500, minimum=1), 5000)
	cursor_key = "gofix:sla_sweep_cursor"
	cursor = frappe.cache.get_value(cursor_key)
	filters = {
		"decision": ["in", ["Accepted", "In Service"]],
		"docstatus": 1,
		"received_datetime": ["is", "set"],
	}
	if cursor:
		filters["name"] = [">", cursor]
	rows = frappe.get_all("Service Request",
		filters={
			**filters,
		},
		fields=["name", "issue_category", "priority", "received_datetime",
				"company", "source_warehouse", "warranty_plan", "warranty_status"],
		order_by="name asc",
		limit=batch_limit + 1,
	)
	if not rows and cursor:
		filters.pop("name", None)
		rows = frappe.get_all(
			"Service Request",
			filters=filters,
			fields=["name", "issue_category", "priority", "received_datetime",
					"company", "source_warehouse", "warranty_plan", "warranty_status"],
			order_by="name asc",
			limit=batch_limit + 1,
		)
	open_srs = rows[:batch_limit]
	if not open_srs:
		return {"evaluated": 0, "warnings": 0, "escalations": 0, "has_more": False}

	rules = _load_active_sla_rules()
	warehouses = tuple(dict.fromkeys(
		sr.source_warehouse for sr in open_srs if sr.source_warehouse
	))
	stores_by_warehouse = {}
	if warehouses and frappe.db.table_exists("CH Store"):
		store_rows = frappe.get_all(
			"CH Store",
			filters={"warehouse": ["in", warehouses], "disabled": 0},
			fields=["name", "warehouse"],
			order_by="name asc",
			limit=len(warehouses),
		)
		stores_by_warehouse = {
			row.warehouse: row.name for row in store_rows if row.warehouse
		}

	sr_names = tuple(sr.name for sr in open_srs)
	assignment_rows = frappe.db.sql(
		"""
			SELECT ranked.`service_request`, ranked.`user`
			FROM (
				SELECT assignment.`service_request`, assignment.`user`,
				       ROW_NUMBER() OVER (
					       PARTITION BY assignment.`service_request`
					       ORDER BY assignment.`creation` DESC, assignment.`name` DESC
				       ) AS rank_position
				FROM `tabJob Assignment` assignment
				WHERE assignment.`service_request` IN %(service_requests)s
				  AND assignment.`assignment_status` IN ('Open', 'In Progress')
			) ranked
			WHERE ranked.rank_position = 1
			LIMIT %(batch_limit)s
		""",
		{"service_requests": sr_names, "batch_limit": batch_limit},
		as_dict=True,
	)
	technicians = {row.service_request: row.user for row in assignment_rows}
	recipient_cache = {}
	email_cache = {}
	level_2_percent = get_int_setting("sla_level_2_percent", 120, minimum=100)
	now = now_datetime()
	warnings = 0
	escalations = 0

	for sr in open_srs:
		sla = _select_sla_rule(
			rules,
			sr.issue_category,
			sr.priority,
			sr.company,
			sr.get("warranty_plan"),
			sr.get("warranty_status"),
		)
		if not sla:
			continue

		elapsed = time_diff_in_hours(now, sr.received_datetime)
		if flt(sla.target_hours) <= 0:
			continue
		pct = (elapsed / flt(sla.target_hours)) * 100
		store = stores_by_warehouse.get(sr.source_warehouse)

		if pct >= level_2_percent and sla.escalation_2_role:
			role = sla.escalation_2_role
			cache_key = (role, sr.company, store)
			if cache_key not in recipient_cache:
				recipient_cache[cache_key] = get_business_role_users(
					(role,), company=sr.company, store=store
				)
			users = recipient_cache[cache_key]
			user_key = tuple(users)
			if user_key not in email_cache:
				email_cache[user_key] = get_business_user_emails(users)
			user_emails = email_cache[user_key]
			if _send_sla_alert(
				sr.name,
				sla,
				level=2,
				elapsed=elapsed,
				users=users,
				user_emails=user_emails,
			):
				escalations += 1
		elif pct >= 100 and sla.escalation_1_role:
			role = sla.escalation_1_role
			cache_key = (role, sr.company, store)
			if cache_key not in recipient_cache:
				recipient_cache[cache_key] = get_business_role_users(
					(role,), company=sr.company, store=store
				)
			users = recipient_cache[cache_key]
			user_key = tuple(users)
			if user_key not in email_cache:
				email_cache[user_key] = get_business_user_emails(users)
			user_emails = email_cache[user_key]
			if _send_sla_alert(
				sr.name,
				sla,
				level=1,
				elapsed=elapsed,
				users=users,
				user_emails=user_emails,
			):
				escalations += 1
		elif pct >= (sla.warning_pct or 80):
			if _send_sla_warning(
				sr.name,
				sla,
				elapsed=elapsed,
				tech_user=technicians.get(sr.name),
			):
				warnings += 1

	frappe.cache.set_value(cursor_key, open_srs[-1].name)
	return {
		"evaluated": len(open_srs),
		"warnings": warnings,
		"escalations": escalations,
		"has_more": len(rows) > batch_limit,
	}


def _scoped_escalation_users(role, sr_name, *, company=_UNSET, store=_UNSET):
	"""Escalation-role holders scoped to the SR's store/company.

	The shared resolver fails closed when company/store scope cannot be verified,
	so an SLA breach never degrades into a site-wide role blast.
	"""
	if company is _UNSET or store is _UNSET:
		sr = frappe.db.get_value(
			"Service Request", sr_name, ["source_warehouse", "company"], as_dict=True
		) or frappe._dict()
		company = sr.company
		store = None
		if sr.source_warehouse:
			store = frappe.db.get_value("CH Store", {"warehouse": sr.source_warehouse}, "name")
	return get_business_role_users((role,), company=company, store=store)


def _send_sla_alert(sr_name, sla, level, elapsed, users=None, user_emails=None):
	"""Send in-app + optional email escalation notification for SLA breach."""
	key = f"sla_escalation_{level}_{sr_name}"
	if frappe.cache.get_value(key):
		return False

	role = sla.escalation_1_role if level == 1 else sla.escalation_2_role
	if users is None:
		users = _scoped_escalation_users(role, sr_name)

	message = _("SLA Breach (Level {0}): Service Request {1} — {2:.1f}h elapsed (target: {3}h)").format(
		level, sr_name, elapsed, sla.target_hours)

	delivered = False
	for user in users:
		frappe.publish_realtime("msgprint",
			{"message": message, "alert": True},
			user=user)
		delivered = True

	# Send email if configured
	if sla.send_email_alert:
		email_addr = sla.escalation_1_email if level == 1 else sla.escalation_2_email
		recipients = list(user_emails) if user_emails is not None else get_business_user_emails(users)
		validated_email = validate_email_address(email_addr) if email_addr else ""
		if validated_email:
			recipients.append(validated_email)
		if recipients:
			try:
				sr_url = frappe.utils.get_url_to_form("Service Request", sr_name)
				frappe.sendmail(
					recipients=sorted(set(recipients)),
					subject=f"GoFix Services | SLA Breach Level {level} | {sr_name}",
					message=(
						"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
						"<div style='background:#7f1d1d;color:#ffffff;padding:12px 16px;font-weight:600'>GoFix Services - SLA Escalation</div>"
						"<div style='padding:16px'>"
						f"<p><b>SLA Breach Alert - Level {level}</b></p>"
						f"<p><b>Service Request:</b> {sr_name}<br>"
						f"<b>Elapsed:</b> {elapsed:.1f} hours<br>"
						f"<b>Target:</b> {sla.target_hours} hours<br>"
						f"<b>SLA Rule:</b> {sla.name}</p>"
						f"<p><a href='{sr_url}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
						"</div></div>"
					),
					reference_doctype="Service Request",
					reference_name=sr_name,
					delayed=True,
				)
				delivered = True
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"SLA alert email failed for {sr_name}")

	if delivered:
		frappe.cache.set_value(
			key,
			1,
			expires_in_sec=get_int_setting("sla_escalation_repeat_seconds", 3600, minimum=60),
		)
	return delivered


def _send_sla_warning(sr_name, sla, elapsed, tech_user=_UNSET):
	"""Send warning to assigned technician that SLA is approaching."""
	key = f"sla_warning_{sr_name}"
	if frappe.cache.get_value(key):
		return False

	if tech_user is _UNSET:
		tech_user = frappe.db.get_value("Job Assignment",
			{"service_request": sr_name, "assignment_status": ["in", ["Open", "In Progress"]]},
			"user", order_by="creation desc")

	if tech_user:
		frappe.publish_realtime("msgprint",
			{"message": _("SLA Warning: {0} is at {1:.0f}% of target ({2}h). Please expedite.").format(
				sr_name, (elapsed / sla.target_hours) * 100, sla.target_hours),
			 "alert": True},
			user=tech_user)

		frappe.cache.set_value(
			key,
			1,
			expires_in_sec=get_int_setting("sla_warning_repeat_seconds", 900, minimum=60),
		)
	return bool(tech_user)
