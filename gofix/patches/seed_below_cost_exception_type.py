import frappe


def execute():
	"""Provision the CH Exception Type used by the Ops Hub below-cost billing gate.

	Routing (which roles approve, SLA, escalation) is ops configuration via
	CH Approval Authority — deliberately not seeded here.
	"""
	if not frappe.db.exists("DocType", "CH Exception Type"):
		return
	if frappe.db.exists("CH Exception Type", "Service Below Cost Billing"):
		return
	frappe.get_doc({
		"doctype": "CH Exception Type",
		"exception_type": "Service Below Cost Billing",
		"enabled": 1,
		"routing_mode": "Approval Matrix",
	}).insert(ignore_permissions=True)
