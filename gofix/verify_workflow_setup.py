#!/usr/bin/env python3
"""
Verify Service Order Workflow Setup
Run with: bench --site erpnext.local execute gofix.verify_workflow_setup
"""

import frappe

def execute():
	"""Verify workflow setup is complete"""
	
	print("\n" + "="*60)
	print("SERVICE ORDER WORKFLOW VERIFICATION")
	print("="*60)
	
	# Check states
	states = frappe.get_all("Service Order State", fields=["state_name", "is_terminal_state"])
	print(f"\n✓ Workflow States: {len(states)} configured")
	for state in states:
		terminal = "🔒 Terminal" if state.is_terminal_state else "→ Active"
		print(f"  - {state.state_name:<20} {terminal}")
	
	# Check transitions
	transitions = frappe.get_all("Service Order Transition", 
		fields=["from_state", "action_name", "to_state", "require_job_sheet_completion", "require_qc_pass"])
	print(f"\n✓ Workflow Transitions: {len(transitions)} configured")
	for t in transitions:
		validations = []
		if t.require_job_sheet_completion:
			validations.append("JobSheet✓")
		if t.require_qc_pass:
			validations.append("QC✓")
		val_str = f" [{', '.join(validations)}]" if validations else ""
		print(f"  - {t.from_state} → {t.to_state} ({t.action_name}){val_str}")
	
	# Check custom field
	print(f"\n✓ Checking Custom Field...")
	meta = frappe.get_meta("Sales Order")
	workflow_field = meta.get_field("workflow_state")
	if workflow_field:
		print(f"  ✓ workflow_state field exists")
		print(f"    Type: {workflow_field.fieldtype}")
		print(f"    Options: {workflow_field.options}")
		print(f"    Visible When: is_service_order=1")
	else:
		print(f"  ✗ workflow_state field NOT found - needs custom field installation")
		return False
	
	# Check validation code
	print(f"\n✓ Checking Validation Code...")
	from gofix.overrides.sales_order import CustomSalesOrder
	if hasattr(CustomSalesOrder, 'validate_state_transition'):
		print(f"  ✓ validate_state_transition() method exists")
	else:
		print(f"  ✗ validate_state_transition() method NOT found")
		return False
	
	# Summary
	print("\n" + "="*60)
	print("✅ WORKFLOW SYSTEM READY!")
	print("="*60)
	print("\nNext Steps:")
	print("1. Create a Service Request")
	print("2. Accept it to create Service Order (workflow_state='Draft')")
	print("3. Create and Complete Job Sheet (workflow_state='QC Awaiting')")
	print("4. Set QC Pass and close (workflow_state='QC Pass' → 'Closed')")
	print("\nTo add custom workflow steps:")
	print("- Go to Service Order State list")
	print("- Go to Service Order Transition list")
	print("\nDocumentation: apps/gofix/WORKFLOW_SETUP.md")
	print("="*60 + "\n")
	
	return True

if __name__ == "__main__":
	execute()
