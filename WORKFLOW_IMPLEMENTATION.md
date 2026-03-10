# Service Order Data-Driven Workflow - Implementation Summary

## What Was Done

Converted the hardcoded Service Order workflow validation into a **data-driven, UI-configurable system** that allows workflow modifications without code changes.

## Files Created

### 1. New DocTypes

**Service Order State** (`/apps/gofix/gofix/gofix_services/doctype/service_order_state/`)
- Defines available workflow states
- Fields: state_name, description, allow_edit_roles, is_terminal_state, update_field, update_value
- Permissions: System Manager (full), Service Manager (read)

**Service Order Transition** (`/apps/gofix/gofix/gofix_services/doctype/service_order_transition/`)
- Defines allowed transitions between states
- Fields: from_state, action_name, to_state, allowed_roles, condition_script, require_job_sheet_completion, require_qc_pass, allow_if_repair_outcome
- Permissions: System Manager (full), Service Manager (read)

### 2. Setup Script

**`/apps/gofix/gofix/setup_service_order_workflow.py`**
- Populates default workflow with 9 states and 11 transitions
- Run with: `bench --site erpnext.local execute gofix.setup_service_order_workflow.execute`

### 3. Documentation

**`/apps/gofix/SERVICE_ORDER_WORKFLOW.md`**
- Complete guide to the workflow system
- Setup instructions
- How to add/modify states and transitions
- Troubleshooting guide

## Files Modified

### 1. Sales Order Overrides
**`/apps/gofix/gofix/overrides/sales_order.py`**

**Changed:**
- Replaced hardcoded `validate_service_order_status()` with data-driven validation
- Added `validate_state_transition()` method that:
  - Looks up transitions from database
  - Validates role permissions
  - Evaluates condition scripts
  - Checks Job Sheet completion
  - Checks QC pass requirements
  - Validates repair outcomes

**Before:**
```python
# Hardcoded logic
if old_doc.status != self.status:
    job_sheets = frappe.get_all(...)
    if not job_sheets:
        frappe.throw("...")
```

**After:**
```python
# Data-driven logic
if old_state != current_state:
    self.validate_state_transition(old_state, current_state)

def validate_state_transition(self, from_state, to_state):
    transitions = frappe.get_all("Service Order Transition", ...)
    # Validate based on configured rules
```

### 2. Custom Fields
**`/apps/gofix/gofix/setup/sales_order_custom_fields.py`**

**Added:**
- `workflow_state` field (Link to Service Order State)
- Only visible when `is_service_order=1`
- Shown in list view and standard filters
- Tracks current workflow state

### 3. Service Request
**`/apps/gofix/gofix/gofix_services/doctype/service_request/service_request.py`**

**Modified** `create_service_order()`:
```python
# Set initial workflow state
so.workflow_state = "Draft"
```

### 4. Job Assignment
**`/apps/gofix/gofix/gofix_services/doctype/job_assignment/job_assignment.py`**

**Modified** `update_service_order_status()`:
```python
# Set workflow state based on repair outcome
if self.repair_outcome == "Not Repairable":
    so.db_set("workflow_state", "Not Repairable", ...)
elif self.repair_outcome == "Customer Cancelled":
    so.db_set("workflow_state", "Customer Cancelled", ...)
else:
    so.db_set("workflow_state", "QC Awaiting", ...)
```

## Default Workflow Configuration

### States (9 total)
1. **Draft** - Initial state when SO created
2. **Submitted** - SO submitted and ready for work
3. **Work in Progress** - Technician working on device
4. **QC Awaiting** - Job completed, awaiting QC
5. **QC Pass** - Device passed QC inspection
6. **QC Fail** - Device failed QC, needs rework
7. **Not Repairable** - Device cannot be repaired (terminal)
8. **Customer Cancelled** - Customer cancelled service (terminal)
9. **Closed** - Service completed and delivered (terminal)

### Transitions (11 total)
1. Draft → Submitted (Submit)
2. Submitted → Work in Progress (Start Work)
3. Work in Progress → QC Awaiting (Complete Job)
4. Work in Progress → Not Repairable (Mark Not Repairable)
5. Work in Progress → Customer Cancelled (Cancel by Customer)
6. QC Awaiting → QC Pass (QC Pass)
7. QC Awaiting → QC Fail (QC Fail)
8. QC Fail → Work in Progress (Rework)
9. QC Pass → Closed (Close Service Order)
10. Not Repairable → Closed (Close)
11. Customer Cancelled → Closed (Close)

## How to Use

### For System Managers - Adding New States

1. Go to **Service Order State** DocType list
2. Create new state with:
   - Unique state name
   - Description
   - Allowed roles (comma-separated)
   - Terminal state flag
   - Auto-update field/value (optional)

### For System Managers - Adding New Transitions

1. Go to **Service Order Transition** DocType list
2. Create new transition with:
   - From State (existing state)
   - Action Name (display text)
   - To State (existing state)
   - Allowed Roles (comma-separated)
   - Condition Script (Python expression with `doc` variable)
   - Validation checkboxes (Job Sheet, QC, Repair Outcome)

### For Users - Working with Service Orders

The workflow automatically:
- Sets state to "Draft" when SO created from Service Request
- Changes to "QC Awaiting" when Job Sheet completed (if repairable)
- Changes to "Not Repairable" or "Customer Cancelled" based on repair_outcome
- Validates all state changes based on configured transitions

## Setup Steps

### 1. Migrate Database
```bash
cd /home/palla/erpnext-bench
bench --site erpnext.local migrate
```
This creates the Service Order State and Service Order Transition tables.

### 2. Install Custom Fields
```bash
bench --site erpnext.local execute gofix.setup.sales_order_custom_fields.create_sales_order_custom_fields
```
This adds the workflow_state field to Sales Order.

### 3. Populate Workflow Data
```bash
bench --site erpnext.local execute gofix.setup_service_order_workflow.execute
```
This creates the 9 default states and 11 transitions.

### 4. Restart Services
```bash
bench restart
```

## Key Benefits

1. **UI Configurable**: Workflow changes via forms, not code
2. **Conditional Logic**: Only affects Service Orders (is_service_order=1)
3. **Role-Based**: Different roles can perform different transitions
4. **Flexible Validation**: Multiple validation rules per transition
5. **Multiple Paths**: Multiple transitions can lead to same state
6. **Python Conditions**: Complex business logic via condition scripts
7. **No Code Changes**: Future workflow modifications without developer

## Validation Logic

When workflow_state changes, the system:
1. Finds all transitions from old_state to new_state
2. For each transition, checks:
   - User has allowed role?
   - Condition script passes?
   - Job Sheets completed? (if required)
   - QC passed? (if required)
   - Repair outcome matches? (if specified)
3. If ANY transition passes all checks → Allow
4. If NO transitions pass → Throw error

## Example: Adding "QC In Progress" State

### Step 1: Create State
```
State Name: QC In Progress
Description: QC inspection is currently being performed
Allow Edit Roles: QC Inspector, Service Manager, System Manager
Is Terminal State: No
Update Field: qc_status
Update Value: In Progress
```

### Step 2: Add Transitions

**From QC Awaiting:**
```
From State: QC Awaiting
Action Name: Start QC
To State: QC In Progress
Allowed Roles: QC Inspector, Service Manager, System Manager
Condition Script: doc.docstatus == 1
```

**To QC Pass:**
```
From State: QC In Progress
Action Name: Complete QC - Pass
To State: QC Pass
Allowed Roles: QC Inspector, Service Manager, System Manager
Condition Script: doc.docstatus == 1 and doc.qc_status == 'Pass'
Require QC Pass: Yes
```

**To QC Fail:**
```
From State: QC In Progress
Action Name: Complete QC - Fail
To State: QC Fail
Allowed Roles: QC Inspector, Service Manager, System Manager
Condition Script: doc.docstatus == 1 and doc.qc_status == 'Fail'
```

### Step 3: Test
1. Create Service Order
2. Complete Job Sheet → State changes to "QC Awaiting"
3. Change workflow_state to "QC In Progress" → Allowed
4. Set qc_status to "Pass" and workflow_state to "QC Pass" → Allowed
5. Try to change to random state → Blocked

## Technical Architecture

### Workflow State Storage
- **Field**: `workflow_state` (Link to Service Order State)
- **Location**: Sales Order custom field
- **Visibility**: Only when `is_service_order=1`
- **Default**: "Draft" on creation

### Validation Trigger
- **Hook**: `validate()` method in CustomSalesOrder
- **Condition**: When `workflow_state` changes
- **Action**: Call `validate_state_transition()`

### Automatic Updates
- **Service Request → Sales Order**: Set "Draft"
- **Job Sheet Completion (Repairable)**: Set "QC Awaiting"
- **Job Sheet Completion (Not Repairable)**: Set "Not Repairable"
- **Job Sheet Completion (Cancelled)**: Set "Customer Cancelled"

## Testing Checklist

- [ ] Migrate database successfully
- [ ] Install custom fields successfully
- [ ] Populate workflow data (9 states, 11 transitions created)
- [ ] Restart bench
- [ ] Create Service Request
- [ ] Accept SR → Creates SO with workflow_state="Draft"
- [ ] Submit SO → workflow_state stays "Draft" (no transition configured yet)
- [ ] Create Job Sheet
- [ ] Complete Job Sheet → SO workflow_state="QC Awaiting"
- [ ] Try to change state manually to invalid state → Error
- [ ] Change qc_status to "Pass" → Should allow workflow_state="QC Pass"
- [ ] Add custom state via UI
- [ ] Add custom transition via UI
- [ ] Test custom transition works

## Next Steps

1. **Run Migration**: Create DocTypes in database
2. **Populate Data**: Load default states and transitions
3. **Test Workflow**: Create Service Order and verify state changes
4. **Add UI Buttons**: Create workflow action buttons on Sales Order form
5. **Track History**: Add workflow history child table
6. **Email Alerts**: Configure notifications on state changes

## Troubleshooting

### Migration Fails
- Check if DocTypes already exist
- Verify JSON syntax in .json files
- Check bench logs: `bench --site erpnext.local console`

### Workflow Not Validating
- Check if is_service_order flag is set
- Verify workflow_state field exists on Sales Order
- Check if transitions are configured for the state change
- Verify user has allowed role

### State Not Updating Automatically
- Check Job Assignment code calls `db_set("workflow_state", ...)`
- Check Service Request sets initial "Draft" state
- Verify custom fields are installed

## Support Files

- **Main Documentation**: `/apps/gofix/SERVICE_ORDER_WORKFLOW.md`
- **Setup Script**: `/apps/gofix/gofix/setup_service_order_workflow.py`
- **Validation Code**: `/apps/gofix/gofix/overrides/sales_order.py`
- **State DocType**: `/apps/gofix/gofix/gofix_services/doctype/service_order_state/`
- **Transition DocType**: `/apps/gofix/gofix/gofix_services/doctype/service_order_transition/`

---

**Implementation Date**: December 2025  
**Version**: 1.0  
**Status**: Ready for testing
