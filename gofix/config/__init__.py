from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, flt


IMMUTABLE_PRIVILEGED_ROLES = frozenset({"System Manager"})
NON_BUSINESS_USERS = frozenset({"Administrator", "Guest"})


def get_user_roles(user: str | None = None) -> frozenset[str]:
	"""Return the authenticated user's roles through the central access registry."""
	user = user or getattr(frappe.session, "user", None)
	if not user or user == "Guest":
		return frozenset()
	try:
		return frozenset(frappe.get_roles(user))
	except Exception:
		return frozenset()


def is_privileged_user(user: str | None = None) -> bool:
	"""Administrator and System Manager always retain full app access."""
	user = user or getattr(frappe.session, "user", None)
	if not user or user == "Guest":
		return False
	if user == "Administrator":
		return True
	return bool(get_user_roles(user).intersection(IMMUTABLE_PRIVILEGED_ROLES))


def get_setting(fieldname: str, default=None):
	try:
		value = frappe.get_cached_value("GoFix Settings", None, fieldname)
	except Exception:
		return default
	return default if value in (None, "") else value


def get_int_setting(fieldname: str, default: int, minimum: int = 1) -> int:
	return max(cint(get_setting(fieldname, default)), minimum)


def get_float_setting(fieldname: str, default: float, minimum: float = 0) -> float:
	return max(flt(get_setting(fieldname, default)), minimum)


# Operational override gates only. Everything else is enforced by native
# Frappe DocPerm via frappe.has_permission(...).
def get_role_setting(fieldname: str, defaults=()) -> set[str]:
	from ch_erp15.role_settings import get_setting_roles

	roles = set(get_setting_roles("GoFix Settings", fieldname, defaults))
	return roles.union(IMMUTABLE_PRIVILEGED_ROLES)


def _normalize_roles(roles) -> tuple[str, ...]:
	if isinstance(roles, str):
		roles = re.split(r"[,\n]", roles)
	return tuple(dict.fromkeys(str(role).strip() for role in (roles or ()) if str(role).strip()))


def has_any_role(roles, user: str | None = None) -> bool:
	"""Check declarative role requirements with the immutable privileged bypass."""
	user = user or getattr(frappe.session, "user", None)
	if is_privileged_user(user):
		return True
	return bool(get_user_roles(user).intersection(_normalize_roles(roles)))


def _notification_recipient_limit(limit=None) -> int:
	configured = min(get_int_setting("notification_recipient_limit", 100, minimum=1), 500)
	if limit is None:
		return configured
	return min(max(cint(limit), 1), configured)


def get_business_role_users(roles, *, company=None, store=None, limit=None) -> list[str]:
	"""Return bounded enabled System Users whose role and location scope match."""
	roles = _normalize_roles(roles)
	if not roles:
		return []
	recipient_limit = _notification_recipient_limit(limit)
	try:
		from ch_erp15.ch_erp15.notification_router import (
			filter_business_notification_recipients,
			filter_users_by_company,
			get_scoped_users,
		)

		users = get_scoped_users(list(roles), store=store)
		users = filter_users_by_company(users, company)
		users = filter_business_notification_recipients(users)
	except (ImportError, ModuleNotFoundError):
		if company or store:
			return []
		candidate_limit = min(max(recipient_limit * 5, recipient_limit), 2000)
		role_users = frappe.get_all(
			"Has Role",
			filters={"role": ("in", roles), "parenttype": "User"},
			pluck="parent",
			limit=candidate_limit,
		)
		candidates = tuple(dict.fromkeys(
			user for user in role_users if user not in NON_BUSINESS_USERS
		))
		users = frappe.get_all(
			"User",
			filters={
				"name": ("in", candidates),
				"enabled": 1,
				"user_type": "System User",
			},
			pluck="name",
			limit=recipient_limit,
		) if candidates else []
	return sorted(set(users))[:recipient_limit]


def get_business_user_emails(users, *, limit=None) -> list[str]:
	"""Resolve a bounded user-name list to enabled System User emails in one query."""
	recipient_limit = _notification_recipient_limit(limit)
	users = tuple(dict.fromkeys(
		user for user in (users or ()) if user and user not in NON_BUSINESS_USERS
	))[:recipient_limit]
	if not users:
		return []
	rows = frappe.get_all(
		"User",
		filters={
			"name": ("in", users),
			"enabled": 1,
			"user_type": "System User",
			"email": ("!=", ""),
		},
		pluck="email",
		limit=recipient_limit,
	)
	return sorted(set(filter(None, rows)))[:recipient_limit]


def has_role_setting(fieldname: str, defaults=(), user: str | None = None) -> bool:
	return has_any_role(get_role_setting(fieldname, defaults), user=user)


def require_role_setting(fieldname: str, defaults=(), *, action: str | None = None) -> None:
	if has_role_setting(fieldname, defaults):
		return
	frappe.throw(
		_("You do not have permission to {0}. Required role: {1}").format(
			action or _("perform this action"),
			", ".join(sorted(get_role_setting(fieldname, defaults)))),
		frappe.PermissionError)
