"""Idempotently make store-linked GoFix POS profiles repair-intake ready."""

import frappe


def execute(company=None):
	result = {"enabled": [], "updated": [], "skipped": []}
	filters = {"disabled": 0}
	if company:
		filters["company"] = company
	stores = frappe.get_all(
		"CH Store",
		filters=filters,
		fields=["name", "company", "warehouse", "pos_profile"],
		order_by="name",
	)
	for store in stores:
		if not store.pos_profile or not frappe.db.exists("POS Profile", store.pos_profile):
			result["skipped"].append({"store": store.name, "reason": "missing POS Profile link"})
			continue

		profile = frappe.get_doc("POS Profile", store.pos_profile)
		cost_center = frappe.db.get_value(
			"Cost Center",
			{"company": store.company, "name": ("like", f"POS - {store.name} - %"), "is_group": 0},
			"name",
		)
		if not cost_center:
			result["skipped"].append({"store": store.name, "reason": "missing store cost center"})
			continue

		profile.warehouse = store.warehouse
		profile.cost_center = cost_center
		profile.write_off_cost_center = cost_center
		if not profile.payments:
			profile.append(
				"payments",
				{"mode_of_payment": "Cash", "default": 1, "allow_in_returns": 1},
			)
		profile.disabled = 0
		profile.save(ignore_permissions=True)

		extension_name = frappe.db.get_value(
			"POS Profile Extension", {"pos_profile": profile.name}, "name"
		)
		if extension_name:
			extension = frappe.get_doc("POS Profile Extension", extension_name)
		else:
			extension = frappe.new_doc("POS Profile Extension")
			extension.pos_profile = profile.name
		extension.store = store.name
		extension.disabled = 0
		extension.enable_repair_intake = 1
		extension.save(ignore_permissions=True)

		result["enabled"].append(profile.name)
		result["updated"].append(extension.name)

	frappe.db.commit()
	return result
