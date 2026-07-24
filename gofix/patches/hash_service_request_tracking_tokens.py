import hashlib

import frappe


def execute():
	if not frappe.db.table_exists("Service Request") or not frappe.db.has_column(
		"Service Request", "tracking_token"
	):
		return

	page_size = 500
	while True:
		rows = frappe.db.sql(
			"""
			SELECT name, tracking_token
			FROM `tabService Request`
			WHERE COALESCE(tracking_token, '') != ''
			  AND tracking_token NOT LIKE 'sha256:%%'
			ORDER BY name
			LIMIT %s
			""",
			(page_size,),
			as_dict=True,
		)
		if not rows:
			break
		for row in rows:
			digest = f"sha256:{hashlib.sha256(row.tracking_token.encode()).hexdigest()}"
			frappe.db.set_value(
				"Service Request",
				row.name,
				"tracking_token",
				digest,
				update_modified=False,
			)
		if len(rows) < page_size:
			break
