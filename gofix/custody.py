# Copyright (c) 2026, GoFix and contributors

"""Who may change a ticket, and from where.

A repair ticket describes a physical object. While that object is on a van
between two stores nobody can see it, nobody can work on it, and nothing anyone
records about it can be checked — so nothing may be recorded. And once it lands,
the only people who can honestly say what is happening to it are the people
standing next to it.

Two rules, both about custody rather than permission:

* **In transit, the ticket is frozen.** No stage moves, no spares, no QC, no
  billing. The only ways forward are the two that change the physical situation
  — receive the device, or call the dispatch back — plus notes, which anybody
  may add at any time because a note claims nothing about the device.

* **Otherwise, only the holder may act.** The ticket is editable from the
  location the device is actually at, not from the store that raised it and not
  from head office.

Both are enforced at ``assert_service_request_access``, which every write path
already goes through, so there is no route into a ticket that misses them.
"""

import frappe
from frappe import _

# Ticket-level states meaning the device is on the road: nobody holds it.
IN_TRANSIT_STATES = ("In Transit", "Return In Transit")

# What stays open while the device is in transit — each either changes the
# physical situation or claims nothing about it.
TRANSIT_EXEMPT_ACTIONS = frozenset({
	"note",       # remarks and comments — anyone, any time
	"receive",    # taking custody is what unlocks the rest
	"cancel",     # calling the dispatch back
	"movement",   # the transfer legs themselves
})

# Acting on a ticket from elsewhere is refused, except for the same set: a note
# is location-free, and a movement is raised by whichever end is handling it.
LOCATION_EXEMPT_ACTIONS = TRANSIT_EXEMPT_ACTIONS | frozenset({"dispatch"})


def device_location(sr) -> str | None:
	"""The warehouse physically holding the device, or None while in transit."""
	transfer = (sr.get("transfer_status") or "").strip()
	if transfer in IN_TRANSIT_STATES:
		return None
	if transfer in ("Received at Service Center", "Repair Complete"):
		return sr.get("current_location") or sr.get("transferred_to_store")
	return sr.get("current_location") or sr.get("source_warehouse")


def custody_state(sr) -> dict:
	"""Where the device is and what that allows, in one answer."""
	transfer = (sr.get("transfer_status") or "").strip()
	return {
		"in_transit": transfer in IN_TRANSIT_STATES,
		"transfer_status": transfer,
		"location": device_location(sr),
		"destination": sr.get("transferred_to_store"),
		"home_store": sr.get("source_warehouse"),
	}


def _user_holds(warehouse: str, user: str | None = None) -> bool:
	"""Whether this user's scope covers the place the device is standing."""
	if not warehouse:
		return False
	try:
		from gofix.scope_guard import user_scope
	except (ImportError, ModuleNotFoundError):
		return True
	try:
		allowed, _companies, bypass = user_scope(user)
	except Exception:
		# A scope that cannot be resolved must not silently unlock the ticket,
		# but neither should it lock out a site that has not adopted scoping.
		# Reading fails open here and closed nowhere else: the company and role
		# checks in assert_service_request_access have already run.
		return True
	if bypass:
		return True
	return warehouse in (allowed or set())


def assert_custody_allows_write(sr, action: str | None = None, user: str | None = None) -> None:
	"""Refuse a change the person making it is not in a position to make.

	``action`` names what is being attempted so the two exempt sets can let the
	right things through. An unnamed action is treated as an ordinary edit,
	which is the safe default: a new API that forgets to declare itself is
	locked rather than quietly exempt.
	"""
	state = custody_state(sr)

	if state["in_transit"] and action not in TRANSIT_EXEMPT_ACTIONS:
		frappe.throw(
			_(
				"{0} is in transit to {1}, so it cannot be worked on. Nobody is "
				"holding the device, and anything recorded now would be a guess. "
				"Receive it at the destination to carry on, or cancel the "
				"dispatch to bring it back. Notes can still be added."
			).format(sr.name, frappe.bold(_shorten(state["destination"]) or _("its destination"))),
			title=_("Device In Transit"),
		)

	if action in LOCATION_EXEMPT_ACTIONS:
		return

	location = state["location"]
	if location and not _user_holds(location, user):
		frappe.throw(
			_(
				"{0} can only be updated from {1}, where the device is. Your "
				"access does not cover that location — the people holding the "
				"device are the ones who can say what is happening to it."
			).format(sr.name, frappe.bold(_shorten(location))),
			title=_("Device Is Elsewhere"),
		)


def _shorten(warehouse: str | None) -> str:
	return (warehouse or "").split(" - ")[0]
