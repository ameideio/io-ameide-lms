import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

try:
	import frappe as frappe_module
except ModuleNotFoundError:
	frappe_module = types.ModuleType("frappe")
	frappe_module.__path__ = []
	frappe_module.whitelist = lambda **_kwargs: lambda fn: fn
	sys.modules["frappe"] = frappe_module
	frappe_permissions_module = types.ModuleType("frappe.permissions")
	frappe_permissions_module.get_valid_perms = lambda _doctype: []
	frappe_permissions_module.has_permission = lambda *_args, **_kwargs: False
	sys.modules["frappe.permissions"] = frappe_permissions_module
import lms.ameide_service_token as service_token_module
from lms.ameide_service_token import (
	ONBOARDING_SERVICE_ROLE,
	PERMISSION_CONTRACT_VERSION,
	ensure_token,
	service_token_contract,
)


class _Role:
	def __init__(self, role):
		self.role = role


class _User:
	def __init__(self, email, roles=None):
		self.email = email
		self.enabled = 0
		self.user_type = "Website User"
		self.roles = [_Role(role) for role in (roles or [])]
		self.api_key = f"key-{email}"
		self.inserted = False
		self.saved = False

	def insert(self, ignore_permissions=False):
		self.inserted = ignore_permissions

	def append(self, field, value):
		if field == "roles":
			self.roles.append(_Role(value["role"]))

	def save(self, ignore_permissions=False):
		self.saved = ignore_permissions


class _Doc:
	def __init__(self, doctype, name=None):
		self.doctype = doctype
		self.name = name
		self.saved = False

	def update(self, values):
		for key, value in values.items():
			setattr(self, key, value)

	def save(self, ignore_permissions=False):
		self.saved = ignore_permissions


class _DB:
	def __init__(self, state):
		self.state = state
		self.users = state["users"]
		self.committed = False

	def exists(self, doctype, value):
		if doctype == "User":
			return value in self.users
		if doctype == "Role":
			return value if value in self.state["roles"] else None
		if doctype == "Custom DocPerm":
			for name, doc in self.state["docperms"].items():
				if all(getattr(doc, key) == expected for key, expected in value.items()):
					return name
			return None
		return None

	def set_value(self, doctype, name, field, value):
		if doctype == "Role":
			self.state["roles"].setdefault(name, _Doc("Role", name)).update({field: value})

	def commit(self):
		self.committed = True


class _Frappe:
	def __init__(self, users):
		self.state = {"users": users, "roles": {}, "docperms": {}}
		self.db = _DB(self.state)
		self.users = self.state["users"]
		self.session = types.SimpleNamespace(user="")
		self.cleared_doctypes = []

	def get_doc(self, *args):
		if isinstance(args[0], dict):
			doc = args[0]
			user = _User(doc["email"], [row["role"] for row in doc["roles"]])
			user.enabled = doc["enabled"]
			user.user_type = doc["user_type"]
			self.users[user.email] = user
			return user
		if args[0] == "Custom DocPerm":
			return self.state["docperms"][args[1]]
		return self.users[args[1]]

	def new_doc(self, doctype):
		name = f"{doctype}-{len(self.state['docperms']) + len(self.state['roles']) + 1}"
		doc = _Doc(doctype, name)
		if doctype == "Custom DocPerm":
			self.state["docperms"][name] = doc
		elif doctype == "Role":
			self.state["roles"][name] = doc
		return doc

	def clear_cache(self, doctype=None):
		self.cleared_doctypes.append(doctype)

	def get_roles(self, user):
		return [row.role for row in self.users[user].roles]

	def _get_valid_perms(self, doctype):
		if doctype != "User":
			return []
		return list(self.state["docperms"].values())

	def _has_permission(self, doctype, ptype="read", doc=None, user=None):
		user_roles = set(self.get_roles(user))
		return any(
			getattr(perm, "parent", None) == doctype
			and getattr(perm, "role", None) in user_roles
			and getattr(perm, "permlevel", 0) == 0
			and getattr(perm, ptype, 0)
			for perm in self.state["docperms"].values()
		)


class TestAmeideServiceToken(unittest.TestCase):
	@contextmanager
	def _with_frappe(self, users):
		frappe = _Frappe(users)
		user_module = types.ModuleType("frappe.core.doctype.user.user")
		user_module.generate_keys = lambda email: {"api_secret": f"secret-{email}"}
		with (
			patch.dict(
				sys.modules,
				{
					"frappe": frappe,
					"frappe.core": types.ModuleType("frappe.core"),
					"frappe.core.doctype": types.ModuleType("frappe.core.doctype"),
					"frappe.core.doctype.user": types.ModuleType("frappe.core.doctype.user"),
					"frappe.core.doctype.user.user": user_module,
				},
			) as modules,
			patch.object(service_token_module, "frappe", frappe),
			patch.object(
				service_token_module,
				"frappe_permissions",
				types.SimpleNamespace(
					get_valid_perms=frappe._get_valid_perms,
					has_permission=frappe._has_permission,
				),
			),
		):
			yield modules

	def test_creates_system_user_and_emits_token_result(self):
		with self._with_frappe({}) as modules:
			frappe = modules["frappe"]
			result = ensure_token("svc@example.com", "Service User", '["System Manager"]')

		self.assertFalse(frappe.db.committed)
		self.assertTrue(frappe.users["svc@example.com"].inserted)
		self.assertEqual(frappe.users["svc@example.com"].user_type, "System User")
		self.assertIn(ONBOARDING_SERVICE_ROLE, [row.role for row in frappe.users["svc@example.com"].roles])
		self.assertEqual(result["api_key"], "key-svc@example.com")
		self.assertEqual(result["api_secret"], "secret-svc@example.com")
		self.assertEqual(result["permission_contract_version"], PERMISSION_CONTRACT_VERSION)
		self.assertEqual(frappe.cleared_doctypes, ["User"])

	def test_updates_existing_user_roles(self):
		users = {"svc@example.com": _User("svc@example.com", ["System Manager"])}
		with self._with_frappe(users):
			ensure_token("svc@example.com", "Service User", ["System Manager", "LMS Manager"])

		self.assertTrue(users["svc@example.com"].saved)
		self.assertEqual(
			[row.role for row in users["svc@example.com"].roles],
			["System Manager", "LMS Manager", ONBOARDING_SERVICE_ROLE],
		)

	def test_converges_user_permission_contract(self):
		with self._with_frappe({}) as modules:
			frappe = modules["frappe"]
			ensure_token("svc@example.com", "Service User", [ONBOARDING_SERVICE_ROLE])

		perms = {
			perm.permlevel: perm
			for perm in frappe.state["docperms"].values()
			if perm.parent == "User" and perm.role == ONBOARDING_SERVICE_ROLE
		}
		self.assertEqual(set(perms), {0, 1})
		self.assertTrue(perms[0].read)
		self.assertTrue(perms[0].write)
		self.assertTrue(perms[0].create)
		self.assertTrue(perms[1].read)
		self.assertTrue(perms[1].write)
		self.assertTrue(perms[1].create)

	def test_service_token_contract_reports_required_permissions(self):
		user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			ensure_token("svc@example.com", "Service User", [ONBOARDING_SERVICE_ROLE])
			result = service_token_contract()

		self.assertEqual(result["permission_contract_version"], PERMISSION_CONTRACT_VERSION)
		self.assertEqual(result["user"], "svc@example.com")
		self.assertTrue(result["user_create"])
		self.assertTrue(result["user_read"])
		self.assertTrue(result["user_write"])
		self.assertTrue(result["user_permlevel_1_read"])
		self.assertTrue(result["user_permlevel_1_write"])
