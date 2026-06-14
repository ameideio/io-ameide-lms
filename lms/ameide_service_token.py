import json

import frappe

ONBOARDING_SERVICE_ROLE = "Ameide LMS Onboarding"
USER_DOCTYPE = "User"


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


def _ensure_onboarding_role_contract() -> None:
	_ensure_role(ONBOARDING_SERVICE_ROLE)


def _split_name(full_name: str) -> tuple[str, str]:
	parts = str(full_name or "").strip().split(" ", 1)
	first_name = parts[0] if parts and parts[0] else "Learner"
	last_name = parts[1] if len(parts) > 1 else ""
	return first_name, last_name


def _ensure_user_role(user, role: str) -> None:
	existing_roles = {row.role for row in user.roles}
	if role in existing_roles:
		return
	user.append("roles", {"role": role})


def _remove_user_role(user, role: str) -> bool:
	kept = [row for row in user.roles if row.role != role]
	if len(kept) == len(user.roles):
		return False
	user.roles = kept
	return True


def _require_onboarding_service_role() -> None:
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	if ONBOARDING_SERVICE_ROLE not in roles:
		raise PermissionError(f"{ONBOARDING_SERVICE_ROLE} role required")


@frappe.whitelist(methods=["POST"])
def ensure_learner(
	email: str,
	full_name: str,
	organization_id: str = "",
	user_id: str = "",
	idempotency_key: str = "",
) -> dict[str, object]:
	_require_onboarding_service_role()
	first_name, last_name = _split_name(full_name)
	email = str(email or "").strip()
	if not email:
		raise ValueError("email is required")

	created = False
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
				"enabled": 1,
				"user_type": "Website User",
				"send_welcome_email": 0,
				"roles": [{"role": "LMS Student"}],
			}
		)
		user.insert(ignore_permissions=True)
		created = True
	else:
		user = frappe.get_doc("User", email)
		user.enabled = 1
		if not getattr(user, "first_name", ""):
			user.first_name = first_name
		if last_name and not getattr(user, "last_name", ""):
			user.last_name = last_name
		_ensure_user_role(user, "LMS Student")
		user.save(ignore_permissions=True)

	return {
		"email": email,
		"user": email,
		"created": created,
		"organization_id": organization_id,
		"user_id": user_id,
		"idempotency_key": idempotency_key,
	}


@frappe.whitelist(methods=["POST"])
def disable_learner(
	email: str,
	full_name: str = "",
	organization_id: str = "",
	user_id: str = "",
	idempotency_key: str = "",
) -> dict[str, object]:
	_require_onboarding_service_role()
	email = str(email or "").strip()
	if not email:
		raise ValueError("email is required")
	if not frappe.db.exists("User", email):
		return {
			"email": email,
			"user": email,
			"removed": False,
			"organization_id": organization_id,
			"user_id": user_id,
			"idempotency_key": idempotency_key,
		}

	user = frappe.get_doc("User", email)
	removed = _remove_user_role(user, "LMS Student")
	if removed:
		if not user.roles:
			user.enabled = 0
		user.save(ignore_permissions=True)

	return {
		"email": email,
		"user": email,
		"removed": removed,
		"organization_id": organization_id,
		"user_id": user_id,
		"idempotency_key": idempotency_key,
	}


@frappe.whitelist(methods=["POST"])
def ensure_token(email: str, full_name: str, roles: str | list[str] | tuple[str, ...]) -> dict[str, str]:
	import frappe
	from frappe.core.doctype.user.user import generate_keys

	roles = _normalize_roles(roles)
	_ensure_onboarding_role_contract()
	if ONBOARDING_SERVICE_ROLE not in roles:
		roles.append(ONBOARDING_SERVICE_ROLE)
	first_name, last_name = _split_name(full_name)

	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
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
	}
	return result
