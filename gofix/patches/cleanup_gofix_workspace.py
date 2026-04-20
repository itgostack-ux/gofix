import frappe


def execute():
	"""Clean up stale GoFix workspace and sidebar entries.

	After renaming/recreating the GoFix workspace (old: 'GoFix Services' / 'gofix-services',
	new: 'GoFix' / 'gofix'), users may have cached sidebar entries pointing to the old URL.
	This patch:
	  1. Deletes any workspace named 'gofix-services' or 'GoFix Services' (old names)
	  2. Deletes user-specific workspace overrides for GoFix module
	  3. Deletes user-specific Workspace Sidebar overrides for GoFix module
	  4. Sets app='gofix' on GoFix Workspace Sidebar entries (ensures standard sync)
	  5. Clears cache so all users get fresh sidebar
	"""

	# 1. Delete stale workspace records with old names
	for old_name in ("gofix-services", "GoFix Services"):
		if frappe.db.exists("Workspace", old_name):
			frappe.delete_doc("Workspace", old_name, force=True)
			frappe.msgprint(f"Deleted stale workspace: {old_name}")

	# 2. Delete user-specific workspace overrides for GoFix module
	user_ws = frappe.db.get_all(
		"Workspace",
		filters={
			"for_user": ("is", "set"),
			"module": ("in", ["GoFix Services", "GoFix"]),
		},
		pluck="name",
	)
	for ws_name in user_ws:
		frappe.delete_doc("Workspace", ws_name, force=True)

	if user_ws:
		frappe.msgprint(f"Deleted {len(user_ws)} user-specific GoFix workspace override(s)")

	# 3. Delete user-specific Workspace Sidebar overrides for GoFix module
	user_sidebars = frappe.db.get_all(
		"Workspace Sidebar",
		filters={
			"for_user": ("is", "set"),
			"module": ("in", ["GoFix Services", "GoFix"]),
		},
		pluck="name",
	)
	for sb_name in user_sidebars:
		frappe.delete_doc("Workspace Sidebar", sb_name, force=True)

	if user_sidebars:
		frappe.msgprint(f"Deleted {len(user_sidebars)} user-specific GoFix sidebar override(s)")

	# 4. Ensure GoFix Workspace Sidebar entries have app='gofix' for proper standard sync
	frappe.db.sql("""
		UPDATE `tabWorkspace Sidebar`
		SET app = 'gofix'
		WHERE module = 'GoFix Services'
		AND (app IS NULL OR app = '')
	""")

	frappe.db.commit()

	# 5. Clear cache so all users see updated sidebar
	frappe.clear_cache()
