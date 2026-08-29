# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Push Workspace JSON fixtures into the database whenever the file changes.

Frappe re-imports a non-DocType fixture only when the JSON's own ``modified``
value is strictly newer than the DB row's — see ``is_db_timestamp_latest`` in
``frappe/modules/import_file.py``.  (DocTypes are exempt: they carry a
``migration_hash`` column and are compared on content.)  For Workspaces that is
a silent trap: editing a workspace JSON does nothing on ``bench migrate``
unless the ``modified`` timestamp is hand-bumped in the same edit, and nothing
warns you when the import is skipped.

A wrong ``stats_filter`` (``{"status": ...}`` on Service Request, which has no
``status`` field) therefore sat unapplied in ``services.json`` while the live
workspace kept throwing "Field not permitted in query: status" at every user
who opened it.

This module syncs on *file content hash* instead — the same idea Frappe already
uses for DocTypes — so editing the JSON is enough.

Scope note: a fixture is only force-imported when its file content changes, so
links added to these workspaces at runtime by other apps (ch_erp15's
``system_setup._wire_workspace_links`` routes "CH Delivery Claim" into
*Services*) survive until someone deliberately edits the fixture — at which
point the JSON is authoritative and must already contain them.
"""

import hashlib
import json
import os
from glob import glob

import frappe
from frappe.model import default_fields, optional_fields
from frappe.modules.import_file import import_file_by_path

#: ``tabDefaultValue`` key prefix holding the last-imported hash per fixture.
HASH_KEY_PREFIX = "gofix_workspace_hash::"


def sync_workspaces(app: str = "gofix") -> None:
	"""Idempotent — safe to run on every ``after_migrate``."""

	changed = False
	for path in workspace_fixtures(app):
		changed = _sync_fixture(path, app) or changed

	if changed:
		frappe.clear_cache()

	warn_on_unknown_filter_fields(app)


def workspace_fixtures(app: str) -> list[str]:
	"""Every ``<module>/workspace/<name>/<name>.json`` shipped by ``app``."""

	return sorted(glob(os.path.join(frappe.get_app_path(app), "*", "workspace", "*", "*.json")))


def _sync_fixture(path: str, app: str) -> bool:
	"""Force-import ``path`` if its content changed since the last sync."""

	key = HASH_KEY_PREFIX + os.path.relpath(path, frappe.get_app_path(app))
	digest = _file_hash(path)
	if frappe.db.get_default(key) == digest:
		return False

	name = _fixture_docname(path)
	if not name or not frappe.db.exists("Workspace", name):
		# Nothing to force: with no DB row to compare timestamps against,
		# Frappe's own importer already picks a new fixture up on migrate.
		# Record the hash so the first real edit is what triggers a sync.
		frappe.db.set_default(key, digest)
		return False

	# ``import_file_by_path(force=True)`` deletes and re-inserts the doc, and in
	# developer_mode ``Workspace.after_delete`` rmtree's the fixture folder —
	# ``delete_doc`` runs ``after_delete`` even for ``for_reload=True``, while
	# ``export_to_files`` refuses to write the folder back because ``in_import``
	# is set.  Run outside migrate that silently deletes the source file we are
	# importing.  ``in_fixtures`` is one of the flags ``disable_saving_as_public()``
	# checks, so setting it short-circuits both delete paths.
	previous = frappe.flags.in_fixtures
	frappe.flags.in_fixtures = True
	try:
		import_file_by_path(path, force=True)
	finally:
		frappe.flags.in_fixtures = previous

	# Only recorded on success, so a failed import retries on the next migrate.
	frappe.db.set_default(key, digest)
	print(f"Synced Workspace {name} from {os.path.relpath(path, frappe.get_app_path(app))}")
	return True


def warn_on_unknown_filter_fields(app: str) -> None:
	"""Report shortcut ``stats_filter`` keys that are not fields of their DocType.

	Such a filter is never validated at save or import time, but every count
	query the workspace fires on load goes through ``frappe.desk.reportview``,
	which throws ``Field not permitted in query: <field>`` — a blocking dialog
	for every user who opens the page.  Non-fatal here on purpose: a bad
	fixture should not abort a migration.
	"""

	for path in workspace_fixtures(app):
		name = _fixture_docname(path)
		if not name or not frappe.db.exists("Workspace", name):
			continue

		for shortcut in frappe.get_doc("Workspace", name).shortcuts:
			for fieldname in _filter_fieldnames(shortcut):
				if _is_queryable(shortcut.link_to, fieldname):
					continue

				frappe.log_error(
					title="Workspace shortcut filters an unknown field",
					message=(
						f"Workspace {name}, shortcut {shortcut.label!r} filters "
						f"{shortcut.link_to} on {fieldname!r}, which is not a field of "
						f"that DocType. Opening the workspace will fail with "
						f"'Field not permitted in query: {fieldname}'."
					),
				)
				print(
					f"WARNING: Workspace {name} shortcut {shortcut.label!r} filters "
					f"{shortcut.link_to} on unknown field {fieldname!r}"
				)


def _filter_fieldnames(shortcut) -> list[str]:
	"""Fieldnames referenced by a shortcut's ``stats_filter``, either form."""

	if shortcut.type != "DocType" or not shortcut.stats_filter:
		return []

	try:
		parsed = json.loads(shortcut.stats_filter)
	except ValueError:
		return []

	if isinstance(parsed, dict):
		return list(parsed)

	# List form: [fieldname, operator, value] or [doctype, fieldname, operator, value]
	return [c[1] if len(c) == 4 else c[0] for c in parsed if isinstance(c, list | tuple) and c]


def _is_queryable(doctype: str, fieldname: str) -> bool:
	if fieldname in default_fields or fieldname in optional_fields:
		return True

	if not doctype or not frappe.db.exists("DocType", doctype):
		return False

	return bool(frappe.get_meta(doctype).get_field(fieldname))


def _fixture_docname(path: str) -> str | None:
	try:
		with open(path) as fixture:
			return json.load(fixture).get("name")
	except (OSError, ValueError):
		return None


def _file_hash(path: str) -> str:
	digest = hashlib.md5(usedforsecurity=False)
	with open(path, "rb") as fixture:
		for chunk in iter(lambda: fixture.read(8192), b""):
			digest.update(chunk)
	return digest.hexdigest()
