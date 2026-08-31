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

	# The device-intake vocabulary, so POS never hardcodes a list that the
	# Service Request DocType will then reject on save.
	from gofix.constants.device_condition import DEFAULT_DEVICE_CONDITION, DEVICE_CONDITIONS

	bootinfo["gofix_device_conditions"] = list(DEVICE_CONDITIONS)
	bootinfo["gofix_default_device_condition"] = DEFAULT_DEVICE_CONDITION
