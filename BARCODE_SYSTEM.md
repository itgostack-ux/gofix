# GoFix Barcode Generation System

## Overview
The barcode generation system automatically creates unique barcodes for service requests based on the device category. Each barcode is stored in the `serial_no` field and integrates with ERPNext's Serial No DocType for inventory tracking.

## Barcode Format
```
PREFIX/YYMMDD#####
```

### Components:
- **PREFIX**: 2-letter category code (MO/SP/TV/AC)
- **YYMMDD**: Date in year-month-day format (e.g., 251205 for Dec 5, 2025)
- **#####**: 5-digit sequence number (auto-incremented daily)

### Examples:
- `MO/25120500001` - Mobile phone, Dec 5, 2025, sequence 1
- `SP/25120500023` - Spare part, Dec 5, 2025, sequence 23
- `TV/25120500005` - Television, Dec 5, 2025, sequence 5
- `AC/25120500142` - Accessory, Dec 5, 2025, sequence 142

## Category Prefixes

| Prefix | Category | Item Group Keywords |
|--------|----------|-------------------|
| **MO** | Mobile Phones | mobile, phone, smartphone |
| **SP** | Spare Parts | spare, part, component |
| **TV** | Televisions | tv, television, lcd, led |
| **AC** | Accessories | accessory, accessories, cable, charger, adapter |

**Default**: If no match is found, defaults to **MO** (Mobile)

## How It Works

### Automatic Generation
1. When a Service Request is saved, the system checks:
   - Is `is_barcode_generated` flag set? (Skip if yes)
   - Is `serial_no` already filled? (Skip if yes)
   - Is `device_item` selected? (Required)

2. If conditions are met, the system:
   - Fetches the Item Group from the device_item
   - Determines the appropriate prefix (MO/SP/TV/AC)
   - Gets the current date in YYMMDD format
   - Queries the database for the last sequence number for this prefix+date
   - Increments the sequence by 1
   - Generates the barcode: `PREFIX/YYMMDD#####`

3. The generated barcode is:
   - Stored in the `serial_no` field
   - Used to create a Serial No document in ERPNext
   - Marked with `is_barcode_generated = 1` flag

### Manual Generation
Users can manually trigger barcode generation:

1. **Via Button**: 
   - Open a Service Request (draft mode)
   - Ensure `device_item` is selected
   - Click **Tools → Generate Barcode**
   - Confirm the action

2. **Regeneration**:
   - The manual method resets `is_barcode_generated` and `serial_no`
   - Generates a fresh barcode with new sequence
   - Creates new Serial No document

## Integration with Serial No DocType

When a barcode is generated, the system automatically creates a Serial No document:

```python
{
    "doctype": "Serial No",
    "serial_no": "MO/25120500001",  # The generated barcode
    "item_code": "IPHONE-14-PRO",   # From device_item
    "item_name": "iPhone 14 Pro",
    "description": "Auto-generated for Service Request",
    "status": "Active",
    "company": "Your Company"
}
```

This enables:
- Full inventory tracking
- Serial number history
- Warranty tracking
- Stock movement monitoring

## Code Location

### Python Backend
**File**: `apps/gofix/gofix/gofix_services/doctype/service_request/service_request.py`

**Key Methods**:
- `generate_barcode()` - Main generation logic
- `get_barcode_prefix(item_group)` - Determines prefix from item group
- `get_next_barcode_sequence(prefix, date_str)` - Gets next sequence number
- `create_serial_no_document(barcode, item)` - Creates Serial No record
- `generate_barcode_manual(name)` - API method for manual generation

### JavaScript Frontend
**File**: `apps/gofix/gofix/gofix_services/doctype/service_request/service_request.js`

**Key Functions**:
- `generate_barcode(frm)` - Shows confirmation dialog and calls API
- Adds "Generate Barcode" button in Tools menu

## Usage Examples

### Example 1: Automatic Generation
```python
# User creates a new Service Request
service_request = frappe.new_doc("Service Request")
service_request.customer = "CUST-00001"
service_request.device_item = "IPHONE-14-PRO"  # Item group: "Mobile Phones"
service_request.service_date = "2025-12-05"
service_request.save()

# System automatically generates:
# serial_no = "MO/25120500001"
# is_barcode_generated = 1
# Creates Serial No document "MO/25120500001"
```

### Example 2: Manual Generation via API
```python
# Call from JavaScript or API
barcode = frappe.call({
    method: 'gofix.gofix_services.doctype.service_request.service_request.generate_barcode_manual',
    args: {
        name: 'SR-25-12-05-0001'
    }
})
# Returns: "MO/25120500002"
```

### Example 3: Custom Prefix Logic
```python
# TV with LCD item group
device_item = "SONY-BRAVIA-55"  # Item group: "LCD Televisions"
# Generated barcode: "TV/25120500001"

# Spare part
device_item = "BATTERY-IPHONE"  # Item group: "Spare Parts"
# Generated barcode: "SP/25120500001"
```

## Validation & Error Handling

### Validations:
1. **Uniqueness**: Each barcode is unique within the date
2. **Format**: Strict format validation (PREFIX/YYMMDD#####)
3. **Serial No Check**: Prevents duplicate Serial No creation

### Error Scenarios:

| Error | Cause | Solution |
|-------|-------|----------|
| "Serial No creation failed" | Duplicate serial number | System logs error but continues |
| No barcode generated | Missing device_item | Select a device item first |
| Sequence mismatch | Race condition | System auto-recovers on next request |

### Logging:
- Errors are logged to ERPNext Error Log
- Title: "Serial No Creation Error"
- User receives alert notification with error details

## Database Schema

### Service Request Fields:
- `serial_no` (Data) - Stores the generated barcode
- `is_barcode_generated` (Check) - Flag to prevent regeneration
- `device_item` (Link to Item) - Required for generation

### Serial No Fields (ERPNext Standard):
- `serial_no` (Data, Primary Key) - The barcode value
- `item_code` (Link) - References the device item
- `status` (Select) - Active/Inactive
- `company` (Link) - Company reference

## Configuration

### Customizing Prefixes:
Edit the `get_barcode_prefix()` method in `service_request.py`:

```python
def get_barcode_prefix(self, item_group):
    item_group_lower = item_group.lower()
    
    # Add custom prefixes here
    if "laptop" in item_group_lower:
        return "LP"
    elif "tablet" in item_group_lower:
        return "TB"
    # ... existing prefixes
```

### Changing Sequence Format:
Edit the barcode generation in `generate_barcode()`:

```python
# Current: PREFIX/YYMMDD##### (11 characters)
barcode = f"{prefix}/{date_str}{sequence:05d}"

# Alternative: PREFIX-YYMMDD-##### (14 characters)
barcode = f"{prefix}-{date_str}-{sequence:05d}"
```

## Testing

### Test Case 1: First Barcode of the Day
```python
# Expected: MO/25120500001
doc = frappe.get_doc("Service Request", "SR-TEST-001")
doc.device_item = "MOBILE-ITEM"
doc.save()
assert doc.serial_no == "MO/25120500001"
```

### Test Case 2: Sequential Generation
```python
# Create 3 service requests on same day
# Expected: MO/25120500001, MO/25120500002, MO/25120500003
for i in range(3):
    doc = frappe.new_doc("Service Request")
    doc.device_item = "MOBILE-ITEM"
    doc.save()
```

### Test Case 3: Different Categories
```python
# Mobile: MO/25120500001
# TV: TV/25120500001
# Spare: SP/25120500001
# All can coexist on same day with different prefixes
```

## Future Enhancements

1. **Barcode Printing**: Add print format with barcode image
2. **QR Code**: Generate QR codes alongside barcodes
3. **Custom Numbering Series**: Allow users to define custom series per branch
4. **Barcode Scanner Integration**: Real-time scanning in Job Tracker
5. **Batch Generation**: Generate barcodes in bulk for multiple requests
6. **Analytics**: Dashboard showing barcode usage by category

## Support

For issues or questions:
- Check Error Log: **Home → Tools → Error Log**
- Search for: "Serial No Creation Error" or "Barcode"
- Contact: System Administrator

---

**Version**: 1.0  
**Last Updated**: December 5, 2025  
**Author**: GoFix Development Team
