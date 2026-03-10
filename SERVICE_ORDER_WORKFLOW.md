# Service Order Workflow System

## Overview

The Service Order workflow system is a **data-driven, UI-configurable workflow engine** specifically designed for Service Orders (Sales Orders with `is_service_order=1`). Unlike ERPNext's standard Workflow feature which applies to ALL documents of a type, this system allows conditional workflow logic that only applies to Service Orders while leaving regular Sales Orders unaffected.

## Key Components

### 1. Service Order State DocType
Defines the available states in the workflow.

**Fields:**
- `state_name` - Unique name for the state (e.g., "Draft", "QC Awaiting")
- `description` - Description of what this state means
- `allow_edit_roles` - Comma-separated roles who can edit documents in this state
- `is_terminal_state` - Whether this is an end state (no transitions out)
- `update_field` - Field to auto-update when entering this state
- `update_value` - Value to set in that field

**Example States:**
- Draft
- Submitted
- Work in Progress
- QC Awaiting
- QC Pass
- QC Fail
- Not Repairable
- Customer Cancelled
- Closed

### 2. Service Order Transition DocType
Defines allowed transitions between states with validation rules.

**Fields:**
- `from_state` - Source state (Link to Service Order State)
- `action_name` - Display name for the transition
- `to_state` - Target state (Link to Service Order State)
- `allowed_roles` - Comma-separated roles who can perform this transition
- `condition_script` - Python expression that must evaluate to True (use `doc` variable)
- `require_job_sheet_completion` - Check box to require all Job Sheets completed
- `require_qc_pass` - Check box to require QC Pass status
- `allow_if_repair_outcome` - Comma-separated repair outcomes that allow this transition

**Example Transitions:**
```
Draft → Submitted (Submit)
  - Allowed Roles: Sales User, Service Manager, System Manager
  - Condition: doc.docstatus == 0

Submitted → Work in Progress (Start Work)
  - Allowed Roles: Service Manager, Technician, System Manager
  - Condition: doc.docstatus == 1

Work in Progress → QC Awaiting (Complete Job)
  - Allowed Roles: Service Manager, Technician, System Manager
  - Require Job Sheet Completion: Yes
  - Condition: doc.docstatus == 1

Work in Progress → Not Repairable (Mark Not Repairable)
  - Allowed Roles: Service Manager, System Manager
  - Allow If Repair Outcome: Not Repairable
  - Condition: doc.docstatus == 1

QC Awaiting → QC Pass (QC Pass)
  - Allowed Roles: QC Inspector, Service Manager, System Manager
  - Require QC Pass: Yes
  - Condition: doc.docstatus == 1

QC Pass → Closed (Close Service Order)
  - Allowed Roles: Service Manager, System Manager
  - Condition: doc.docstatus == 1
```

## How It Works

### 1. Workflow State Tracking
The `workflow_state` custom field on Sales Order tracks the current state. This field is only visible when `is_service_order=1`.

### 2. Validation Logic
When a Service Order's `workflow_state` changes, the `validate_state_transition()` method:

1. Looks up all transitions from the old state to the new state
2. For each transition, checks:
   - User has one of the allowed roles
   - Condition script evaluates to True
   - Job Sheet completion requirement met (if required)
   - QC pass requirement met (if required)
   - Repair outcome matches (if specified)
3. If at least one transition passes all checks, the state change is allowed
4. Otherwise, a validation error is thrown

### 3. Automatic State Updates
- **On Service Order Creation**: `workflow_state` set to "Draft"
- **On Job Sheet Completion**: 
  - If repair_outcome = "Not Repairable" → state = "Not Repairable"
  - If repair_outcome = "Customer Cancelled" → state = "Customer Cancelled"
  - Otherwise → state = "QC Awaiting"

## Setup Instructions

### 1. Migrate Database
Run bench migrate to create the new DocTypes:
```bash
bench --site erpnext.local migrate
```

### 2. Install Custom Fields
Run the custom fields setup if not already done:
```bash
bench --site erpnext.local execute gofix.setup.sales_order_custom_fields.create_sales_order_custom_fields
```

### 3. Populate Workflow Data
Load the default states and transitions:
```bash
bench --site erpnext.local execute gofix.setup_service_order_workflow.execute
```

This creates 9 default states and 11 transitions.

## Modifying the Workflow

### Adding a New State

1. Go to **Service Order State** list
2. Click **New**
3. Fill in:
   - State Name (e.g., "QC In Progress")
   - Description
   - Allow Edit Roles (comma-separated)
   - Is Terminal State (check if no transitions out)
   - Update Field and Value (optional auto-updates)
4. Save

### Adding a New Transition

1. Go to **Service Order Transition** list
2. Click **New**
3. Fill in:
   - From State (select existing state)
   - Action Name (e.g., "Start QC")
   - To State (select existing state)
   - Allowed Roles (comma-separated)
   - Condition Script (Python expression using `doc`)
   - Validation checkboxes (Job Sheet, QC, Repair Outcome)
4. Save

### Example: Adding QC In Progress State

**State:**
```
State Name: QC In Progress
Description: QC inspection is currently being performed
Allow Edit Roles: QC Inspector, Service Manager, System Manager
Is Terminal State: No
Update Field: qc_status
Update Value: In Progress
```

**Transitions:**
```
1. QC Awaiting → QC In Progress (Start QC)
   Allowed Roles: QC Inspector, Service Manager, System Manager
   Condition: doc.docstatus == 1

2. QC In Progress → QC Pass (Complete QC - Pass)
   Allowed Roles: QC Inspector, Service Manager, System Manager
   Condition: doc.docstatus == 1 and doc.qc_status == 'Pass'

3. QC In Progress → QC Fail (Complete QC - Fail)
   Allowed Roles: QC Inspector, Service Manager, System Manager
   Condition: doc.docstatus == 1 and doc.qc_status == 'Fail'
```

## Advantages Over Standard ERPNext Workflow

1. **Conditional Application**: Only affects Service Orders, not regular Sales Orders
2. **Flexible Validation**: Multiple validation rules per transition
3. **Role-Based Access**: Different roles can perform different transitions
4. **Dynamic Conditions**: Python expressions for complex business logic
5. **UI Configurable**: Add/modify states and transitions without code changes
6. **Multiple Paths**: Multiple transitions can lead to the same state with different conditions
7. **Integration Ready**: Works with Job Sheet completion and QC status

## Technical Details

### Files Modified/Created

**New DocTypes:**
- `/apps/gofix/gofix/gofix_services/doctype/service_order_state/`
- `/apps/gofix/gofix/gofix_services/doctype/service_order_transition/`

**Setup Script:**
- `/apps/gofix/gofix/setup_service_order_workflow.py`

**Modified Files:**
- `/apps/gofix/gofix/overrides/sales_order.py` - Added `validate_state_transition()`
- `/apps/gofix/gofix/setup/sales_order_custom_fields.py` - Added `workflow_state` field
- `/apps/gofix/gofix/gofix_services/doctype/service_request/service_request.py` - Initialize state on SO creation
- `/apps/gofix/gofix/gofix_services/doctype/job_assignment/job_assignment.py` - Update state on job completion

### Database Schema

**Service Order State:**
```sql
CREATE TABLE `tabService Order State` (
  name VARCHAR(140) PRIMARY KEY,
  state_name VARCHAR(140) UNIQUE NOT NULL,
  description TEXT,
  allow_edit_roles TEXT,
  is_terminal_state INT DEFAULT 0,
  update_field VARCHAR(140),
  update_value VARCHAR(140)
);
```

**Service Order Transition:**
```sql
CREATE TABLE `tabService Order Transition` (
  name VARCHAR(140) PRIMARY KEY,
  from_state VARCHAR(140) NOT NULL,
  action_name VARCHAR(140) NOT NULL,
  to_state VARCHAR(140) NOT NULL,
  allowed_roles TEXT,
  condition_script TEXT,
  require_job_sheet_completion INT DEFAULT 0,
  require_qc_pass INT DEFAULT 0,
  allow_if_repair_outcome TEXT
);
```

## Future Enhancements

1. **Workflow Actions**: Add buttons to Sales Order form for each available transition
2. **Workflow History**: Track state changes in a child table
3. **Email Notifications**: Trigger emails on state changes
4. **SLA Tracking**: Monitor time spent in each state
5. **State-Based Permissions**: Restrict field editing based on current state
6. **Workflow Visualization**: Diagram showing current state and available transitions

## Troubleshooting

### "Invalid transition" error
Check that:
1. Transition exists for the old state → new state
2. User has one of the allowed roles
3. Condition script evaluates to True
4. Job Sheet completion requirement met (if required)
5. QC requirement met (if required)

### Workflow state not updating automatically
Check that:
1. Job Assignment code is calling `db_set("workflow_state", ...)`
2. Service Request is setting initial state to "Draft"
3. Custom fields are installed (workflow_state field exists)

### Regular Sales Orders affected
Check that:
1. All workflow validation has `if self.is_service_order` check
2. Custom fields have `depends_on="eval:doc.is_service_order==1"`
3. workflow_state field is only shown for Service Orders

## Support

For issues or questions, check:
1. ERPNext logs: `bench --site erpnext.local console` → `frappe.get_all("Error Log", limit=5)`
2. State configuration: List → Service Order State
3. Transition configuration: List → Service Order Transition
4. Validation code: `/apps/gofix/gofix/overrides/sales_order.py`
