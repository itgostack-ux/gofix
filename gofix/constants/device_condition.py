# Copyright (c) 2026, GoFix and contributors

"""The one device-intake condition vocabulary.

Three lists used to exist — the Service Request DocType, the POS repair
workspace and the POS queue workspace — and they agreed on only two values.
"Minor Scratches", "Cracked Screen" and "Water Damage" were offered at intake
and rejected on save, so counter staff could record nothing but "Good" and
every ticket in the system says "Good" regardless of the device's real state.

Anything that shows or stores a device condition reads this list. It is
published to the client on boot as ``frappe.boot.gofix_device_conditions``.
"""

# Ordered best -> worst, which is the order a counter operator scans.
DEVICE_CONDITIONS = (
	"Good",
	"Minor Scratches",
	"Cracked Screen",
	"Damaged",
	"Water Damaged",
	"Broken",
)

DEFAULT_DEVICE_CONDITION = "Good"

# Values that older clients (and the pre-fix POS bundles) may still send.
# Mapped rather than rejected so a stale cached bundle cannot block an intake.
LEGACY_DEVICE_CONDITION_ALIASES = {
	"water damage": "Water Damaged",
	"waterdamaged": "Water Damaged",
	"cracked": "Cracked Screen",
	"scratches": "Minor Scratches",
	"minor scratch": "Minor Scratches",
}


def as_select_options() -> str:
	"""Newline-joined, the way a Select docfield and a frappe Dialog want it."""
	return "\n".join(DEVICE_CONDITIONS)


def normalize_device_condition(value):
	"""Coerce a submitted condition onto the canonical vocabulary.

	Returns None for an empty value so a caller can apply its own default.
	Anything unrecognised is returned unchanged — the DocType's own Select
	validation is left to reject it, rather than silently rewriting a value
	somebody deliberately configured.
	"""
	if value is None:
		return None
	text = str(value).strip()
	if not text:
		return None
	for canonical in DEVICE_CONDITIONS:
		if text.casefold() == canonical.casefold():
			return canonical
	return LEGACY_DEVICE_CONDITION_ALIASES.get(text.casefold(), text)
