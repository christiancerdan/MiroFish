import axios from 'axios'
import i18n from '../i18n'
import { clearSession, getCsrfToken } from '../auth/session'

const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  // Same-origin cookies are sent by the browser; cross-origin credentials
  // are deliberately disabled. Vite proxies /api during local development.
  withCredentials: false,
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' }
})

service.interceptors.request.use(config => {
  config.headers['Accept-Language'] = i18n.global.locale.value
  const method = (config.method || 'get').toUpperCase()
  const sameOrigin = new URL(axios.getUri(config), window.location.origin).origin === window.location.origin
  const csrfToken = getCsrfToken()
  if (sameOrigin && csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    config.headers['X-CSRF-Token'] = csrfToken
  }
  return config
})

service.interceptors.response.use(
  response => {
    const res = response.data
    if (!res.success && res.success !== undefined) {
      return Promise.reject(new Error(res.error || res.message || 'Request failed'))
    }
    return res
  },
  error => {
    if (error.response?.status === 401) clearSession()
    const apiError = error.response?.data?.error || error.response?.data?.message
    if (typeof apiError === 'string' && apiError) error.message = apiError
    // Axios errors include the request body and headers. Never log them:
    // a failed login contains the owner's access key.
    return Promise.reject(error)
  }
)

export default service
