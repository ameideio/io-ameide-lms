from __future__ import annotations

import base64
import json

import frappe
from frappe import _
from frappe.integrations.oauth2_logins import decoder_compat
from frappe.utils.oauth import (
	get_email,
	get_oauth2_flow,
	get_oauth2_providers,
	get_redirect_uri,
	login_oauth_user,
	redirect_post_login,
)

from lms.ameide_sso.provider import resolve_social_login_key_name

no_cache = 1


def get_context(context=None):
	provider = resolve_social_login_key_name()
	if not provider:
		frappe.respond_as_web_page(
			_("SSO not configured"), _("Missing an enabled Social Login Key."), http_status_code=500
		)
		return {}

	code = frappe.form_dict.get("code")
	state = frappe.form_dict.get("state")
	if not (code and state):
		frappe.respond_as_web_page(_("Invalid Request"), _("Missing code or state"), http_status_code=417)
		return {}

	flow = get_oauth2_flow(provider)
	oauth_provider = get_oauth2_providers()[provider]
	session = flow.get_auth_session(
		data={
			"code": code,
			"redirect_uri": get_redirect_uri(provider),
			"grant_type": "authorization_code",
		},
		decoder=decoder_compat,
	)
	token_response = json.loads(session.access_token_response.text)
	userinfo = session.get(
		oauth_provider["api_endpoint"],
		params=oauth_provider.get("api_endpoint_args"),
	).json()

	if not (userinfo.get("email_verified") or get_email(userinfo)):
		frappe.throw(_("Email not verified with Keycloak"))

	login_oauth_user(userinfo, provider=provider, state=state)
	_store_id_token(token_response.get("id_token"))
	state_dict = _decode_state(state) or {}
	if frappe.local.response.get("type") != "redirect":
		redirect_post_login(
			desk_user=False,
			redirect_to=state_dict.get("redirect_to"),
			provider=provider,
		)
	return {}


def _store_id_token(id_token: str | None) -> None:
	if not id_token or frappe.session.user == "Guest":
		return

	frappe.session.data.ameide_oidc_id_token = id_token
	if getattr(frappe.local, "session_obj", None):
		frappe.local.session_obj.data.data.ameide_oidc_id_token = id_token
		frappe.local.session_obj.update(force=True)


def _decode_state(state: str) -> dict | None:
	try:
		decoded = base64.b64decode(state)
		return json.loads(decoded.decode("utf-8"))
	except Exception:
		return None
