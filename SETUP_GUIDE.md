# GoFix App - Setup Complete! ✅

## Current Status
- ✅ App installed and built
- ✅ Module configured (GoFix Services)
- ✅ Desktop icon configured
- ✅ Workspace structure created

## Next Steps - Create DocTypes

### 1. Start the Bench
```bash
cd /home/palla/erpnext-bench
source env/bin/activate
bench start
```

### 2. Access ERPNext
Open browser: http://localhost:8000

### 3. Create the following DocTypes via UI

#### A. Service Request
**Path:** Developer > DocType > New DocType

**Fields:**
- naming_series: SR-.YYYY.-.####
- customer: Link to Customer (ERPNext)
- customer_name: Data (fetch from Customer)
- item_to_repair: Link to Item (ERPNext)
- item_name: Data (fetch from Item)
- serial_no: Link to Serial No (optional)
- issue_description: Text Editor
- received_date: Date
- expected_delivery: Date
- priority: Select (Low, Medium, High, Urgent)
- status: Select (Draft, Pending, In Progress, Completed, Delivered, Cancelled)
- assigned_to: Link to User

**Settings:**
- Module: GoFix Services
- Is Submittable: Yes
- Track Changes: Yes

#### B. Repair Job
**Path:** Developer > DocType > New DocType

**Fields:**
- naming_series: RJ-.YYYY.-.####
- service_request: Link to Service Request
- customer: Link to Customer (fetch from Service Request)
- item: Link to Item (fetch from Service Request)
- technician: Link to User
- start_date: Datetime
- end_date: Datetime
- diagnosis: Text Editor
- repair_notes: Text Editor
- labor_charges: Currency
- status: Select (Not Started, In Progress, On Hold, Completed)

**Child Table: Spare Parts Used**
- item_code: Link to Item
- item_name: Data (fetch)
- qty: Float
- rate: Currency
- amount: Currency

**Settings:**
- Module: GoFix Services
- Is Submittable: Yes

#### C. Warranty Registration
**Path:** Developer > DocType > New DocType

**Fields:**
- naming_series: WR-.YYYY.-.####
- customer: Link to Customer
- item: Link to Item
- serial_no: Link to Serial No
- purchase_date: Date
- warranty_start_date: Date
- warranty_end_date: Date
- warranty_period_months: Int
- warranty_type: Select (Standard, Extended, Premium)
- terms_and_conditions: Text Editor
- status: Select (Active, Expired, Claimed, Cancelled)

**Settings:**
- Module: GoFix Services
- Is Submittable: Yes
- Track Changes: Yes

#### D. Warranty Claim
**Path:** Developer > DocType > New DocType

**Fields:**
- naming_series: WC-.YYYY.-.####
- warranty_registration: Link to Warranty Registration
- customer: Link to Customer (fetch)
- item: Link to Item (fetch)
- claim_date: Date
- issue_description: Text Editor
- claim_status: Select (Submitted, Under Review, Approved, Rejected, Completed)
- approval_date: Date
- rejection_reason: Text
- service_request: Link to Service Request (after creating SR for this claim)

**Settings:**
- Module: GoFix Services
- Is Submittable: Yes
- Track Changes: Yes

## CLI Alternative (Faster)

You can also create DocTypes using bench console:

```bash
cd /home/palla/erpnext-bench
source env/bin/activate

# Create Service Request DocType
bench --site erpnext.local console
```

Then in Python console:
```python
import frappe

# Create Service Request DocType
doc = frappe.get_doc({
    "doctype": "DocType",
    "name": "Service Request",
    "module": "GoFix Services",
    "custom": 0,
    "istable": 0,
    "is_submittable": 1,
    "track_changes": 1,
    "fields": [
        {"fieldname": "customer", "fieldtype": "Link", "label": "Customer", "options": "Customer", "reqd": 1},
        {"fieldname": "customer_name", "fieldtype": "Data", "label": "Customer Name", "fetch_from": "customer.customer_name", "read_only": 1},
        # Add more fields...
    ]
})
doc.insert()
```

## After Creating DocTypes

1. Run migration:
```bash
bench --site erpnext.local migrate
```

2. Clear cache:
```bash
bench --site erpnext.local clear-cache
```

3. Rebuild assets:
```bash
bench build
```

## Integration with ERPNext

### Creating Service Invoice
In Repair Job doctype, add a button to create Sales Invoice:
- Add custom button in JavaScript client script
- Fetch spare parts and labor charges
- Create Sales Invoice linked to customer

### Accounting Integration
Sales Invoice automatically creates accounting entries:
- Debit: Accounts Receivable
- Credit: Service Income

## File Structure Created
```
gofix/
├── gofix/
│   ├── gofix_services/              # Module
│   │   ├── doctype/                 # DocTypes will be here
│   │   └── workspace/
│   │       └── gofix/
│   │           └── gofix.json       # Workspace config
│   ├── config/
│   │   └── desktop.py               # Desktop icon
│   └── hooks.py                     # App hooks
```

## What's Configured

**hooks.py additions needed:**
```python
# Add to /home/palla/erpnext-bench/apps/gofix/gofix/hooks.py

# Fixtures to export workspace
fixtures = [
    {"dt": "Workspace", "filters": [["name", "in", ["GoFix"]]]},
]
```

Access GoFix from: **Sidebar > GoFix** (after creating DocTypes)
