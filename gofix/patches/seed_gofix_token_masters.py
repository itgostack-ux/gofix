"""Seed the GoFix walk-in intake masters (symptoms, visit and cancellation reasons).

Loads the BRD-defined device types, per-device brands and symptoms, walk-in
visit reasons, cancellation reasons, the backend Issue Category taxonomy the
symptoms map to, and the ``gofix_token_confirmation`` WhatsApp event. Safe to
re-run — every insert is guarded by an existence check, and updates only touch
our seeded rows when the display order or a flag has changed. Ops can freely
add / disable rows in the UI without this patch clobbering them.

Sections mapped from the GoFix Token BRD (updated change requirements,
2026-07-16):

* 3.1       Brand catalogue per device
* 4.1 – 4.5 Symptom catalogue per device (symptom = what the customer sees)
* 2.        Visit reasons
* 11.       Cancellation reasons
* 13/14.    WhatsApp token-confirmation event registration
* 17.       Backend mapping: customer-facing symptom → Issue Category, so the
            Service Request / job tracker and the token reports share one
            service taxonomy.

Symptoms retired by the 2026-07-16 catalogue revision are disabled, never
deleted — historical ``POS Kiosk Token Symptom`` rows keep a valid ``symptom_ref``.
"""

from __future__ import annotations

import frappe


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

# Device taxonomy lives in the item master (CH Category / Brand / CH Model).
# The seed only flags which categories the kiosk and tablet offer for repair
# and keys the symptom catalogue by those categories. Keys below are the
# historical BRD labels; the map turns them into CH Category names.
_DEVICE_TYPE_CATEGORY = {
	"Mobile": "Smart Phones",
	"Tablet": "Tablets",
	"Laptop": "Laptops",
	"Smartwatch": "Watches",
	"Other": None,  # generic symptoms: device_category left blank
}
_REPAIRABLE_CATEGORIES = [
	{"category": "Smart Phones", "icon": "\U0001f4f1", "order": 10},
	{"category": "Tablets", "icon": "\U0001f4f2", "order": 20},
	{"category": "Laptops", "icon": "\U0001f4bb", "order": 30},
	{"category": "Watches", "icon": "⌚", "order": 40},
]


# Backend service taxonomy (BRD §17: customer sees the symptom, the job
# tracker works in service categories). category_name is the PK.
_ISSUE_CATEGORIES = [
	{"category_name": "Screen & Display", "description": "Display panel, touch glass, lines / flickering, black screen", "estimated_repair_hours": 2},
	{"category_name": "Battery", "description": "Battery drain, swelling, backup issues", "estimated_repair_hours": 1},
	{"category_name": "Charging & Power", "description": "Charging port, adapter / charger, slow or no charging", "estimated_repair_hours": 2},
	{"category_name": "Board Diagnosis", "description": "Dead device, hanging / heating / restarting — motherboard-level diagnosis", "estimated_repair_hours": 24},
	{"category_name": "Camera", "description": "Front / rear camera and webcam faults", "estimated_repair_hours": 2},
	{"category_name": "Speaker & Mic", "description": "Speaker, microphone and audio faults", "estimated_repair_hours": 2},
	{"category_name": "Water Damage", "description": "Liquid ingress treatment and corrosion service", "estimated_repair_hours": 48},
	{"category_name": "Physical Damage", "description": "Back panel, hinge, strap, body / housing damage", "estimated_repair_hours": 4},
	{"category_name": "Software", "description": "OS, password / lock, updates, virus, settings", "estimated_repair_hours": 2},
	{"category_name": "Network & Connectivity", "description": "SIM / network, Wi-Fi, Bluetooth, pairing", "estimated_repair_hours": 2},
	{"category_name": "Sensors & Biometrics", "description": "Face ID, fingerprint, health / motion sensors", "estimated_repair_hours": 4},
	{"category_name": "Buttons & Keys", "description": "Buttons, crown, keyboard keys, touchpad", "estimated_repair_hours": 2},
	{"category_name": "Data Recovery", "description": "Data backup and recovery service", "estimated_repair_hours": 48},
	{"category_name": "Accessories", "description": "Stylus / pen and other device accessories", "estimated_repair_hours": 1},
	{"category_name": "General Diagnosis", "description": "Expert check-up when the fault is unknown or uncategorised", "estimated_repair_hours": 4},
]

# Symptom rows: (label, is_expert_check, is_other, backend_category).
# Labels are exactly the BRD §4.1–§4.5 customer-facing options; the fourth
# element is the Issue Category the FDE's job card should default to.
_SYMPTOMS: dict[str, list[tuple[str, int, int, str]]] = {
	"Mobile": [
		("Screen cracked / broken", 0, 0, "Screen & Display"),
		("Display not working / black screen", 0, 0, "Screen & Display"),
		("Lines or flickering on display", 0, 0, "Screen & Display"),
		("Touch not working", 0, 0, "Screen & Display"),
		("Battery draining fast", 0, 0, "Battery"),
		("Battery swollen", 0, 0, "Battery"),
		("Not charging / charging slowly", 0, 0, "Charging & Power"),
		("Device not switching on", 0, 0, "Board Diagnosis"),
		("Camera not working", 0, 0, "Camera"),
		("Speaker / mic issue", 0, 0, "Speaker & Mic"),
		("Liquid / water damage", 0, 0, "Water Damage"),
		("Phone hanging / heating / restarting", 0, 0, "Board Diagnosis"),
		("Back panel damaged", 0, 0, "Physical Damage"),
		("Software / password / update issue", 0, 0, "Software"),
		("Network / SIM issue", 0, 0, "Network & Connectivity"),
		("Face ID / fingerprint not working", 0, 0, "Sensors & Biometrics"),
		("Buttons not working", 0, 0, "Buttons & Keys"),
		("Not sure / need expert check", 1, 0, "General Diagnosis"),
		("Other", 0, 1, "General Diagnosis"),
	],
	"Tablet": [
		("Screen cracked / broken", 0, 0, "Screen & Display"),
		("Display not working / black screen", 0, 0, "Screen & Display"),
		("Lines or flickering on display", 0, 0, "Screen & Display"),
		("Touch not working", 0, 0, "Screen & Display"),
		("Battery draining fast", 0, 0, "Battery"),
		("Battery swollen", 0, 0, "Battery"),
		("Not charging / charging slowly", 0, 0, "Charging & Power"),
		("Tablet not switching on", 0, 0, "Board Diagnosis"),
		("Camera not working", 0, 0, "Camera"),
		("Speaker / mic issue", 0, 0, "Speaker & Mic"),
		("Liquid / water damage", 0, 0, "Water Damage"),
		("Tablet hanging / heating / restarting", 0, 0, "Board Diagnosis"),
		("Back panel damaged", 0, 0, "Physical Damage"),
		("Software / password / update issue", 0, 0, "Software"),
		("WiFi / Bluetooth issue", 0, 0, "Network & Connectivity"),
		("SIM / network issue", 0, 0, "Network & Connectivity"),
		("Stylus / pen not working", 0, 0, "Accessories"),
		("Buttons not working", 0, 0, "Buttons & Keys"),
		("Not sure / need expert check", 1, 0, "General Diagnosis"),
		("Other", 0, 1, "General Diagnosis"),
	],
	"Laptop": [
		("Screen cracked / broken", 0, 0, "Screen & Display"),
		("Display not working / black screen", 0, 0, "Screen & Display"),
		("Lines or flickering on display", 0, 0, "Screen & Display"),
		("Laptop not switching on", 0, 0, "Board Diagnosis"),
		("Battery backup issue", 0, 0, "Battery"),
		("Battery not charging", 0, 0, "Charging & Power"),
		("Charger / charging port issue", 0, 0, "Charging & Power"),
		("Keyboard not working", 0, 0, "Buttons & Keys"),
		("Touchpad not working", 0, 0, "Buttons & Keys"),
		("Laptop heating", 0, 0, "Board Diagnosis"),
		("Laptop running slow / hanging", 0, 0, "Software"),
		("Software / OS issue", 0, 0, "Software"),
		("Virus / data issue", 0, 0, "Software"),
		("Speaker / mic issue", 0, 0, "Speaker & Mic"),
		("Camera not working", 0, 0, "Camera"),
		("WiFi / Bluetooth issue", 0, 0, "Network & Connectivity"),
		("Hinge damage", 0, 0, "Physical Damage"),
		("Body / panel damage", 0, 0, "Physical Damage"),
		("Liquid damage", 0, 0, "Water Damage"),
		("Data recovery needed", 0, 0, "Data Recovery"),
		("Not sure / need expert check", 1, 0, "General Diagnosis"),
		("Other", 0, 1, "General Diagnosis"),
	],
	"Smartwatch": [
		("Screen cracked / broken", 0, 0, "Screen & Display"),
		("Display not working / black screen", 0, 0, "Screen & Display"),
		("Touch not working", 0, 0, "Screen & Display"),
		("Battery draining fast", 0, 0, "Battery"),
		("Not charging / charging slowly", 0, 0, "Charging & Power"),
		("Watch not switching on", 0, 0, "Board Diagnosis"),
		("Strap / body damage", 0, 0, "Physical Damage"),
		("Button / crown not working", 0, 0, "Buttons & Keys"),
		("Speaker / mic issue", 0, 0, "Speaker & Mic"),
		("Bluetooth pairing issue", 0, 0, "Network & Connectivity"),
		("Notifications not working", 0, 0, "Software"),
		("Sensor / health tracking issue", 0, 0, "Sensors & Biometrics"),
		("Software / update issue", 0, 0, "Software"),
		("Water damage", 0, 0, "Water Damage"),
		("Watch heating / restarting", 0, 0, "Board Diagnosis"),
		("Not sure / need expert check", 1, 0, "General Diagnosis"),
		("Other", 0, 1, "General Diagnosis"),
	],
	"Other": [
		("Device not switching on", 0, 0, "Board Diagnosis"),
		("Not charging / power issue", 0, 0, "Charging & Power"),
		("Display / light not working", 0, 0, "Screen & Display"),
		("Sound issue", 0, 0, "Speaker & Mic"),
		("Connectivity issue", 0, 0, "Network & Connectivity"),
		("Button not working", 0, 0, "Buttons & Keys"),
		("Body damage", 0, 0, "Physical Damage"),
		("Liquid / water damage", 0, 0, "Water Damage"),
		("Software / settings issue", 0, 0, "Software"),
		("Not sure / need expert check", 1, 0, "General Diagnosis"),
		("Other", 0, 1, "General Diagnosis"),
	],
}

# The pre-2026-07-16 catalogue. Labels here that are absent from _SYMPTOMS get
# disabled (not deleted — token history keeps a valid symptom_ref).
_LEGACY_SYMPTOMS: dict[str, list[str]] = {
	"Mobile": [
		"Screen cracked / broken", "Touch not working", "Display flickering / lines",
		"Battery draining fast", "Battery not charging", "Phone not switching on",
		"Overheating", "Speaker / sound issue", "Microphone issue", "Camera not working",
		"Charging port loose / broken", "Water damage", "Software / OS issue",
		"SIM / network issue", "Slow / hanging", "Face ID / fingerprint issue",
		"Volume / power button issue", "Body / back panel damage",
		"Not sure / need expert check", "Other",
	],
	"Tablet": [
		"Screen cracked / broken", "Touch not working", "Display flickering / lines",
		"Battery draining fast", "Battery not charging", "Not switching on",
		"Overheating", "Speaker / sound issue", "Microphone issue", "Camera not working",
		"Charging port loose / broken", "Water damage", "Software / OS issue",
		"Wi-Fi / connectivity issue", "Slow / hanging", "Stylus / pen issue",
		"Volume / power button issue", "Body / back panel damage",
		"Not sure / need expert check", "Other",
	],
	"Laptop": [
		"Screen cracked / broken", "Display flickering / lines", "Touchpad not working",
		"Keyboard keys not working", "Battery draining fast", "Battery not charging",
		"Adapter / charger issue", "Not switching on", "Overheating / fan noise",
		"Speaker / audio issue", "Microphone issue", "Webcam not working",
		"USB / HDMI port issue", "Wi-Fi / Bluetooth issue", "Hard-disk / SSD issue",
		"Windows / macOS / software issue", "Slow / hanging", "Hinge / body damage",
		"Water damage", "Virus / malware", "Not sure / need expert check", "Other",
	],
	"Smartwatch": [
		"Screen cracked / broken", "Touch not working", "Display flickering",
		"Battery draining fast", "Battery not charging", "Not switching on",
		"Strap / band broken", "Charging pin / cable issue", "Bluetooth pairing issue",
		"Heart-rate / sensor not working", "Water damage", "Buttons not responding",
		"Software / firmware issue", "Body / crown damage", "Speaker / mic issue",
		"Not sure / need expert check", "Other",
	],
	"Other": [
		"Screen / display issue", "Battery issue", "Not switching on", "Charging issue",
		"Water damage", "Software issue", "Physical damage", "Accessory issue",
		"Warranty / service enquiry", "Not sure / need expert check", "Other",
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


def _seed_repairable_categories() -> None:
	"""Flag the item-master categories the kiosk and tablet offer for repair."""
	if not frappe.db.has_column("CH Category", "is_repairable_device"):
		return
	for row in _REPAIRABLE_CATEGORIES:
		if not frappe.db.exists("CH Category", row["category"]):
			continue
		current = frappe.db.get_value(
			"CH Category", row["category"], ["is_repairable_device", "device_icon", "kiosk_display_order"], as_dict=True)
		values = {}
		if not current.is_repairable_device:
			values["is_repairable_device"] = 1
		if not current.device_icon:
			values["device_icon"] = row["icon"]
		if not current.kiosk_display_order:
			values["kiosk_display_order"] = row["order"]
		if values:
			frappe.db.set_value("CH Category", row["category"], values, update_modified=False)


_CATEGORY_CODES = {
	"Screen & Display": "SCR", "Battery": "BAT", "Charging & Power": "CHG",
	"Board Diagnosis": "BRD", "Camera": "CAM", "Speaker & Mic": "AUD",
	"Water Damage": "WTR", "Physical Damage": "PHY", "Software": "SFT",
	"Network & Connectivity": "NET", "Sensors & Biometrics": "SNS",
	"Buttons & Keys": "BTN", "Data Recovery": "DAT", "Accessories": "ACC",
	"General Diagnosis": "GEN",
}


def _seed_issue_categories() -> None:
	for row in _ISSUE_CATEGORIES:
		values = dict(row)
		values["is_active"] = 1
		if row["category_name"] in _CATEGORY_CODES:
			values["category_code"] = _CATEGORY_CODES[row["category_name"]]
		# Don't overwrite ops-tuned repair-hour estimates on re-run — only set
		# the estimate when the category is new or still unset.
		existing = frappe.db.exists("Issue Category", row["category_name"])
		if existing and frappe.db.get_value("Issue Category", existing, "estimated_repair_hours"):
			values.pop("estimated_repair_hours", None)
		_upsert("Issue Category", row["category_name"], values)


def _symptom_filters(device_type: str, label: str) -> dict:
	category = _DEVICE_TYPE_CATEGORY.get(device_type, device_type)
	if category and not frappe.db.exists("CH Category", category):
		category = None
	return {"device_category": category or ("is", "not set"), "symptom_name": label}


def _seed_symptoms() -> None:
	for device_type, symptoms in _SYMPTOMS.items():
		category = _DEVICE_TYPE_CATEGORY.get(device_type, device_type)
		if category and not frappe.db.exists("CH Category", category):
			category = None
		for order, (label, is_expert, is_other, backend) in enumerate(symptoms, start=1):
			_upsert(
				"GoFix Symptom",
				_symptom_filters(device_type, label),
				{
					"device_category": category,
					"symptom_name": label,
					"is_expert_check": is_expert,
					"is_other": is_other,
					"display_order": order * 10,
					"backend_category": backend,
					"symptom_code": f"{_CATEGORY_CODES.get(backend, 'GEN')}-{order:02d}",
				},
			)


def _retire_legacy_symptoms() -> None:
	"""Disable previously seeded symptoms dropped by the catalogue revision.

	Only labels from _LEGACY_SYMPTOMS are touched — symptoms ops added by
	hand are left alone.
	"""

	retired = 0
	for device_type, legacy in _LEGACY_SYMPTOMS.items():
		current = {label for (label, _e, _o, _c) in _SYMPTOMS.get(device_type, [])}
		for label in set(legacy) - current:
			filters = _symptom_filters(device_type, label)
			filters["disabled"] = 0
			name = frappe.db.exists("GoFix Symptom", filters)
			if name:
				frappe.db.set_value("GoFix Symptom", name, "disabled", 1, update_modified=False)
				retired += 1
	if retired:
		print(f"Retired {retired} legacy GoFix symptoms (disabled, not deleted).")


def _seed_visit_reasons() -> None:
	for row in _VISIT_REASONS:
		_upsert("GoFix Visit Reason", row["reason_name"], row)


def _seed_cancellation_reasons() -> None:
	for row in _CANCELLATION_REASONS:
		_upsert("GoFix Cancellation Reason", row["reason_name"], row)


def _register_whatsapp_event() -> None:
	"""Add gofix_token_confirmation to the CH WhatsApp Event catalog.

	Variables mirror what ``whatsapp_notifications.send_token_confirmation``
	actually sends (the BRD §14 short template plus queue position). No-op on
	sites without ch_item_master.
	"""

	if not frappe.db.table_exists("CH WhatsApp Event"):
		return
	_upsert(
		"CH WhatsApp Event",
		"gofix_token_confirmation",
		{
			"event_key": "gofix_token_confirmation",
			"label": "GoFix Token Confirmation",
			"module": "GoFix",
			"default_template": "gofix_token_confirmation",
			"variables": "1=customer, 2=token number, 3=store name, 4=queue position",
		},
	)


# ---------------------------------------------------------------------------
# Patch entrypoint
# ---------------------------------------------------------------------------


def execute() -> None:
	for doctype in (
		"GoFix Symptom",
		"GoFix Visit Reason",
		"GoFix Cancellation Reason",
		"Issue Category",
	):
		if not frappe.db.table_exists(doctype):
			# Migration order should have created these; bail politely if not.
			frappe.log_error(
				title="seed_gofix_token_masters",
				message=f"Table for {doctype} missing at patch time; skipping seed.",
			)
			return

	_seed_repairable_categories()
	_seed_issue_categories()
	_seed_symptoms()
	_retire_legacy_symptoms()
	_seed_visit_reasons()
	_seed_cancellation_reasons()
	_register_whatsapp_event()
	frappe.db.commit()
	print(
		"Seeded GoFix Token masters: "
		f"{len(_REPAIRABLE_CATEGORIES)} repairable categories flagged, "
		f"{len(_ISSUE_CATEGORIES)} issue categories, "
		f"{sum(len(v) for v in _SYMPTOMS.values())} symptoms, "
		f"{len(_VISIT_REASONS)} visit reasons, "
		f"{len(_CANCELLATION_REASONS)} cancellation reasons."
	)
