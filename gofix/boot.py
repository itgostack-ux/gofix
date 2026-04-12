# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

def boot_session(bootinfo):
	"""Push GoFix settings to client at login."""
	import frappe
	if frappe.db.exists("DocType", "GoFix Settings"):
		settings = frappe.get_cached_doc("GoFix Settings")
		bootinfo["gofix_settings"] = {
			"default_repair_warranty_days": getattr(settings, "default_repair_warranty_days", 30),
			"enable_customer_portal": getattr(settings, "enable_customer_portal", 0),
		}
