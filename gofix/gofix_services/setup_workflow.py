#!/usr/bin/env python3
"""
Service Order Workflow Setup Script
====================================
This script sets up the complete workflow system for Service Orders (Sales Orders with is_service_order=1).

Usage:
    bench --site erpnext.local execute gofix.gofix_services.setup_workflow.setup_service_order_workflow

What it does:
1. Creates workflow states (Draft, Approved, In Progress, etc.)
2. Creates workflow transitions (state changes with actions)
3. Creates the Workflow document linking everything together
4. Activates the workflow for use

Author: GoFix Team
Date: December 2025
"""

import frappe
from frappe import _


def setup_service_order_workflow():
    """Main setup function for Service Order workflow"""
    print("\n" + "=" * 70)
    print("Service Order Workflow Setup")
    print("=" * 70 + "\n")
    
    try:
        # Step 0: Ensure required custom roles
        print("Step 0: Ensuring custom roles...")
        ensure_custom_roles()

        # Step 1: Create workflow states
        print("Step 1: Creating Workflow States...")
        create_workflow_states()
        
        # Step 2: Create workflow transitions
        print("\nStep 2: Creating Workflow Transitions...")
        create_workflow_transitions()
        
        # Step 3: Ensure workflow_state field exists
        print("\nStep 3: Verifying Custom Field...")
        ensure_workflow_field()
        
        # Step 4: Create or update the workflow document
        print("\nStep 4: Creating Workflow Document...")
        create_workflow_document()
        
        print("\n" + "=" * 70)
        print("✓ Service Order Workflow setup completed successfully!")
        print("=" * 70 + "\n")
        
        print("Next Steps:")
        print("1. Navigate to: Setup → Workflow → Service Order Workflow")
        print("2. Review the workflow states and transitions")
        print("3. Test by creating a new Sales Order with is_service_order=1")
        print()
        
    except Exception as e:
        print(f"\n✗ Error during setup: {str(e)}")
        import traceback
        traceback.print_exc()
        frappe.db.rollback()
        raise


def _ensure_workflow_state_and_actions():
    """Create the global Frappe Workflow State and Workflow Action Master
    rows required by the Service Order Workflow.

    These are global registries shared across all workflows in Frappe; the
    Workflow Document State.state and Workflow Transition.action fields
    are Link-typed and Workflow.save() validates them up-front.
    """
    workflow_states = [
        ("Draft", "Primary"),
        ("Pending Approval", "Warning"),
        ("GoGizmo Head Approval", "Warning"),
        ("Approved", "Success"),
        ("In Progress", "Info"),
        ("Completed", "Success"),
        ("Cancelled", "Danger"),
    ]
    for state_name, style in workflow_states:
        if not frappe.db.exists("Workflow State", state_name):
            frappe.get_doc({
                "doctype": "Workflow State",
                "workflow_state_name": state_name,
                "style": style,
            }).insert(ignore_permissions=True)

    workflow_actions = [
        "Submit for Approval", "Cancel", "Approve", "Reject",
        "Escalate to GoGizmo Head", "Final Approve", "Send Back",
        "Start Work", "Complete",
    ]
    for action_name in workflow_actions:
        if not frappe.db.exists("Workflow Action Master", action_name):
            frappe.get_doc({
                "doctype": "Workflow Action Master",
                "workflow_action_name": action_name,
            }).insert(ignore_permissions=True)
    frappe.db.commit()


def ensure_custom_roles():
    """Idempotently create custom roles required by the workflow.

    Currently registers:
      - GoGizmo Head — final approver for escalated Service Orders.
    """
    roles = ["GoGizmo Head"]
    for role_name in roles:
        if frappe.db.exists("Role", role_name):
            print(f"  ○ Role exists: {role_name}")
            continue
        role = frappe.get_doc({
            "doctype": "Role",
            "role_name": role_name,
            "desk_access": 1,
            "is_custom": 1,
        })
        role.insert(ignore_permissions=True)
        print(f"  ✓ Created role: {role_name}")
    frappe.db.commit()


def create_workflow_states():
    """Create the workflow states"""
    states = [
        {"state_name": "Draft"},
        {"state_name": "Pending Approval"},
        {"state_name": "GoGizmo Head Approval"},
        {"state_name": "Approved"},
        {"state_name": "In Progress"},
        {"state_name": "Completed"},
        {"state_name": "Cancelled"}
    ]
    
    created = 0
    exists = 0
    
    for state_data in states:
        state_name = state_data["state_name"]
        existing = frappe.db.get_value(
            "Service Order State", {"state_name": state_name}, "name"
        )
        if existing:
            # The doctype now autonames by `field:state_name`; legacy rows
            # may still carry random hashes. Normalise so Service Order
            # Transition Links resolve correctly.
            if existing != state_name:
                frappe.rename_doc(
                    "Service Order State", existing, state_name, force=True
                )
                print(f"  ↻ Renamed state '{existing}' → '{state_name}'")
            else:
                print(f"  ○ State exists: {state_name}")
            exists += 1
        else:
            state = frappe.get_doc({
                "doctype": "Service Order State",
                **state_data
            })
            state.insert(ignore_permissions=True)
            print(f"  ✓ Created state: {state_name}")
            created += 1
    
    frappe.db.commit()
    print(f"  → {created} created, {exists} already existed")


def create_workflow_transitions():
    """Create the workflow transitions"""
    transitions = [
        # From Draft
        {
            "from_state": "Draft",
            "to_state": "Pending Approval",
            "action": "Submit for Approval",
            "allowed_role": "Sales User"
        },
        # From Pending Approval
        {
            "from_state": "Pending Approval",
            "to_state": "Approved",
            "action": "Approve",
            "allowed_role": "Sales Manager"
        },
        # GoGizmo Head escalation gate (Phase J #22): high-value or
        # exception-flagged orders take a second approval hop after the
        # Sales Manager queue. The Sales Manager initiates the escalation;
        # only GoGizmo Head can finalise the approval/rejection.
        {
            "from_state": "Pending Approval",
            "to_state": "GoGizmo Head Approval",
            "action": "Escalate to GoGizmo Head",
            "allowed_role": "Sales Manager"
        },
        {
            "from_state": "GoGizmo Head Approval",
            "to_state": "Approved",
            "action": "Final Approve",
            "allowed_role": "GoGizmo Head"
        },
        {
            "from_state": "GoGizmo Head Approval",
            "to_state": "Draft",
            "action": "Send Back",
            "allowed_role": "GoGizmo Head"
        },
        {
            "from_state": "Pending Approval",
            "to_state": "Draft",
            "action": "Reject",
            "allowed_role": "Sales Manager"
        },
        # From Approved (post-submit)
        {
            "from_state": "Approved",
            "to_state": "In Progress",
            "action": "Start Work",
            "allowed_role": "Sales User"
        },
        {
            "from_state": "Approved",
            "to_state": "Cancelled",
            "action": "Cancel",
            "allowed_role": "Sales Manager"
        },
        # From In Progress
        {
            "from_state": "In Progress",
            "to_state": "Completed",
            "action": "Complete",
            "allowed_role": "Sales User"
        }
    ]
    
    created = 0
    exists = 0
    
    for trans_data in transitions:
        # Check if transition already exists
        filters = {
            "from_state": trans_data["from_state"],
            "to_state": trans_data["to_state"],
            "action": trans_data["action"]
        }
        
        if frappe.db.exists("Service Order Transition", filters):
            print(f"  ○ Transition exists: {trans_data['from_state']} → {trans_data['to_state']}")
            exists += 1
        else:
            transition = frappe.get_doc({
                "doctype": "Service Order Transition",
                **trans_data
            })
            transition.insert(ignore_permissions=True)
            print(f"  ✓ Created: {trans_data['from_state']} → {trans_data['to_state']} ({trans_data['action']})")
            created += 1
    
    frappe.db.commit()
    print(f"  → {created} created, {exists} already existed")


def ensure_workflow_field():
    """Ensure the workflow_state custom field exists on Sales Order"""
    field_name = "Sales Order-workflow_state"
    
    if frappe.db.exists("Custom Field", field_name):
        print(f"  ✓ workflow_state field already exists on Sales Order")
        return
    
    custom_field = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Sales Order",
        "label": "Workflow State",
        "fieldname": "workflow_state",
        "fieldtype": "Link",
        "options": "Service Order State",
        "insert_after": "status",
        "read_only": 1,
        "allow_on_submit": 1,
        "translatable": 0,
        "print_hide": 0,
        "in_standard_filter": 1
    })
    custom_field.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"  ✓ Created workflow_state field on Sales Order")


def create_workflow_document():
    """Create or update the Workflow document"""
    workflow_name = "Service Order Workflow"
    
    # Check if workflow exists
    if frappe.db.exists("Workflow", workflow_name):
        print(f"  ○ Workflow '{workflow_name}' already exists")
        workflow = frappe.get_doc("Workflow", workflow_name)
        
        # Update it to be active and correct
        workflow.is_active = 1
        workflow.document_type = "Sales Order"
        workflow.workflow_state_field = "workflow_state"
        
        # Clear and rebuild states/transitions
        workflow.states = []
        workflow.transitions = []
        
        add_workflow_states_and_transitions(workflow)
        workflow.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"  ✓ Updated and activated workflow: {workflow_name}")
        return
    
    # Create new workflow
    workflow = frappe.get_doc({
        "doctype": "Workflow",
        "workflow_name": workflow_name,
        "document_type": "Sales Order",
        "workflow_state_field": "workflow_state",
        "is_active": 1,
        "send_email_alert": 0,
        "override_status": 0,
        "states": [],
        "transitions": []
    })
    
    add_workflow_states_and_transitions(workflow)
    workflow.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"  ✓ Created workflow: {workflow_name}")


def add_workflow_states_and_transitions(workflow):
    """Add states and transitions to workflow document"""

    # Pre-create the global Frappe Workflow State / Workflow Action Master
    # rows that are referenced by Workflow Document State.state and
    # Workflow Transition.action — both are Link fields and the Workflow
    # save() validates them eagerly.
    _ensure_workflow_state_and_actions()
    
    # Define workflow states with their properties
    state_config = {
        "Draft": {
            "doc_status": "0",
            "allow_edit": "Sales User",
            "state_style": "Primary"
        },
        "Pending Approval": {
            "doc_status": "0",
            "allow_edit": "Sales Manager",
            "state_style": "Warning"
        },
        "GoGizmo Head Approval": {
            "doc_status": "0",
            "allow_edit": "GoGizmo Head",
            "state_style": "Warning"
        },
        "Approved": {
            "doc_status": "1",
            "allow_edit": "Sales Manager",
            "state_style": "Success"
        },
        "In Progress": {
            "doc_status": "1",
            "allow_edit": "Sales User",
            "state_style": "Info"
        },
        "Completed": {
            "doc_status": "1",
            "allow_edit": "Sales Manager",
            "state_style": "Success"
        },
        "Cancelled": {
            "doc_status": "2",
            "allow_edit": "Sales Manager",
            "state_style": "Danger"
        }
    }
    
    # Add states
    for state_name, config in state_config.items():
        workflow.append("states", {
            "state": state_name,
            "doc_status": config["doc_status"],
            "allow_edit": config["allow_edit"],
            "is_optional_state": 0,
            "next_action_email_template": None,
            "update_field": None,
            "update_value": None
        })
    
    # Get all transitions
    transitions = frappe.get_all(
        "Service Order Transition",
        fields=["from_state", "to_state", "action", "allowed_role"],
        order_by="from_state, to_state"
    )
    
    # Add transitions
    for trans in transitions:
        workflow.append("transitions", {
            "state": trans["from_state"],
            "action": trans["action"],
            "next_state": trans["to_state"],
            "allowed": trans["allowed_role"],
            "allow_self_approval": 0,
            "condition": None
        })


if __name__ == "__main__":
    setup_service_order_workflow()
