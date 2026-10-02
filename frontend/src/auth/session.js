import { reactive, readonly } from 'vue'

// Only the CSRF token is retained in memory. Authentication lives in the
// server's HttpOnly cookie and must be verified after every page reload.
const state = reactive({ status: 'checking', configured: null })
let csrfToken = ''

export const session = readonly(state)
export const getCsrfToken = () => csrfToken

export function beginSessionCheck() {
  csrfToken = ''
  state.status = 'checking'
  state.configured = null
}

export function applySession(data) {
  if (!data || typeof data.authenticated !== 'boolean' || typeof data.configured !== 'boolean' ||
      (data.authenticated && (!data.configured || typeof data.csrf_token !== 'string' || !data.csrf_token))) {
    throw new Error('Unable to verify this session. Please retry.')
  }
  csrfToken = data.authenticated ? data.csrf_token : ''
  state.configured = data.configured
  state.status = data.authenticated ? 'authenticated' : 'anonymous'
}

export function clearSession() {
  csrfToken = ''
  state.status = 'anonymous'
  if (state.configured === null) state.configured = true
}
