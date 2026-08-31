import frappe


WORKFLOW_NAME = "Service Order Workflow"
SYSTEM_MANAGER_ROLE = "System Manager"

ROLE_NAMES = (
	"Service Manager",
	"Service Engineer",
	"Service Viewer",
)

WORKFLOW_STATE_DEFS = (
	{"name": "Draft", "style": "", "icon": "file"},
	{"name": "Submitted", "style": "Info", "icon": "plane"},
	{"name": "Work in Progress", "style": "Warning", "icon": "wrench"},
	{"name": "QC Awaiting", "style": "Warning", "icon": "time"},
	{"name": "QC Pass", "style": "Success", "icon": "ok-sign"},
	{"name": "QC Fail", "style": "Danger", "icon": "remove-sign"},
	{"name": "Not Repairable", "style": "Danger", "icon": "ban-circle"},
	{"name": "Customer Cancelled", "style": "Danger", "icon": "remove-circle"},
	{"name": "Closed", "style": "Success", "icon": "lock"},
)

WORKFLOW_ACTIONS = (
	"Submit",
	"Start Work",
	"Complete Job",
	"Mark Not Repairable",
	"Customer Cancelled",
	"QC Pass",
	"QC Fail",
	"Rework",
	"Close",
)

WORKFLOW_STATES = (
	{
		"state": "Draft",
		"doc_status": "0",
		"allow_edit": "All",
		"is_optional_state": 0,
		"update_field": None,
		"update_value": None,
	},
	{
		"state": "Submitted",
		"doc_status": "1",
		"allow_edit": "Service Manager",
		"is_optional_state": 0,
		"update_field": None,
		"update_value": None,
	},
	{
		"state": "Work in Progress",
		"doc_status": "1",
		"allow_edit": "All",
		"is_optional_state": 0,
		"update_field": "qc_status",
		"update_value": "Pending",
	},
	{
		"state": "QC Awaiting",
		"doc_status": "1",
		"allow_edit": "Service Manager",
		"is_optional_state": 0,
		"update_field": "qc_status",
		"update_value": "Awaiting",
	},
	{
		"state": "QC Pass",
		"doc_status": "1",
		"allow_edit": "Service Manager",
		"is_optional_state": 0,
		"update_field": "qc_status",
		"update_value": "Pass",
	},
	{
		"state": "QC Fail",
		"doc_status": "1",
		"allow_edit": "All",
		"is_optional_state": 1,
		"update_field": "qc_status",
		"update_value": "Fail",
	},
	{
		"state": "Not Repairable",
		"doc_status": "1",
		"allow_edit": "Service Manager",
		"is_optional_state": 1,
		"update_field": "repair_outcome",
		"update_value": "Not Repairable",
	},
	{
		"state": "Customer Cancelled",
		"doc_status": "1",
		"allow_edit": "Service Manager",
		"is_optional_state": 1,
		"update_field": "repair_outcome",
		"update_value": "Customer Cancelled",
	},
	{
		"state": "Closed",
		"doc_status": "1",
		"allow_edit": "Service Manager",
		"is_optional_state": 0,
		"update_field": None,
		"update_value": None,
	},
)

WORKFLOW_TRANSITIONS = (
	{
		"state": "Draft",
		"action": "Submit",
		"next_state": "Submitted",
		"allowed": "All",
		"condition": "",
	},
	{
		"state": "Submitted",
		"action": "Start Work",
		"next_state": "Work in Progress",
		"allowed": "Service Manager",
		"condition": "doc.is_service_order == 1",
	},
	{
		"state": "Submitted",
		"action": "Start Work",
		"next_state": "Work in Progress",
		"allowed": "Service Engineer",
		"condition": "doc.is_service_order == 1",
	},
	{
		"state": "Work in Progress",
		"action": "Complete Job",
		"next_state": "QC Awaiting",
		"allowed": "Service Manager",
		"condition": "frappe.db.get_value('Job Assignment', {'service_order': doc.name, 'assignment_status': ['in', ['Completed', 'Closed']]}, 'name') is not None",
	},
	{
		"state": "Work in Progress",
		"action": "Complete Job",
		"next_state": "QC Awaiting",
		"allowed": "Service Engineer",
		"condition": "frappe.db.get_value('Job Assignment', {'service_order': doc.name, 'assignment_status': ['in', ['Completed', 'Closed']]}, 'name') is not None",
	},
	{
		"state": "Work in Progress",
		"action": "Mark Not Repairable",
		"next_state": "Not Repairable",
		"allowed": "Service Manager",
		"condition": "doc.is_service_order == 1",
	},
	{
		"state": "Work in Progress",
		"action": "Mark Not Repairable",
		"next_state": "Not Repairable",
		"allowed": "Service Engineer",
		"condition": "doc.is_service_order == 1",
	},
	{
		"state": "Work in Progress",
		"action": "Customer Cancelled",
		"next_state": "Customer Cancelled",
		"allowed": "Service Manager",
		"condition": "doc.is_service_order == 1",
	},
	{
		"state": "QC Awaiting",
		"action": "QC Pass",
		"next_state": "QC Pass",
		"allowed": "Service Manager",
		"condition": "",
	},
	{
		"state": "QC Awaiting",
		"action": "QC Fail",
		"next_state": "QC Fail",
		"allowed": "Service Manager",
		"condition": "",
	},
	{
		"state": "QC Fail",
		"action": "Rework",
		"next_state": "Work in Progress",
		"allowed": "Service Manager",
		"condition": "",
	},
	{
		"state": "QC Pass",
		"action": "Close",
		"next_state": "Closed",
		"allowed": "Service Manager",
		"condition": "",
	},
	{
		"state": "Not Repairable",
		"action": "Close",
		"next_state": "Closed",
		"allowed": "Service Manager",
		"condition": "",
	},
	{
		"state": "Customer Cancelled",
		"action": "Close",
		"next_state": "Closed",
		"allowed": "Service Manager",
		"condition": "",
	},
)


def workflow_states_with_system_manager_parity():
	"""Return configured states plus one equivalent System Manager row per state/docstatus."""
	rows = [dict(state) for state in WORKFLOW_STATES]
	existing = {
		(state["state"], str(state["doc_status"]))
		for state in WORKFLOW_STATES
		if state["allow_edit"] == SYSTEM_MANAGER_ROLE
	}
	for state in WORKFLOW_STATES:
		key = (state["state"], str(state["doc_status"]))
		if key in existing:
			continue
		parity = dict(state)
		parity["allow_edit"] = SYSTEM_MANAGER_ROLE
		rows.append(parity)
		existing.add(key)
	return rows


def workflow_transitions_with_system_manager_parity():
	"""Return transitions plus one equivalent System Manager route per unique action path."""
	rows = [dict(transition) for transition in WORKFLOW_TRANSITIONS]
	existing = {
		(
			transition["state"],
			transition["action"],
			transition["next_state"],
			transition["condition"],
		)
		for transition in WORKFLOW_TRANSITIONS
		if transition["allowed"] == SYSTEM_MANAGER_ROLE
	}
	for transition in WORKFLOW_TRANSITIONS:
		key = (
			transition["state"],
			transition["action"],
			transition["next_state"],
			transition["condition"],
		)
		if key in existing:
			continue
		parity = dict(transition)
		parity["allowed"] = SYSTEM_MANAGER_ROLE
		rows.append(parity)
		existing.add(key)
	return rows


def ensure_service_order_workflow():
	"""Ensure GoFix workflow metadata is valid for Frappe's single-role workflow schema."""
	ensure_roles()
	ensure_workflow_states()
	ensure_workflow_actions()
	ensure_service_order_field_flags()
	ensure_workflow_state_field()
	ensure_workflow_document()
	frappe.clear_cache()


def ensure_roles():
	for role_name in ROLE_NAMES:
		if frappe.db.exists("Role", role_name):
			continue

		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1,
			}
		).insert(ignore_permissions=True)


def ensure_workflow_states():
	for state_def in WORKFLOW_STATE_DEFS:
		if frappe.db.exists("Workflow State", state_def["name"]):
			state = frappe.get_doc("Workflow State", state_def["name"])
			state.workflow_state_name = state_def["name"]
			state.style = state_def["style"]
			state.icon = state_def["icon"]
			state.save(ignore_permissions=True)
			continue

		frappe.get_doc(
			{
				"doctype": "Workflow State",
				"workflow_state_name": state_def["name"],
				"style": state_def["style"],
				"icon": state_def["icon"],
			}
		).insert(ignore_permissions=True)


def ensure_workflow_actions():
	for action_name in WORKFLOW_ACTIONS:
		if frappe.db.exists("Workflow Action Master", action_name):
			continue

		frappe.get_doc(
			{
				"doctype": "Workflow Action Master",
				"workflow_action_name": action_name,
			}
		).insert(ignore_permissions=True)


def ensure_service_order_field_flags():
	for fieldname in ("qc_status", "repair_outcome"):
		field = frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": fieldname})
		if not field:
			continue

		custom_field = frappe.get_doc("Custom Field", field)
		if custom_field.allow_on_submit:
			continue

		custom_field.allow_on_submit = 1
		custom_field.save(ignore_permissions=True)


def ensure_workflow_state_field():
	field_name = "Sales Order-workflow_state"
	if frappe.db.exists("Custom Field", field_name):
		return

	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": "Sales Order",
			"label": "Workflow State",
			"fieldname": "workflow_state",
			"fieldtype": "Link",
			"options": "Workflow State",
			"insert_after": "status",
			"read_only": 1,
			"allow_on_submit": 1,
			"in_standard_filter": 1,
		}
	).insert(ignore_permissions=True)


def ensure_workflow_document():
	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		workflow = frappe.get_doc("Workflow", WORKFLOW_NAME)
	else:
		workflow = frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": WORKFLOW_NAME,
				"document_type": "Sales Order",
				"workflow_state_field": "workflow_state",
				"is_active": 1,
				"send_email_alert": 0,
				"override_status": 0,
			}
		)

	workflow.document_type = "Sales Order"
	workflow.workflow_state_field = "workflow_state"
	workflow.is_active = 1
	workflow.send_email_alert = 0
	workflow.override_status = 0
	workflow.set("states", [])
	workflow.set("transitions", [])

	for state in workflow_states_with_system_manager_parity():
		workflow.append(
			"states",
			{
				"state": state["state"],
				"doc_status": state["doc_status"],
				"allow_edit": state["allow_edit"],
				"is_optional_state": state["is_optional_state"],
				"next_action_email_template": None,
				"update_field": state["update_field"],
				"update_value": state["update_value"],
			},
		)

	for transition in workflow_transitions_with_system_manager_parity():
		workflow.append(
			"transitions",
			{
				"state": transition["state"],
				"action": transition["action"],
				"next_state": transition["next_state"],
				"allowed": transition["allowed"],
				"allow_self_approval": 0,
				"send_email_to_creator": 0,
				"condition": transition["condition"],
			},
		)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)
