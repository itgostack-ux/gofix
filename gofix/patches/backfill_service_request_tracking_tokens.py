"""Backfill random tracking tokens on existing Service Request rows."""

import uuid

import frappe


def _token_exists(token: str, current_name: str | None = None) -> bool:
	filters = {"tracking_token": token}
	if current_name:
		filters["name"] = ["!=", current_name]
	return bool(frappe.db.exists("Service Request", filters))


def _new_token(current_name: str | None = None) -> str:
	for _ in range(10):
		token = str(uuid.uuid4())
		if not _token_exists(token, current_name=current_name):
			return token
	frappe.throw("Could not generate a unique Service Request tracking token.")


def execute():
	if not frappe.db.table_exists("Service Request"):
		return
	if not frappe.db.has_column("Service Request", "tracking_token"):
		return

	rows = frappe.db.sql(
		"""
		SELECT name
		FROM `tabService Request`
		WHERE tracking_token IS NULL OR tracking_token = ''
		""",
		as_dict=True,
	)

	for row in rows:
		frappe.db.sql(
			"""
			UPDATE `tabService Request`
			SET tracking_token = %s
			WHERE name = %s
			""",
			(_new_token(current_name=row.name), row.name),
		)

	frappe.db.commit()
	print(f"Backfilled Service Request tracking tokens: {len(rows)}")
