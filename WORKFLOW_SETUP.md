# Service Order Workflow - Quick Setup Guide

## 1. Create DocTypes in Database
```bash
cd /home/palla/erpnext-bench
bench --site erpnext.local migrate
```
**Expected Output:** Tables created for Service Order State and Service Order Transition

---

## 2. Install workflow_state Custom Field
```bash
bench --site erpnext.local execute gofix.setup.sales_order_custom_fields.create_sales_order_custom_fields
```
**Expected Output:** Custom field 'workflow_state' added to Sales Order

---

## 3. Load Default Workflow (9 States, 11 Transitions)
```bash
bench --site erpnext.local execute gofix.setup_service_order_workflow.execute
```
**Expected Output:**
```
Creating Service Order States...
✓ Created state: Draft
✓ Created state: Submitted
✓ Created state: Work in Progress
✓ Created state: QC Awaiting
✓ Created state: QC Pass
✓ Created state: QC Fail
✓ Created state: Not Repairable
✓ Created state: Customer Cancelled
✓ Created state: Closed

Creating Service Order Transitions...
✓ Created transition: Draft → Submitted (Submit)
✓ Created transition: Submitted → Work in Progress (Start Work)
✓ Created transition: Work in Progress → QC Awaiting (Complete Job)
✓ Created transition: Work in Progress → Not Repairable (Mark Not Repairable)
✓ Created transition: Work in Progress → Customer Cancelled (Cancel by Customer)
✓ Created transition: QC Awaiting → QC Pass (QC Pass)
✓ Created transition: QC Awaiting → QC Fail (QC Fail)
✓ Created transition: QC Fail → Work in Progress (Rework)
✓ Created transition: QC Pass → Closed (Close Service Order)
✓ Created transition: Not Repairable → Closed (Close)
✓ Created transition: Customer Cancelled → Closed (Close)

✓ Service Order workflow setup complete!
```

---

## 4. Restart Services
```bash
bench restart
```

---

## 5. Test the Workflow

### 5.1 Create Service Order
1. Go to Service Request
2. Fill mandatory fields (estimated_cost, expected_completion_date)
3. Accept Service Request → Creates Sales Order
4. Check: workflow_state should be "Draft"

### 5.2 Create and Complete Job Sheet
1. Open Service Order
2. Create Job Sheet
3. Assign to technician
4. Complete Job Sheet
5. Check: workflow_state should be "QC Awaiting"

### 5.3 Perform QC
1. Open Service Order
2. Set qc_status to "Pass"
3. Change workflow_state to "QC Pass"
4. Should allow (transition configured)

### 5.4 Close Service Order
1. Open Service Order
2. Change workflow_state to "Closed"
3. Should allow (QC Pass → Closed transition exists)

---

## 6. Verify Configuration

### View States
```bash
bench --site erpnext.local mariadb -e "SELECT state_name, is_terminal_state FROM \`tabService Order State\`;"
```

### View Transitions
```bash
bench --site erpnext.local mariadb -e "SELECT from_state, action_name, to_state FROM \`tabService Order Transition\`;"
```

---

## 7. Add Custom State (Example)

### Via ERPNext UI:
1. Go to: **Service Order State** list
2. Click **New**
3. Fill:
   - State Name: `QC In Progress`
   - Description: `QC inspection currently being performed`
   - Allow Edit Roles: `QC Inspector, Service Manager, System Manager`
   - Is Terminal State: **Unchecked**
4. Save

### Add Transitions:
1. Go to: **Service Order Transition** list
2. Create: `QC Awaiting → QC In Progress`
   - From State: QC Awaiting
   - Action Name: Start QC
   - To State: QC In Progress
   - Allowed Roles: QC Inspector, Service Manager, System Manager
   - Condition Script: `doc.docstatus == 1`
3. Create: `QC In Progress → QC Pass`
   - From State: QC In Progress
   - Action Name: Complete QC - Pass
   - To State: QC Pass
   - Allowed Roles: QC Inspector, Service Manager, System Manager
   - Condition Script: `doc.docstatus == 1 and doc.qc_status == 'Pass'`
   - Require QC Pass: **Checked**

---

## 8. Troubleshooting

### Check Logs
```bash
bench --site erpnext.local console
```
```python
frappe.get_all("Error Log", limit=10, fields=["*"], order_by="creation desc")
```

### Verify Field Exists
```bash
bench --site erpnext.local mariadb -e "DESCRIBE \`tabSales Order\`;" | grep workflow_state
```

### Check Transitions for State
```python
# In bench console
frappe.get_all("Service Order Transition", 
    filters={"from_state": "Work in Progress"}, 
    fields=["*"])
```

### Reset Workflow (if needed)
```bash
bench --site erpnext.local mariadb -e "DELETE FROM \`tabService Order Transition\`;"
bench --site erpnext.local mariadb -e "DELETE FROM \`tabService Order State\`;"
bench --site erpnext.local execute gofix.setup_service_order_workflow.execute
```

---

## 9. Common Issues

### "Invalid transition" Error
- **Cause**: No transition configured from current state to new state
- **Fix**: Add transition via Service Order Transition list

### "User not allowed" Error
- **Cause**: User role not in transition's allowed_roles
- **Fix**: Add user's role to transition's Allowed Roles field

### "Condition not met" Error
- **Cause**: Condition script evaluated to False
- **Fix**: Check condition script logic or update document fields

### Workflow state not visible
- **Cause**: is_service_order not set
- **Fix**: Set is_service_order=1 on Sales Order

---

## 10. Key Files Reference

| File | Purpose |
|------|---------|
| `/apps/gofix/SERVICE_ORDER_WORKFLOW.md` | Complete documentation |
| `/apps/gofix/WORKFLOW_IMPLEMENTATION.md` | Implementation summary |
| `/apps/gofix/gofix/setup_service_order_workflow.py` | Setup script |
| `/apps/gofix/gofix/overrides/sales_order.py` | Validation logic |
| `/apps/gofix/gofix/setup/sales_order_custom_fields.py` | Custom fields |
| `/apps/gofix/gofix/gofix_services/doctype/service_order_state/` | State DocType |
| `/apps/gofix/gofix/gofix_services/doctype/service_order_transition/` | Transition DocType |

---

## Default Workflow Diagram

```
         [Draft]
            |
         Submit
            ↓
       [Submitted]
            |
       Start Work
            ↓
   [Work in Progress]
       /    |    \
      /     |     \
Complete   Mark   Cancel
   Job      Not    by
            Repair Customer
   /         |        \
  ↓          ↓         ↓
[QC      [Not      [Customer
Awaiting] Repairable] Cancelled]
 /  \        |           |
QC   QC    Close       Close
Pass Fail    ↓           ↓
 ↓    ↓    [Closed]    [Closed]
[QC  [QC
Pass] Fail]
  |    |
Close Rework
  ↓    ↓
[Closed] [Work in Progress]
```

---

## Status: ✅ Ready for Testing

All files created, setup script ready, documentation complete.
Run commands 1-4 above to activate the workflow system.
