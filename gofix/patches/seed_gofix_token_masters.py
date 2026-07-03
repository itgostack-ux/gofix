"""Seed GoFix Token master data.

Loads the BRD-defined device types, per-device brands and symptoms, walk-in
visit reasons and cancellation reasons. Safe to re-run — every insert is
guarded by an existence check, and updates only touch our seeded rows when the
display order or flag has changed. Ops can freely add / disable rows in the
UI without this patch clobbering them.

Sections mapped from the GoFix Token BRD:

* 3.1 Brand catalogue per device
* 4.1 – 4.5 Symptom catalogue per device
* 5.  Visit reasons
* 8.  Cancellation reasons
"""

from __future__ import annotations

import frappe


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_DEVICE_TYPES = [
	{"device_type": "Mobile", "icon": "\U0001f4f1", "display_order": 10},
	{"device_type": "Tablet", "icon": "\U0001f4f2", "display_order": 20},
	{"device_type": "Laptop", "icon": "\U0001f4bb", "display_order": 30},
	{"device_type": "Smartwatch", "icon": "\u231a", "display_order": 40},
	{"device_type": "Other", "icon": "\U0001f527", "display_order": 90},
]

_BRANDS: dict[str, list[str]] = {
	"Mobile": [
		"Samsung", "Apple", "OnePlus", "Vivo", "Oppo", "Redmi", "Xiaomi",
		"Realme", "Motorola", "Nothing", "Poco", "Google Pixel", "Nokia",
		"Infinix", "Tecno", "Lava", "iQOO", "Other",
	],
	"Tablet": [
		"Apple", "Samsung", "Lenovo", "Xiaomi", "Redmi", "OnePlus", "Realme",
		"Oppo", "Honor", "Huawei", "Nokia", "Motorola", "Microsoft Surface",
		"Other",
	],
	"Laptop": [
		"HP", "Dell", "Lenovo", "Apple", "Asus", "Acer", "Microsoft Surface",
		"MSI", "Samsung", "LG", "Avita", "Honor", "Xiaomi", "Fujitsu",
		"Toshiba", "Other",
	],
	"Smartwatch": [
		"Apple", "Samsung", "Noise", "Boat", "Fire-Boltt", "Fastrack", "Titan",
		"Amazfit", "Garmin", "Fitbit", "OnePlus", "Oppo", "Realme", "Redmi",
		"Xiaomi", "Huawei", "Honor", "Crossbeats", "Other",
	],
	"Other": [
		"Other",
	],
}

# Symptom rows: (label, is_expert_check, is_other). Free-form entries with
# is_other=1 receive the additional-notes textarea on the tablet.
_SYMPTOMS: dict[str, list[tuple[str, int, int]]] = {
	"Mobile": [
		("Screen cracked / broken", 0, 0),
		("Touch not working", 0, 0),
		("Display flickering / lines", 0, 0),
		("Battery draining fast", 0, 0),
		("Battery not charging", 0, 0),
		("Phone not switching on", 0, 0),
		("Overheating", 0, 0),
		("Speaker / sound issue", 0, 0),
		("Microphone issue", 0, 0),
		("Camera not working", 0, 0),
		("Charging port loose / broken", 0, 0),
		("Water damage", 0, 0),
		("Software / OS issue", 0, 0),
		("SIM / network issue", 0, 0),
		("Slow / hanging", 0, 0),
		("Face ID / fingerprint issue", 0, 0),
		("Volume / power button issue", 0, 0),
		("Body / back panel damage", 0, 0),
		("Not sure / need expert check", 1, 0),
		("Other", 0, 1),
	],
	"Tablet": [
		("Screen cracked / broken", 0, 0),
		("Touch not working", 0, 0),
		("Display flickering / lines", 0, 0),
		("Battery draining fast", 0, 0),
		("Battery not charging", 0, 0),
		("Not switching on", 0, 0),
		("Overheating", 0, 0),
		("Speaker / sound issue", 0, 0),
		("Microphone issue", 0, 0),
		("Camera not working", 0, 0),
		("Charging port loose / broken", 0, 0),
		("Water damage", 0, 0),
		("Software / OS issue", 0, 0),
		("Wi-Fi / connectivity issue", 0, 0),
		("Slow / hanging", 0, 0),
		("Stylus / pen issue", 0, 0),
		("Volume / power button issue", 0, 0),
		("Body / back panel damage", 0, 0),
		("Not sure / need expert check", 1, 0),
		("Other", 0, 1),
	],
	"Laptop": [
		("Screen cracked / broken", 0, 0),
		("Display flickering / lines", 0, 0),
		("Touchpad not working", 0, 0),
		("Keyboard keys not working", 0, 0),
		("Battery draining fast", 0, 0),
		("Battery not charging", 0, 0),
		("Adapter / charger issue", 0, 0),
		("Not switching on", 0, 0),
		("Overheating / fan noise", 0, 0),
		("Speaker / audio issue", 0, 0),
		("Microphone issue", 0, 0),
		("Webcam not working", 0, 0),
		("USB / HDMI port issue", 0, 0),
		("Wi-Fi / Bluetooth issue", 0, 0),
		("Hard-disk / SSD issue", 0, 0),
		("Windows / macOS / software issue", 0, 0),
		("Slow / hanging", 0, 0),
		("Hinge / body damage", 0, 0),
		("Water damage", 0, 0),
		("Virus / malware", 0, 0),
		("Not sure / need expert check", 1, 0),
		("Other", 0, 1),
	],
	"Smartwatch": [
		("Screen cracked / broken", 0, 0),
		("Touch not working", 0, 0),
		("Display flickering", 0, 0),
		("Battery draining fast", 0, 0),
		("Battery not charging", 0, 0),
		("Not switching on", 0, 0),
		("Strap / band broken", 0, 0),
		("Charging pin / cable issue", 0, 0),
		("Bluetooth pairing issue", 0, 0),
		("Heart-rate / sensor not working", 0, 0),
		("Water damage", 0, 0),
		("Buttons not responding", 0, 0),
		("Software / firmware issue", 0, 0),
		("Body / crown damage", 0, 0),
		("Speaker / mic issue", 0, 0),
		("Not sure / need expert check", 1, 0),
		("Other", 0, 1),
	],
	"Other": [
		("Screen / display issue", 0, 0),
		("Battery issue", 0, 0),
		("Not switching on", 0, 0),
		("Charging issue", 0, 0),
		("Water damage", 0, 0),
		("Software issue", 0, 0),
		("Physical damage", 0, 0),
		("Accessory issue", 0, 0),
		("Warranty / service enquiry", 0, 0),
		("Not sure / need expert check", 1, 0),
		("Other", 0, 1),
	],
}

_VISIT_REASONS = [
	{"reason_name": "Repair my device", "is_repair": 1, "display_order": 10},
	{"reason_name": "Check existing repair status", "is_repair": 0, "display_order": 20},
	{"reason_name": "Collect my device", "is_repair": 0, "display_order": 30},
	{"reason_name": "Warranty / service issue", "is_repair": 0, "display_order": 40},
	{"reason_name": "General enquiry", "is_repair": 0, "display_order": 50},
	{"reason_name": "Other", "is_repair": 0, "display_order": 90},
]

_CANCELLATION_REASONS = [
	{"reason_name": "Customer left due to waiting time", "scope": "Customer Left", "requires_note": 0, "display_order": 10},
	{"reason_name": "Price not accepted", "scope": "Both", "requires_note": 0, "display_order": 20},
	{"reason_name": "Service not available", "scope": "Both", "requires_note": 0, "display_order": 30},
	{"reason_name": "Part not available", "scope": "Both", "requires_note": 0, "display_order": 40},
	{"reason_name": "Just enquiry", "scope": "Both", "requires_note": 0, "display_order": 50},
	{"reason_name": "Warranty issue", "scope": "Both", "requires_note": 0, "display_order": 60},
	{"reason_name": "Duplicate token", "scope": "Cancelled", "requires_note": 0, "display_order": 70},
	{"reason_name": "Wrong entry", "scope": "Cancelled", "requires_note": 0, "display_order": 80},
	{"reason_name": "Other", "scope": "Both", "requires_note": 1, "display_order": 90},
]


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


def _upsert(doctype: str, name_or_filters, values: dict) -> None:
	"""Insert if missing, otherwise patch only the fields that changed.

	`name_or_filters` may be either the DocType name (str) or a dict of
	filters — the second form is used for the standalone Brand/Symptom
	masters that autoname by expression.
	"""

	if isinstance(name_or_filters, str):
		existing = frappe.db.exists(doctype, name_or_filters)
	else:
		existing = frappe.db.exists(doctype, name_or_filters)
	if existing:
		doc = frappe.get_doc(doctype, existing)
		dirty = False
		for field, value in values.items():
			if doc.get(field) != value:
				doc.set(field, value)
				dirty = True
		if dirty:
			doc.save(ignore_permissions=True)
		return
	doc = frappe.new_doc(doctype)
	doc.update(values)
	doc.insert(ignore_permissions=True)


def _seed_device_types() -> None:
	for row in _DEVICE_TYPES:
		_upsert("GoFix Device Type", row["device_type"], row)


def _seed_brands() -> None:
	for device_type, brands in _BRANDS.items():
		for order, brand in enumerate(brands, start=1):
			key = f"{device_type}::{brand}"
			_upsert(
				"GoFix Brand Option",
				key,
				{
					"device_type": device_type,
					"brand_name": brand,
					"display_order": order * 10,
				},
			)


def _seed_symptoms() -> None:
	for device_type, symptoms in _SYMPTOMS.items():
		for order, (label, is_expert, is_other) in enumerate(symptoms, start=1):
			key = f"{device_type}::{label}"
			_upsert(
				"GoFix Symptom",
				key,
				{
					"device_type": device_type,
					"symptom_name": label,
					"is_expert_check": is_expert,
					"is_other": is_other,
					"display_order": order * 10,
				},
			)


def _seed_visit_reasons() -> None:
	for row in _VISIT_REASONS:
		_upsert("GoFix Visit Reason", row["reason_name"], row)


def _seed_cancellation_reasons() -> None:
	for row in _CANCELLATION_REASONS:
		_upsert("GoFix Cancellation Reason", row["reason_name"], row)


# ---------------------------------------------------------------------------
# Patch entrypoint
# ---------------------------------------------------------------------------


def execute() -> None:
	for doctype in (
		"GoFix Device Type",
		"GoFix Brand Option",
		"GoFix Symptom",
		"GoFix Visit Reason",
		"GoFix Cancellation Reason",
	):
		if not frappe.db.table_exists(doctype):
			# Migration order should have created these; bail politely if not.
			frappe.log_error(
				title="seed_gofix_token_masters",
				message=f"Table for {doctype} missing at patch time; skipping seed.",
			)
			return

	_seed_device_types()
	_seed_brands()
	_seed_symptoms()
	_seed_visit_reasons()
	_seed_cancellation_reasons()
	frappe.db.commit()
	print(
		"Seeded GoFix Token masters: "
		f"{len(_DEVICE_TYPES)} device types, "
		f"{sum(len(v) for v in _BRANDS.values())} brands, "
		f"{sum(len(v) for v in _SYMPTOMS.values())} symptoms, "
		f"{len(_VISIT_REASONS)} visit reasons, "
		f"{len(_CANCELLATION_REASONS)} cancellation reasons."
	)
