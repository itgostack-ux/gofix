import frappe


def execute():
	"""Provision the CH Exception Type used by the Confirm-step price gate.

	Routing (which roles approve, SLA, escalation) is ops configuration via
	CH Approval Authority — deliberately not seeded here, same as the
	below-cost type this mirrors.
	"""
	if not frappe.db.exists("DocType", "CH Exception Type"):
		return
	if frappe.db.exists("CH Exception Type", "Service Estimate Override"):
		return
	frappe.get_doc({
		"doctype": "CH Exception Type",
		"exception_type": "Service Estimate Override",
		"enabled": 1,
		"routing_mode": "Approval Matrix",
	}).insert(ignore_permissions=True)
