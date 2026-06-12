import json

import frappe

PERMISSION_CONTRACT_VERSION = "lms-user-upsert-v1"
ONBOARDING_SERVICE_ROLE = "Ameide LMS Onboarding"
USER_DOCTYPE = "User"


def _truthy(value) -> bool:
	return bool(int(value or 0))


def _normalize_roles(roles: str | list[str] | tuple[str, ...]) -> list[str]:
	if isinstance(roles, str):
		roles = json.loads(roles)
	if not isinstance(roles, list | tuple):
		raise ValueError("roles must be a list")

	normalized = [str(role).strip() for role in roles if str(role).strip()]
	if not normalized:
		raise ValueError("roles must not be empty")
	return normalized


def _ensure_role(role_name: str) -> None:
	if frappe.db.exists("Role", role_name):
		frappe.db.set_value("Role", role_name, "desk_access", 0)
		return

	role = frappe.new_doc("Role")
	role.update({"role_name": role_name, "home_page": "", "desk_access": 0})
	role.save(ignore_permissions=True)


def _ensure_custom_docperm(role_name: str, permlevel: int, read: int, write: int, create: int) -> None:
	filters = {"parent": USER_DOCTYPE, "role": role_name, "permlevel": permlevel}
	fields = {
		"read": read,
		"select": read,
		"write": write,
		"create": create,
		"permlevel": permlevel,
	}
	name = frappe.db.exists("Custom DocPerm", filters)
	if name:
		doc = frappe.get_doc("Custom DocPerm", name)
	else:
		doc = frappe.new_doc("Custom DocPerm")
		doc.update(
			{
				"parent": USER_DOCTYPE,
				"parentfield": "permissions",
				"parenttype": "DocType",
				"role": role_name,
			}
		)
	doc.update(fields)
	doc.save(ignore_permissions=True)


def _ensure_onboarding_permission_contract() -> None:
	_ensure_role(ONBOARDING_SERVICE_ROLE)
	_ensure_custom_docperm(ONBOARDING_SERVICE_ROLE, 0, read=1, write=1, create=1)
	_ensure_custom_docperm(ONBOARDING_SERVICE_ROLE, 1, read=1, write=1, create=1)
	frappe.clear_cache(doctype=USER_DOCTYPE)


def _user_role_permissions(user: str) -> list:
	user_roles = set(frappe.get_roles(user))
	return [
		perm
		for perm in frappe.permissions.get_valid_perms(USER_DOCTYPE)
		if getattr(perm, "role", None) in user_roles
	]


def _has_role_permission(user: str, action: str, permlevel: int) -> bool:
	return any(
		int(getattr(perm, "permlevel", 0) or 0) == permlevel and _truthy(getattr(perm, action, 0))
		for perm in _user_role_permissions(user)
	)


@frappe.whitelist(methods=["GET"])
def service_token_contract() -> dict[str, object]:
	user = frappe.session.user
	return {
		"permission_contract_version": PERMISSION_CONTRACT_VERSION,
		"user": user,
		"user_create": bool(frappe.permissions.has_permission(USER_DOCTYPE, "create", user=user)),
		"user_read": bool(frappe.permissions.has_permission(USER_DOCTYPE, "read", user=user)),
		"user_write": bool(frappe.permissions.has_permission(USER_DOCTYPE, "write", user=user)),
		"user_permlevel_1_read": _has_role_permission(user, "read", 1),
		"user_permlevel_1_write": _has_role_permission(user, "write", 1),
	}


@frappe.whitelist(methods=["POST"])
def ensure_token(email: str, full_name: str, roles: str | list[str] | tuple[str, ...]) -> dict[str, str]:
	import frappe
	from frappe.core.doctype.user.user import generate_keys

	roles = _normalize_roles(roles)
	_ensure_onboarding_permission_contract()
	if ONBOARDING_SERVICE_ROLE not in roles:
		roles.append(ONBOARDING_SERVICE_ROLE)
	parts = full_name.split(" ", 1)

	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": parts[0],
				"last_name": parts[1] if len(parts) > 1 else "",
				"enabled": 1,
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles],
			}
		)
		user.insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)
		user.enabled = 1
		user.user_type = "System User"
		existing_roles = {row.role for row in user.roles}
		for role in roles:
			if role not in existing_roles:
				user.append("roles", {"role": role})
		user.save(ignore_permissions=True)

	keys = generate_keys(email)
	user = frappe.get_doc("User", email)
	result = {
		"email": email,
		"api_key": user.api_key,
		"api_secret": keys.get("api_secret") if isinstance(keys, dict) else "",
		"permission_contract_version": PERMISSION_CONTRACT_VERSION,
	}
	return result
