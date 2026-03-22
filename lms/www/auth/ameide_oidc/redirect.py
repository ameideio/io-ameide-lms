from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils.oauth import login_via_oauth2

from lms.ameide_sso.provider import resolve_social_login_key_name

no_cache = 1


def get_context(context=None):
	provider = resolve_social_login_key_name()
	if not provider:
		frappe.respond_as_web_page(_("SSO not configured"), _("Missing an enabled Social Login Key."), http_status_code=500)
		return {}

	code = frappe.form_dict.get("code")
	state = frappe.form_dict.get("state")
	if not (code and state):
		frappe.respond_as_web_page(_("Invalid Request"), _("Missing code or state"), http_status_code=417)
		return {}

	login_via_oauth2(provider, code, state, decoder=json.loads)
	return {}

