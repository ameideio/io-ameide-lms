import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'
import { redirectToAmeideOidc } from '../utils/auth'

export const usersStore = defineStore('lms-users', () => {
	let userResource = createResource({
		url: 'lms.lms.api.get_user_info',
		onError(error) {
			if (error && error.exc_type === 'AuthenticationError') {
				redirectToAmeideOidc()
			}
		},
	})

	const allUsers = createResource({
		url: 'lms.lms.api.get_all_users',
		cache: ['allUsers'],
	})

	return {
		userResource,
		allUsers,
	}
})
