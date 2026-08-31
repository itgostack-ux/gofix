"""Repair the Service Order workflow condition that crashed every save.

The "Complete Job" transition tested ``frappe.db.count(...) > 0``. Workflow
conditions run through ``frappe.safe_eval`` with the globals from
``frappe.model.workflow.get_workflow_safe_globals``, which exposes only
``frappe.db.get_value`` and ``frappe.db.get_list``. Because ``frappe.db``
there is a ``frappe._dict``, the missing ``count`` resolved to ``None``
instead of raising AttributeError, so the condition evaluated ``None(...)``
and every Sales Order save that processed workflow actions died with
``TypeError: 'NoneType' object is not callable``.

``get_list`` is permission-checked, so it would silently read 0 for a user
without Job Assignment access and hide the transition. ``get_value`` is the
permission-free equivalent and is what the condition now uses.
"""

import frappe

OLD = (
	"frappe.db.count('Job Assignment', {'service_order': doc.name, "
	"'assignment_status': ['in', ['Completed', 'Closed']]}) > 0"
)
NEW = (
	"frappe.db.get_value('Job Assignment', {'service_order': doc.name, "
	"'assignment_status': ['in', ['Completed', 'Closed']]}, 'name') is not None"
)


def execute():
	if not frappe.db.table_exists("Workflow Transition"):
		return

	rows = frappe.get_all(
		"Workflow Transition",
		filters={"condition": ("like", "%frappe.db.count(%Job Assignment%")},
		fields=["name", "parent", "condition"],
	)
	fixed = 0
	for row in rows:
		if OLD not in (row.condition or ""):
			# A different db.count condition — report it rather than rewriting
			# something this patch was not written to understand.
			frappe.log_error(
				title="Unconverted workflow db.count condition",
				message=f"{row.parent} / {row.name}: {row.condition}",
			)
			continue
		frappe.db.set_value(
			"Workflow Transition", row.name, "condition",
			row.condition.replace(OLD, NEW), update_modified=False,
		)
		fixed += 1

	if fixed:
		frappe.clear_cache()
		print(f"v10: repaired {fixed} workflow transition condition(s)")
