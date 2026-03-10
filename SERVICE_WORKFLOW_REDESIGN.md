# GoFix Service Workflow Redesign

## Executive Summary
Redesigning GoFix service management to split the large Service Request form into a streamlined 2-step workflow:
1. **Service Request** - Initial customer intake (lightweight, quick acceptance/rejection)
2. **Service Order** - Detailed service execution with Job Sheet management

This design leverages ERPNext's built-in functionality while adding minimal custom fields.

---

## Current vs Proposed Workflow

### Current Workflow (Single Form)
```
Service Request (ALL-IN-ONE)
├── Customer Details
├── Device Information
├── Issue Description
├── Warranty Information
├── Estimated Cost
├── Delivery Details
├── Spare Parts
├── Service Items
├── Job Assignment
└── Billing
```

### Proposed Workflow (2-Step)
```
STEP 1: Service Request (Lightweight Intake)
├── Customer arrives at store / books online / bot
├── Minimal information collection
├── Quick Accept/Reject decision
└── If Accepted → Create Service Order

STEP 2: Service Order (Detailed Execution)
├── Detailed planning & costing
├── Estimated delivery date
├── Auto-create Job Sheet (replaces Job Assignment)
├── Technician assignment & tracking
├── Spare parts addition/removal
├── QC verification
├── Close Job Sheet
├── Billing & Invoice
└── Transfer to other stores
```

---

## ERPNext DocTypes Leveraged

### 1. **Sales Order** (as Service Order base)
**Why**: ERPNext's Sales Order already handles:
- Customer linking
- Item/Service lines with pricing
- Payment terms
- Workflow states (Draft → Confirmed → Completed → Closed)
- Billing integration (auto-create Sales Invoice)
- Multi-location support via warehouse

**Custom Fields to Add**:
- `service_request` (Link to Service Request)
- `device_information` section (IMEI, brand, model, issue)
- `estimated_delivery_date`
- `actual_delivery_date`
- `device_condition` (like product_condition_desc)
- `technician_notes`
- `qc_status` (Select: Pending/Passed/Failed)
- `qc_checked_by` (Link to User)
- `qc_datetime`

### 2. **Work Order** (optional reference, not directly used)
**Why Not Use**: Work Order is for manufacturing with BOM. Service is simpler - we use Job Sheet instead.

### 3. **Job Card** (inspiration for Job Sheet)
**Why Reference**: Job Card tracks operation-level work. Our Job Sheet is similar but service-focused.

**Our Approach**: Enhance existing **Job Assignment** DocType and rename to **Job Sheet** OR create new lightweight Job Sheet.

### 4. **Stock Entry** (for spare parts & transfers)
**Already Available**: Use ERPNext's Stock Entry for:
- Material consumption (spare parts used)
- Store-to-store transfers
- Material requests

### 5. **Sales Invoice** (for billing)
**Already Available**: Auto-create from Service Order using ERPNext's built-in `make_sales_invoice()` function.

---

## Detailed Workflow Design

### Phase 1: Service Request (Intake & Acceptance)

#### Purpose
Quick customer intake at store/online. Capture minimum info to decide Accept/Reject.

#### Fields (Minimal)
**Customer Section**:
- Customer (Link) *required*
- Customer Name (fetch_from)
- Contact Number
- Email

**Device Section**:
- Brand (Link to Brand)
- Model
- IMEI/Serial No
- Device Condition (Physical inspection notes)

**Issue Section**:
- Issue Category (Link to Issue Category master)
- Issue Specified By Customer (Text)
- Symptoms Checklist (Table: Screen damage, battery, etc.)

**Store Section**:
- Source Warehouse (Link to Warehouse) *auto-populate from user default*
- Current Location (Link to Warehouse) *same as source initially*
- Walk-in Source (Link to Walk-in Source master)

**Intake Section**:
- Service Date (Date) *auto: today*
- Received DateTime (Datetime) *auto: now*
- Received By (Link to User) *auto: current user*

**Estimate & Decision**:
- Estimated Cost (Currency)
- Customer Remarks
- Internal Remarks
- Decision (Select: **Draft / Accepted / Rejected / Cancelled**)

#### Workflow States
1. **Draft** - Just created
2. **Accepted** - Service approved, ready to create Service Order
3. **Rejected** - Customer declined / not feasible
4. **Cancelled** - Customer cancelled after acceptance

#### Auto-Actions
- On **Accept**: Auto-create Service Order (Sales Order) with linked Service Request
- On **Reject/Cancel**: Close Service Request, no further action

---

### Phase 2: Service Order (Detailed Execution)

#### DocType: Sales Order (with Custom Fields)

#### Auto-Creation from Service Request
When Service Request is **Accepted**, create Sales Order with:
- Customer from Service Request
- Title: "Service - {Customer Name} - {IMEI}"
- Order Type: "Service Order"
- Custom fields populated from Service Request

#### Fields (Detailed)

**Reference Section** (Custom):
- `service_request` (Link) *auto-populated*
- `service_request_status` (read-only, fetch_from)

**Device Information** (Custom Section):
- `device_brand` (Link)
- `device_model` (Data)
- `imei_serial_no` (Data)
- `device_condition` (Long Text)
- `issue_category` (Link)
- `issue_description` (Long Text)
- `password_pattern` (Small Text) - security info
- `backup_status` (Small Text)

**Service Planning** (Custom Section):
- `estimated_delivery_date` (Date)
- `actual_delivery_date` (Date)
- `priority` (Select: Low/Medium/High/Urgent)
- `warranty_status` (Select: In Warranty/Out of Warranty)
- `warranty_provider` (Link to Supplier)

**Items** (Standard Sales Order):
- Service Items (e.g., "Screen Replacement", "Software Repair")
- Spare Parts (e.g., "Battery", "Display")
- Both with Rate, Qty, Amount

**Quality Control** (Custom Section):
- `qc_status` (Select: Pending/In Progress/Passed/Failed)
- `qc_checked_by` (Link to User)
- `qc_datetime` (Datetime)
- `qc_remarks` (Text)

**Delivery** (Custom Section):
- `delivery_mode` (Select: Pickup/Courier/Hand Delivery)
- `courier_name` (Data)
- `tracking_number` (Data)
- `delivered_datetime` (Datetime)

**Warehouse** (Standard + Custom):
- `set_warehouse` (Standard field) - source warehouse
- `current_location` (Custom) - where device is now

#### Workflow States (ERPNext Standard + Custom)
1. **Draft** - Just created from Service Request
2. **Confirmed** - Ready for Job Sheet creation
3. **In Progress** - Job Sheet(s) created, work ongoing
4. **QC Pending** - Work complete, needs QC
5. **QC Failed** - Needs rework
6. **Completed** - QC passed, ready for billing
7. **Billed** - Invoice generated
8. **Delivered** - Device delivered to customer
9. **Closed** - Fully closed

#### Auto-Actions
- On **Confirm**: Auto-create Job Sheet
- On **Completed**: Enable "Create Invoice" button
- On **Close**: Update Service Request status

---

### Phase 3: Job Sheet (Enhanced Job Assignment)

#### Approach
**Option A**: Enhance existing Job Assignment DocType
**Option B**: Create new Job Sheet DocType (cleaner)

**Recommendation**: Enhance Job Assignment and rename to "Job Sheet"

#### Fields

**Reference**:
- `service_order` (Link to Sales Order)
- `service_request` (Link to Service Request) *fetch_from service_order*
- `customer` (fetch_from)
- `imei_serial` (fetch_from)

**Assignment**:
- `job_sheet_date` (Date) *auto: today*
- `job_sheet_datetime` (Datetime) *auto: now*
- `assigned_by` (User) *auto: current user*
- `assigned_to` (Link to User/Employee) *Service Engineer*
- `team` (Link to Employee Group)

**Job Details**:
- `job_type` (Select: Repair/Diagnosis/QC/Spare Parts Replacement)
- `job_status` (Select: **Open / In Progress / On Hold / Completed / Cancelled**)
- `priority` (Select: Low/Medium/High/Urgent)
- `estimated_hours` (Float)
- `actual_hours` (Float)

**Execution**:
- `start_datetime` (Datetime)
- `end_datetime` (Datetime)
- `technician_remarks` (Long Text)
- `work_performed` (Long Text)

**Spare Parts Usage** (Child Table):
- Item Code
- Item Name
- Qty Used
- Rate
- Amount
- Stock Entry Reference (Link to Stock Entry)

**Time Logs** (Child Table):
- Employee
- Start Time
- End Time
- Hours
- Activity (Select: Diagnosis/Repair/Testing/Waiting for Parts)

**Barcode**:
- `barcode` (Barcode field) - auto-generated from Job Sheet ID

#### Workflow States
1. **Open** - Created, not started
2. **In Progress** - Technician working
3. **On Hold** - Waiting for parts/customer approval
4. **Completed** - Work finished
5. **Cancelled** - Job cancelled

#### Auto-Actions
- On **Create**: Generate barcode, auto-assign to default technician if configured
- On **Start**: Set start_datetime, status = In Progress
- On **Complete**: Set end_datetime, calculate actual_hours, update Service Order progress
- Spare Parts Added: Auto-create Stock Entry (Material Issue)

---

### Phase 4: Multi-Technician Support

#### Scenario
Complex repairs need multiple technicians (e.g., hardware + software).

#### Approach
- Service Order can have **multiple Job Sheets**
- Each Job Sheet assigned to different technician/team
- Service Order status tracks overall progress

#### Example
```
Service Order: SO-2024-001
├── Job Sheet 1: Hardware Diagnosis (Tech A) - Completed
├── Job Sheet 2: Screen Replacement (Tech B) - In Progress
└── Job Sheet 3: Software Update (Tech C) - Open
```

---

### Phase 5: QC & Closure

#### QC Process
1. Technician completes Job Sheet → Status = Completed
2. All Job Sheets completed → Service Order = QC Pending
3. QC Engineer opens Service Order, performs checks
4. Update QC fields:
   - `qc_status` = Passed/Failed
   - `qc_checked_by` = Current User
   - `qc_datetime` = Now
   - `qc_remarks` = Inspection notes
5. If **Passed**: Service Order → Completed (ready for billing)
6. If **Failed**: Create new Job Sheet for rework

---

### Phase 6: Billing & Invoice

#### Process
1. Service Order status = Completed
2. Admin clicks "Create Invoice" button (ERPNext standard)
3. ERPNext auto-creates **Sales Invoice** with:
   - All service items from Service Order
   - All spare parts from Job Sheets
   - Customer payment terms
4. Submit Sales Invoice
5. Service Order status → Billed

#### Integration
Use ERPNext's built-in `make_sales_invoice()` function from Sales Order.

---

### Phase 7: Device Transfer (Store to Store)

#### Scenario
Device needs transfer from Store A → Master Hub → Store B for delivery.

#### Integration with Stock Entry
1. Create **Stock Entry** (Material Transfer)
2. Item: Device/Dummy Item (SKU: DEVICE-TRANSFER)
3. Source Warehouse: Current Location
4. Target Warehouse: Destination
5. Reference: Service Order number
6. Update Service Order `current_location` field

#### E-Way Bill Integration
- If interstate transfer, auto-generate E-Way Bill
- Use India Compliance app's E-Way Bill functionality
- Trigger based on `state_code` mismatch

---

## Implementation Phases

### Phase 1: Service Request Redesign (Week 1)
**Tasks**:
1. Simplify Service Request DocType
   - Remove detailed fields (move to Service Order)
   - Keep only intake fields
   - Add Decision field (Draft/Accepted/Rejected)
2. Add workflow: Draft → Accepted/Rejected
3. Create server script to auto-create Service Order on Accept
4. Update UI for quick acceptance

**Files to Modify**:
- `service_request.json` - simplify fields
- `service_request.py` - add `on_update_after_submit()` for Accept action
- `service_request.js` - add "Accept" and "Reject" buttons

### Phase 2: Service Order Creation (Week 2)
**Tasks**:
1. Add custom fields to Sales Order DocType
   - Device information section
   - Service Request link
   - QC section
   - Delivery section
2. Create custom DocType link: "Service Order" (child of Sales Order)
3. Create server script: `make_service_order(service_request)`
4. Add custom print format for Service Order

**Files to Create/Modify**:
- `gofix/fixtures/custom_field.json` - Sales Order custom fields
- `gofix/gofix_services/doctype/service_order/` - wrapper if needed
- `gofix/utils/service_order_utils.py` - helper functions

### Phase 3: Job Sheet Enhancement (Week 3)
**Tasks**:
1. Decide: Enhance Job Assignment OR create new Job Sheet
2. Add Service Order link
3. Add job tracking fields
4. Add spare parts usage child table
5. Add time logs child table
6. Integrate barcode generation
7. Create custom print format

**Files to Modify**:
- `job_assignment.json` → `job_sheet.json` (if renaming)
- `job_assignment.py` → `job_sheet.py`
- Add auto-creation from Service Order

### Phase 4: Multi-Technician & Stock Integration (Week 4)
**Tasks**:
1. Enable multiple Job Sheets per Service Order
2. Integrate Stock Entry for spare parts
3. Auto-create Stock Entry when spare parts added
4. Track inventory consumption
5. Update Service Order cost automatically

**Files to Create**:
- `gofix/utils/stock_integration.py`
- Server scripts for Stock Entry creation

### Phase 5: QC & Billing Workflow (Week 5)
**Tasks**:
1. Add QC workflow to Service Order
2. Create QC checklist form
3. Enable invoice creation button
4. Integrate with ERPNext Sales Invoice
5. Update Service Order status tracking

**Files to Modify**:
- `sales_order.js` - custom QC form
- Server script: QC validation

### Phase 6: Store Transfer & E-Way Bill (Week 6)
**Tasks**:
1. Create device transfer functionality
2. Integrate with Stock Entry
3. Auto-generate E-Way Bill for interstate
4. Update current_location tracking
5. Add transfer history to Service Order

**Files to Create**:
- `gofix/utils/device_transfer.py`
- E-Way Bill integration script

---

## Database Schema Changes

### Service Request (Simplified)
```python
# Remove these fields (move to Service Order):
- service_items (Table)
- spare_parts (Table)
- estimated_delivery_date
- actual_delivery_date
- delivery_mode, courier_name, tracking_number
- warranty_provider, warranty_expiry

# Add these fields:
- decision (Select: Draft/Accepted/Rejected/Cancelled)
- rejection_reason (Text) - if rejected
- accepted_by (User)
- accepted_datetime (Datetime)
```

### Sales Order (Custom Fields)
```python
# Section: Service Order Details
- service_request (Link to Service Request)
- service_request_status (Data, read-only)

# Section: Device Information
- device_brand (Link to Brand)
- device_model (Data)
- imei_serial_no (Data)
- device_condition (Long Text)
- issue_category (Link to Issue Category)
- issue_description (Long Text)
- password_pattern (Small Text)
- backup_status (Small Text)

# Section: Service Planning
- estimated_delivery_date (Date)
- actual_delivery_date (Date)
- priority (Select)
- warranty_status (Select)
- warranty_provider (Link to Supplier)

# Section: Quality Control
- qc_status (Select)
- qc_checked_by (Link to User)
- qc_datetime (Datetime)
- qc_remarks (Text)

# Section: Delivery
- delivery_mode (Select)
- courier_name (Data)
- tracking_number (Data)
- delivered_datetime (Datetime)

# Custom field
- current_location (Link to Warehouse)
```

### Job Sheet (Enhanced Job Assignment)
```python
# Add these fields:
- service_order (Link to Sales Order)
- job_type (Select)
- priority (Select)
- estimated_hours (Float)
- actual_hours (Float)
- start_datetime (Datetime)
- end_datetime (Datetime)
- work_performed (Long Text)

# Child Tables:
- spare_parts_used (Table)
  - item_code
  - item_name
  - qty_used
  - rate
  - amount
  - stock_entry_ref

- time_logs (Table)
  - employee
  - start_time
  - end_time
  - hours
  - activity
```

---

## UI/UX Improvements

### Service Request Form
- **Lightweight, single-page form**
- Grouped sections: Customer → Device → Issue → Decision
- Big buttons: **Accept** (green) / **Reject** (red)
- Auto-save draft every 30 seconds

### Service Order Dashboard
- Card view of all Service Orders
- Filters: Status, Priority, Store, Technician
- Quick stats: Total Open, In Progress, QC Pending, Completed
- Search by IMEI, Customer, Service Request ID

### Job Sheet View
- Kanban board: Open | In Progress | On Hold | Completed
- Drag-and-drop to change status
- Barcode scanner integration for tracking
- Timer for time log tracking

---

## Benefits of This Approach

### 1. Leverages ERPNext Built-ins
- ✅ Sales Order workflow & states
- ✅ Billing via Sales Invoice
- ✅ Stock management via Stock Entry
- ✅ Multi-location support
- ✅ Payment terms & partial payments
- ✅ Print formats & customization

### 2. Simplified User Experience
- ✅ Quick intake at store (Service Request)
- ✅ Detailed planning separately (Service Order)
- ✅ Technician focused view (Job Sheet)
- ✅ Clear separation of concerns

### 3. Scalability
- ✅ Multiple Job Sheets per Service Order
- ✅ Multi-store transfers
- ✅ Spare parts inventory tracking
- ✅ Time tracking & costing

### 4. Minimal Custom Code
- ✅ Mostly custom fields, not new DocTypes
- ✅ Reuse ERPNext functions
- ✅ Standard workflows
- ✅ Easier upgrades

---

## Next Steps

1. **Review & Approve Design** - Get stakeholder sign-off
2. **Phase 1 Implementation** - Simplify Service Request
3. **Prototype Service Order** - Add custom fields to Sales Order
4. **Test Workflow** - End-to-end testing
5. **Roll Out Gradually** - Store by store

---

## Questions to Resolve

1. **Job Sheet**: Enhance existing Job Assignment or create new DocType?
2. **Service Order**: Use Sales Order directly or create wrapper DocType?
3. **Stock Item for Devices**: Create dummy item "DEVICE-TRANSFER" for transfers?
4. **Barcode**: Continue using Service Request barcode or Job Sheet barcode for tracking?
5. **Permissions**: Role-based access (Store Staff, Technician, QC, Admin)?

---

## Appendix: ERPNext Functions to Use

### Sales Order Functions
```python
# Auto-create invoice
frappe.model.mapper.make_mapped_doc("Sales Order", service_order_name, {
    "Sales Order": {
        "doctype": "Sales Invoice",
        # field mappings
    }
})

# Get Sales Order
so = frappe.get_doc("Sales Order", name)
so.status = "Completed"
so.save()
```

### Stock Entry Functions
```python
# Create material transfer
se = frappe.new_doc("Stock Entry")
se.stock_entry_type = "Material Transfer"
se.from_warehouse = source_warehouse
se.to_warehouse = target_warehouse
se.append("items", {
    "item_code": item_code,
    "qty": qty,
    "s_warehouse": source_warehouse,
    "t_warehouse": target_warehouse
})
se.insert()
se.submit()
```

### Workflow Functions
```python
# Apply workflow action
doc.apply_workflow(action="Accept")

# Get workflow state
workflow_state = frappe.get_value("Service Request", name, "workflow_state")
```

---

**Document Version**: 1.0  
**Last Updated**: December 8, 2025  
**Status**: Draft - Pending Approval
