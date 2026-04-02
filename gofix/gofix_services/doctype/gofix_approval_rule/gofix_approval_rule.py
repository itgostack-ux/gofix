# Copyright (c) 2025, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class GoFixApprovalRule(Document):
	pass


def get_approval_rule(rule_type, value=0, company=None, issue_category=None, warranty_status=None):
	"""Find the best matching active approval rule.

	Rules are evaluated in priority order (highest first).  A rule matches if:
	- rule_type matches
	- threshold_amount <= value  (0 means always match)
	- company is blank or matches
	- issue_category is blank or matches
	- warranty_status is blank or matches
	"""
	filters = {"is_active": 1, "rule_type": rule_type}
	if company:
		filters["company"] = ["in", [company, "", None]]

	rules = frappe.get_all("GoFix Approval Rule",
		filters=filters,
		fields=["name", "threshold_amount", "approver_role", "escalation_role",
				"auto_reject_hours", "issue_category", "warranty_status", "priority"],
		order_by="priority desc, threshold_amount desc")

	for rule in rules:
		if flt(rule.threshold_amount) > flt(value):
			continue
		if rule.issue_category and rule.issue_category != issue_category:
			continue
		if rule.warranty_status and rule.warranty_status != warranty_status:
			continue
		return rule
	return None


def check_approval_required(rule_type, value=0, company=None, issue_category=None, warranty_status=None):
	"""Check if approval is required and return the rule if so."""
	rule = get_approval_rule(rule_type, value, company, issue_category, warranty_status)
	return rule
