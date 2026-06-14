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
import lms.ameide_service_token as service_token_module
from lms.ameide_service_token import (
	ONBOARDING_SERVICE_ROLE,
	disable_learner,
	ensure_learner,
	ensure_token,
)


class _Role:
	def __init__(self, role):
		self.role = role


class _User:
	def __init__(self, email, roles=None):
		self.email = email
		self.first_name = ""
		self.last_name = ""
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
		return None

	def set_value(self, doctype, name, field, value):
		if doctype == "Role":
			self.state["roles"].setdefault(name, _Doc("Role", name)).update({field: value})

	def commit(self):
		self.committed = True


class _Frappe:
	def __init__(self, users):
		self.state = {"users": users, "roles": {}}
		self.db = _DB(self.state)
		self.users = self.state["users"]
		self.session = types.SimpleNamespace(user="")
		self.cleared_doctypes = []

	def get_doc(self, *args):
		if isinstance(args[0], dict):
			doc = args[0]
			user = _User(doc["email"], [row["role"] for row in doc["roles"]])
			user.first_name = doc.get("first_name", "")
			user.last_name = doc.get("last_name", "")
			user.enabled = doc["enabled"]
			user.user_type = doc["user_type"]
			self.users[user.email] = user
			return user
		return self.users[args[1]]

	def new_doc(self, doctype):
		name = f"{doctype}-{len(self.state['roles']) + 1}"
		doc = _Doc(doctype, name)
		if doctype == "Role":
			self.state["roles"][name] = doc
		return doc

	def clear_cache(self, doctype=None):
		self.cleared_doctypes.append(doctype)

	def get_roles(self, user):
		return [row.role for row in self.users[user].roles]


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
		self.assertEqual(frappe.cleared_doctypes, [])

	def test_updates_existing_user_roles(self):
		users = {"svc@example.com": _User("svc@example.com", ["System Manager"])}
		with self._with_frappe(users):
			ensure_token("svc@example.com", "Service User", ["System Manager", "LMS Manager"])

		self.assertTrue(users["svc@example.com"].saved)
		self.assertEqual(
			[row.role for row in users["svc@example.com"].roles],
			["System Manager", "LMS Manager", ONBOARDING_SERVICE_ROLE],
		)

	def test_converges_onboarding_role_contract(self):
		with self._with_frappe({}) as modules:
			frappe = modules["frappe"]
			ensure_token("svc@example.com", "Service User", [ONBOARDING_SERVICE_ROLE])

		self.assertIn(ONBOARDING_SERVICE_ROLE, [row.role for row in frappe.users["svc@example.com"].roles])
		role = next(doc for doc in frappe.state["roles"].values() if doc.role_name == ONBOARDING_SERVICE_ROLE)
		self.assertEqual(role.desk_access, 0)

	def test_ensure_learner_creates_website_user_with_student_role(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			result = ensure_learner(
				email="learner@example.com",
				full_name="Learner One",
				organization_id="org-1",
				user_id="user-1",
				idempotency_key="seed-lms-1",
			)

		learner = frappe.users["learner@example.com"]
		self.assertTrue(result["created"])
		self.assertEqual(result["user"], "learner@example.com")
		self.assertTrue(learner.inserted)
		self.assertEqual(learner.user_type, "Website User")
		self.assertEqual(learner.first_name, "Learner")
		self.assertEqual(learner.last_name, "One")
		self.assertIn("LMS Student", [row.role for row in learner.roles])

	def test_ensure_learner_updates_existing_user_idempotently(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		learner = _User("learner@example.com", ["Existing Role"])
		with self._with_frappe({"svc@example.com": service_user, "learner@example.com": learner}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			first = ensure_learner("learner@example.com", "Learner One")
			second = ensure_learner("learner@example.com", "Learner One")

		self.assertFalse(first["created"])
		self.assertFalse(second["created"])
		self.assertTrue(learner.saved)
		self.assertEqual(
			[row.role for row in frappe.users["learner@example.com"].roles],
			["Existing Role", "LMS Student"],
		)

	def test_ensure_learner_requires_onboarding_service_role(self):
		user = _User("regular@example.com", ["LMS Student"])
		with self._with_frappe({"regular@example.com": user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "regular@example.com"
			with self.assertRaises(PermissionError):
				ensure_learner("learner@example.com", "Learner One")

	def test_disable_learner_removes_student_role_and_preserves_other_roles(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		learner = _User("learner@example.com", ["Existing Role", "LMS Student"])
		learner.enabled = 1
		with self._with_frappe({"svc@example.com": service_user, "learner@example.com": learner}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			result = disable_learner("learner@example.com", user_id="user-1")

		self.assertTrue(result["removed"])
		self.assertEqual(result["user"], "learner@example.com")
		self.assertTrue(learner.saved)
		self.assertEqual(learner.enabled, 1)
		self.assertEqual([row.role for row in learner.roles], ["Existing Role"])

	def test_disable_learner_disables_sole_student_role_user(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		learner = _User("learner@example.com", ["LMS Student"])
		learner.enabled = 1
		with self._with_frappe({"svc@example.com": service_user, "learner@example.com": learner}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			result = disable_learner("learner@example.com")

		self.assertTrue(result["removed"])
		self.assertTrue(learner.saved)
		self.assertEqual(learner.enabled, 0)
		self.assertEqual(learner.roles, [])

	def test_disable_learner_missing_user_is_idempotent(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			result = disable_learner("missing@example.com")

		self.assertFalse(result["removed"])
		self.assertEqual(result["user"], "missing@example.com")
