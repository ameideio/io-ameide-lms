import importlib.util
import sys
import types
import unittest
from pathlib import Path


class FakeFrappe(types.ModuleType):
	def __getattr__(self, name):
		if name in {"session", "form_dict", "conf"}:
			return getattr(self.local, name)
		raise AttributeError(name)


class TestAmeideOidcPages(unittest.TestCase):
	def _restore_module(self, name, module):
		if module is None:
			sys.modules.pop(name, None)
			return
		sys.modules[name] = module

	def _load_module(self, relative_path):
		original_frappe = sys.modules.get("frappe")
		original_lms = sys.modules.get("lms")
		original_lms_www = sys.modules.get("lms.www")
		original_lms_www_auth = sys.modules.get("lms.www.auth")
		original_lms_www_auth_ameide_oidc = sys.modules.get("lms.www.auth.ameide_oidc")
		original_index = sys.modules.get("lms.www.auth.ameide_oidc.index")
		original_redirect = sys.modules.get("lms.www.auth.ameide_oidc.redirect")
		original_logout = sys.modules.get("lms.www.auth.ameide_oidc.logout")
		frappe = FakeFrappe("frappe")
		frappe.Redirect = type("Redirect", (Exception,), {})
		frappe.local = types.SimpleNamespace(
			flags=types.SimpleNamespace(),
			login_manager=types.SimpleNamespace(logout=lambda: setattr(self, "logout_called", True)),
			form_dict={},
			session=types.SimpleNamespace(data={}),
		)

		index = types.ModuleType("lms.www.auth.ameide_oidc.index")
		index.get_context = lambda context=None: setattr(self, "index_context", context)
		redirect = types.ModuleType("lms.www.auth.ameide_oidc.redirect")
		redirect.get_context = lambda context=None: setattr(self, "redirect_context", context)
		logout = types.ModuleType("lms.www.auth.ameide_oidc.logout")
		logout.get_context = lambda context=None: setattr(self, "logout_context", context)

		lms = types.ModuleType("lms")
		lms.__path__ = [str(Path(__file__).resolve().parent)]
		lms_www = types.ModuleType("lms.www")
		lms_www.__path__ = [str(Path(__file__).resolve().parent / "www")]
		lms_www_auth = types.ModuleType("lms.www.auth")
		lms_www_auth.__path__ = [str(Path(__file__).resolve().parent / "www" / "auth")]
		lms_www_auth_ameide_oidc = types.ModuleType("lms.www.auth.ameide_oidc")
		lms_www_auth_ameide_oidc.__path__ = [
			str(Path(__file__).resolve().parent / "www" / "auth" / "ameide_oidc")
		]

		self.addCleanup(self._restore_module, "frappe", original_frappe)
		self.addCleanup(self._restore_module, "lms", original_lms)
		self.addCleanup(self._restore_module, "lms.www", original_lms_www)
		self.addCleanup(self._restore_module, "lms.www.auth", original_lms_www_auth)
		self.addCleanup(
			self._restore_module,
			"lms.www.auth.ameide_oidc",
			original_lms_www_auth_ameide_oidc,
		)
		self.addCleanup(self._restore_module, "lms.www.auth.ameide_oidc.index", original_index)
		self.addCleanup(self._restore_module, "lms.www.auth.ameide_oidc.redirect", original_redirect)
		self.addCleanup(self._restore_module, "lms.www.auth.ameide_oidc.logout", original_logout)
		sys.modules["frappe"] = frappe
		sys.modules["lms"] = lms
		sys.modules["lms.www"] = lms_www
		sys.modules["lms.www.auth"] = lms_www_auth
		sys.modules["lms.www.auth.ameide_oidc"] = lms_www_auth_ameide_oidc
		sys.modules["lms.www.auth.ameide_oidc.index"] = index
		sys.modules["lms.www.auth.ameide_oidc.redirect"] = redirect
		sys.modules["lms.www.auth.ameide_oidc.logout"] = logout

		module_path = Path(__file__).resolve().parent / relative_path
		spec = importlib.util.spec_from_file_location(f"lms_{relative_path.replace('/', '_')}", module_path)
		module = importlib.util.module_from_spec(spec)
		assert spec and spec.loader
		spec.loader.exec_module(module)
		return module, frappe

	def _load_hooks(self):
		original_frappe = sys.modules.get("frappe")
		original_lms = sys.modules.get("lms")
		original_lms_hooks = sys.modules.get("lms.hooks")

		frappe = FakeFrappe("frappe")
		frappe.local = types.SimpleNamespace(conf={})
		package = types.ModuleType("lms")
		package.__path__ = [str(Path(__file__).resolve().parent)]
		package.__version__ = "0.0.0"

		self.addCleanup(self._restore_module, "frappe", original_frappe)
		self.addCleanup(self._restore_module, "lms", original_lms)
		self.addCleanup(self._restore_module, "lms.hooks", original_lms_hooks)
		sys.modules["frappe"] = frappe
		sys.modules["lms"] = package

		module_path = Path(__file__).resolve().parent / "hooks.py"
		spec = importlib.util.spec_from_file_location("lms.hooks", module_path)
		module = importlib.util.module_from_spec(spec)
		assert spec and spec.loader
		sys.modules["lms.hooks"] = module
		spec.loader.exec_module(module)
		return module

	def test_login_page_redirects_to_oidc(self):
		module, frappe = self._load_module("www/login.py")
		context = types.SimpleNamespace()
		frappe.local.form_dict = {"redirect_to": "/lms/courses"}
		module.get_context(context)
		self.assertIs(self.index_context, context)

	def test_auth_entrypoint_redirects_to_oidc(self):
		module, frappe = self._load_module("www/ameide_oidc.py")
		context = types.SimpleNamespace()
		frappe.local.form_dict = {"redirect-to": "/lms"}
		module.get_context(context)
		self.assertIs(self.index_context, context)

	def test_auth_redirect_page_completes_login(self):
		module, frappe = self._load_module("www/ameide_oidc_redirect.py")
		context = types.SimpleNamespace()
		frappe.local.form_dict = {"code": "code-123", "state": "state-456"}
		module.get_context(context)
		self.assertIs(self.redirect_context, context)

	def test_logout_page_uses_keycloak_logout(self):
		module, frappe = self._load_module("www/logout.py")
		context = types.SimpleNamespace()
		frappe.local.session.data["ameide_oidc_id_token"] = "token-123"
		module.get_context(context)
		self.assertIs(self.logout_context, context)

	def test_hooks_expose_sales_equivalent_ameide_routes(self):
		hooks = self._load_hooks()
		self.assertIn(
			{"from_route": "/auth/ameide-oidc", "to_route": "ameide_oidc"},
			hooks.website_route_rules,
		)
		self.assertIn(
			{"from_route": "/auth/ameide-oidc/redirect", "to_route": "ameide_oidc_redirect"},
			hooks.website_route_rules,
		)
		self.assertIn(
			{"from_route": "/auth/ameide-oidc/logout", "to_route": "ameide_oidc_logout"},
			hooks.website_route_rules,
		)
		self.assertIn(
			{"source": "/login", "target": "/auth/ameide-oidc"},
			hooks.website_redirects,
		)
		self.assertIn(
			{"source": "/logout", "target": "/auth/ameide-oidc/logout"},
			hooks.website_redirects,
		)

	def test_route_targets_have_matching_www_pages(self):
		app_root = Path(__file__).resolve().parent
		for route_target in (
			"ameide_oidc",
			"ameide_oidc_redirect",
			"ameide_oidc_logout",
		):
			module_path = app_root / "www" / f"{route_target}.py"
			template_path = app_root / "www" / f"{route_target}.html"
			self.assertTrue(module_path.is_file(), module_path)
			self.assertTrue(template_path.is_file(), template_path)


if __name__ == "__main__":
	unittest.main()
