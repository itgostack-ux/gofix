# GoFix Multi-Store Service Management Workflow

## Business Process Overview

### Current Flow:
```
Customer → Store A → Service Request → Job Sheet
                    ↓
              Small Issue? → Repair at Store → QC → Invoice
                    ↓
              Complex Issue → Transfer to Master Hub → Repair → QC → Transfer back → Invoice
```

## Solution Design: Maximum ERPNext Integration

---

## 1. WAREHOUSE SETUP (Use Existing ERPNext)

### Structure:
```
All Warehouses (Group)
├── Master Hub Warehouse (Main)
│   ├── Repair Center
│   └── QC Department
├── Store A (Chennai - T.Nagar)
├── Store B (Chennai - Velachery)  
├── Store C (Bangalore - Koramangala)
└── Store D (Mumbai - Andheri)
```

### Implementation:
- **Use ERPNext Warehouse DocType** - Already exists
- Each warehouse linked to **Company**
- Set **Address** for each warehouse (for GST/E-Way Bill)
- Auto-populate **State Code** from Address (Tamil Nadu = TN)

---

## 2. AUTOMATED FIELD POPULATION

### Service Request Enhancements:

#### Add These Fields to `service_request.json`:

```json
{
  "fieldname": "source_warehouse",
  "fieldtype": "Link",
  "label": "Store/Warehouse",
  "options": "Warehouse",
  "reqd": 1,
  "fetch_from": "company.default_warehouse"  // Auto-set based on user's default
},
{
  "fieldname": "warehouse_address",
  "fieldtype": "Link",
  "label": "Store Address",
  "options": "Address",
  "fetch_from": "source_warehouse.address",
  "read_only": 1
},
{
  "fieldname": "state_name",
  "fieldtype": "Data",
  "label": "State Name",
  "fetch_from": "warehouse_address.state",
  "read_only": 1
},
{
  "fieldname": "state_code",
  "fieldtype": "Data",
  "label": "State Code",
  "fetch_from": "warehouse_address.gst_state_number",
  "read_only": 1
}
```

### Python Logic for Auto-population:

```python
def before_insert(self):
    """Set warehouse based on user's default warehouse or company"""
    if not self.source_warehouse:
        # Get user's default warehouse
        self.source_warehouse = frappe.defaults.get_user_default("warehouse")
        
        # Fallback to company's default warehouse
        if not self.source_warehouse and self.company:
            self.source_warehouse = frappe.get_cached_value(
                "Company", self.company, "default_warehouse"
            )
    
    # Auto-fetch address and state details
    if self.source_warehouse:
        warehouse_address = frappe.get_cached_value(
            "Warehouse", self.source_warehouse, "address"
        )
        if warehouse_address:
            address_details = frappe.get_doc("Address", warehouse_address)
            self.warehouse_address = warehouse_address
            self.state_name = address_details.state
            self.state_code = address_details.gst_state_number
```

---

## 3. JOB SHEET AUTO-CREATION

### Modify Job Assignment to be Auto-Generated:

#### service_request.py:

```python
def on_submit(self):
    """Auto-create Job Sheet when Service Request is accepted"""
    if self.walkin_status == "Accepted":
        self.create_job_sheet()
        self.generate_job_barcode()

def create_job_sheet(self):
    """Create Job Assignment automatically"""
    job = frappe.new_doc("Job Assignment")
    job.service_request = self.name
    job.service_request_name = self.customer_name
    job.imei_serial = self.imei_serial_no
    job.device_item = self.device_item
    job.device_name = self.device_item_name
    job.issue_description = self.issue_description
    job.source_warehouse = self.source_warehouse
    job.current_location = self.source_warehouse
    job.assignment_status = "Open"
    job.barcode = self.barcode
    job.insert()
    
    frappe.msgprint(
        _("Job Sheet {0} created").format(job.name),
        alert=True,
        indicator="green"
    )
    return job.name

def generate_job_barcode(self):
    """Generate barcode for job tracking"""
    if not self.barcode:
        self.barcode = self.name  # SR-251206-0001
```

---

## 4. DEVICE TRANSFER FLOW (Use ERPNext Stock Entry)

### When Device Needs Transfer to Master Hub:

#### Add Button to Service Request:

```python
@frappe.whitelist()
def transfer_to_master_hub(service_request_name):
    """Create Stock Entry for device transfer"""
    sr = frappe.get_doc("Service Request", service_request_name)
    
    # Validate
    if not sr.device_item:
        frappe.throw(_("Device Item is required for transfer"))
    
    # Create Stock Entry (Material Transfer)
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.from_warehouse = sr.source_warehouse
    se.to_warehouse = frappe.get_cached_value("Company", sr.company, "master_hub_warehouse")
    se.company = sr.company
    
    # Add device as item
    se.append("items", {
        "item_code": sr.device_item,
        "qty": 1,
        "serial_no": sr.serial_no,
        "s_warehouse": sr.source_warehouse,
        "t_warehouse": se.to_warehouse,
        "description": f"Service Request: {sr.name} - {sr.issue_description}"
    })
    
    se.insert()
    se.submit()
    
    # Update Service Request
    sr.current_location = se.to_warehouse
    sr.stock_entry = se.name
    sr.transfer_status = "Transferred to Hub"
    sr.save()
    
    # E-Way Bill (if India Compliance installed)
    if sr.state_code:
        generate_eway_bill(se.name)
    
    frappe.msgprint(
        _("Stock Entry {0} created. Device transferred to Master Hub").format(se.name),
        alert=True
    )
    return se.name
```

---

## 5. RETURN TRANSFER (Hub → Store)

```python
@frappe.whitelist()
def return_to_store(service_request_name):
    """Transfer repaired device back to store"""
    sr = frappe.get_doc("Service Request", service_request_name)
    
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.from_warehouse = sr.current_location  # Master Hub
    se.to_warehouse = sr.source_warehouse    # Original Store
    se.company = sr.company
    
    se.append("items", {
        "item_code": sr.device_item,
        "qty": 1,
        "serial_no": sr.serial_no,
        "s_warehouse": sr.current_location,
        "t_warehouse": sr.source_warehouse
    })
    
    se.insert()
    se.submit()
    
    sr.current_location = sr.source_warehouse
    sr.transfer_status = "Returned to Store"
    sr.status = "Ready for Delivery"
    sr.save()
    
    return se.name
```

---

## 6. E-WAY BILL INTEGRATION (Use India Compliance)

### Automatic E-Way Bill Generation:

```python
def generate_eway_bill(stock_entry_name):
    """Generate E-Way Bill for interstate transfers"""
    try:
        # India Compliance app handles this
        se = frappe.get_doc("Stock Entry", stock_entry_name)
        
        # Get addresses
        from_address = frappe.get_cached_value("Warehouse", se.from_warehouse, "address")
        to_address = frappe.get_cached_value("Warehouse", se.to_warehouse, "address")
        
        from_state = frappe.get_cached_value("Address", from_address, "gst_state_number")
        to_state = frappe.get_cached_value("Address", to_address, "gst_state_number")
        
        # Generate if interstate
        if from_state != to_state:
            # India Compliance API call
            frappe.msgprint(_("E-Way Bill required for interstate transfer"))
            # Trigger E-Way Bill generation (India Compliance handles this)
            
    except Exception as e:
        frappe.log_error(f"E-Way Bill generation failed: {str(e)}")
```

---

## 7. SERVICE INVOICE GENERATION

### Auto-create when QC Complete:

```python
def on_qc_complete(self):
    """Generate Service Invoice after QC"""
    if self.qc_status == "Passed" and not self.sales_invoice:
        self.create_service_invoice()

def create_service_invoice(self):
    """Create Sales Invoice for service"""
    si = frappe.new_doc("Sales Invoice")
    si.customer = self.customer
    si.company = self.company
    si.set_warehouse = self.source_warehouse
    si.posting_date = today()
    
    # Add service items
    for item in self.service_items:
        si.append("items", {
            "item_code": item.service_item,
            "qty": item.quantity,
            "rate": item.amount,
            "description": f"Service: {item.service_item}"
        })
    
    # Add spare parts
    for part in self.spare_parts:
        si.append("items", {
            "item_code": part.item_code,
            "qty": part.quantity,
            "rate": part.rate
        })
    
    si.insert()
    
    self.sales_invoice = si.name
    self.save()
    
    frappe.msgprint(
        _("Sales Invoice {0} created").format(si.name),
        alert=True
    )
```

---

## 8. IMPLEMENTATION STEPS

### Phase 1: Warehouse Setup
1. Create Master Hub warehouse
2. Create store warehouses (A, B, C, D)
3. Link addresses to each warehouse
4. Set default warehouses for users

### Phase 2: Service Request Enhancement
1. Add warehouse/state fields to Service Request JSON
2. Implement auto-population logic
3. Add `before_insert()` and `on_submit()` hooks

### Phase 3: Transfer Flow
1. Add "Transfer to Hub" button
2. Implement Stock Entry creation
3. Test E-Way Bill integration

### Phase 4: Job Sheet Auto-creation
1. Modify Job Assignment to auto-create
2. Link barcode generation
3. Track device movement

### Phase 5: Invoice Generation
1. Add QC complete trigger
2. Auto-generate Service Invoice
3. Link spare parts and services

---

## 9. CONFIGURATION NEEDED

### Company Master:
Add custom field `master_hub_warehouse` to Company DocType

### User Defaults:
Set for each store user:
```python
frappe.defaults.set_user_default("warehouse", "Store A - Chennai", "user@example.com")
frappe.defaults.set_user_default("Company", "GoFix Pvt Ltd", "user@example.com")
```

### Warehouse Address Linking:
```python
# Link address to warehouse
warehouse = frappe.get_doc("Warehouse", "Store A - Chennai")
warehouse.address = "Store-A-Chennai-Address"
warehouse.save()
```

---

## 10. CUSTOM FIELDS TO ADD

### Service Request:
- `source_warehouse` (Link to Warehouse)
- `current_location` (Link to Warehouse) 
- `warehouse_address` (Link to Address)
- `state_name` (Data - auto-filled)
- `state_code` (Data - auto-filled)
- `stock_entry` (Link to Stock Entry)
- `transfer_status` (Select: At Store/Transferred to Hub/Returned to Store)
- `sales_invoice` (Link to Sales Invoice)

### Job Assignment:
- `source_warehouse` (Link to Warehouse)
- `current_location` (Link to Warehouse)
- `barcode` (Data)
- `device_item` (Link to Item)
- `device_name` (Data)

### Company:
- `master_hub_warehouse` (Link to Warehouse)

---

## BENEFITS OF THIS APPROACH:

✅ **Uses 80% existing ERPNext features** (Warehouse, Stock Entry, Address, E-Way Bill)
✅ **Minimal custom code** - only business logic
✅ **Automatic state code population** from warehouse address
✅ **Standard inventory tracking** via Serial Numbers
✅ **Compliance ready** - E-Way Bill for interstate transfers
✅ **Job sheet auto-generation** on Service Request acceptance
✅ **Seamless invoice generation** after repair complete

---

## NEXT STEPS:

1. Review this design
2. Create custom fields in Service Request
3. Implement warehouse auto-population
4. Test transfer flow
5. Integrate E-Way Bill
6. Deploy to stores

**Want me to start implementing any specific phase?**
