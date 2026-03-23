import { getLmsRoute } from './basePath'

export function normalizeAppPath(path = '') {
	const basePath = getLmsRoute()
	if (!path || path === '/') {
		return basePath
	}

	if (path.startsWith(basePath)) {
		return path
	}

	if (path.startsWith('/')) {
		return getLmsRoute(path)
	}

	return getLmsRoute(path)
}

export function buildAmeideOidcLoginHref(
	path =
		window.location.pathname +
		window.location.search +
		window.location.hash
) {
	const redirectTo = normalizeAppPath(path)
	return `/auth/ameide-oidc?redirect-to=${encodeURIComponent(redirectTo)}`
}

export function redirectToAmeideOidc(path) {
	window.location.assign(buildAmeideOidcLoginHref(path))
}

export function redirectToAmeideLogout() {
	window.location.assign('/auth/ameide-oidc/logout')
}
