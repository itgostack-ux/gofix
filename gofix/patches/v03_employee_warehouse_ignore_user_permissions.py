"""Stop Employee.gofix_service_warehouse from hiding every employee.

`gofix_service_warehouse` says where a technician is based. It is descriptive,
never an authorisation control — access is decided by CH User Scope and the role
matrix. Frappe cannot know that, so under
`System Settings -> Apply Strict User Permissions` it built

    `gofix_service_warehouse` in (<the user's allowed warehouses>)

with **no** `ifnull(field, '') = ''` escape. The field is empty on almost every
Employee, so every user holding a Warehouse User Permission — which ch_erp15
creates from each CH User Scope store row — matched nothing and saw ZERO
employees in every desk list, with no error message.

That silently broke Frappe HR, ch_hrms, and this app's own technician picker
(`technician_intelligence.recommend_technicians` reads Employee through
`frappe.get_list`). Holding System Manager did not help: that role bypasses
ROLE permissions, not USER permissions.

Setting `ignore_user_permissions` restores visibility without weakening any real
control. Idempotent.
"""

import frappe

FIELD = "Employee-gofix_service_warehouse"


def execute():
	if not frappe.db.exists("Custom Field", FIELD):
		return
	if frappe.db.get_value("Custom Field", FIELD, "ignore_user_permissions"):
		return

	frappe.db.set_value("Custom Field", FIELD, "ignore_user_permissions", 1)
	frappe.clear_cache(doctype="Employee")

	frappe.logger("gofix").info(
		"Employee.gofix_service_warehouse: ignore_user_permissions set — "
		"employee lists were empty for every user holding a Warehouse User Permission."
	)
