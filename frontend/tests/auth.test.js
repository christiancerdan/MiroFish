import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, onMounted } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { AxiosError } from 'axios'
import App from '../src/App.vue'
import service from '../src/api/index.js'

const originalAdapter = service.defaults.adapter
let wrapper
let requests
let protectedMounts
const authenticated = { authenticated: true, configured: true, csrf_token: 'test-session-csrf' }
const anonymous = { authenticated: false, configured: true }
const response = (config, data, status = 200) => ({ data, status, statusText: String(status), headers: {}, config })

async function startApp({ session = anonymous, login = authenticated, loginError, delaySession, failSession = false } = {}) {
  requests = []
  protectedMounts = 0
  service.defaults.adapter = async config => {
    requests.push(config)
    if (config.url === '/api/auth/session') {
      if (failSession) throw new AxiosError('Network Error', 'ERR_NETWORK', config)
      if (delaySession) await delaySession
      return response(config, { success: true, data: session })
    }
    if (config.url === '/api/auth/login') {
      if (loginError) throw new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, response(config, { success: false, error: loginError }, 401))
      return response(config, { success: true, data: login })
    }
    if (config.url === '/api/expired') {
      throw new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, response(config, { success: false, error: 'Authentication required' }, 401))
    }
    return response(config, { success: true, data: anonymous })
  }
  const PrivateRoute = defineComponent({
    setup() {
      onMounted(() => { protectedMounts += 1 })
      return () => h('main', 'Private report contents')
    }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: PrivateRoute }] })
  await router.push('/')
  await router.isReady()
  wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

async function login() {
  await wrapper.get('input[type="password"]').setValue('test-private-owner-key')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

function lastRequest(url) { return requests.filter(request => request.url === url).at(-1) }

afterEach(() => {
  wrapper?.unmount()
  service.defaults.adapter = originalAdapter
  vi.restoreAllMocks()
})

describe('private workspace session boundary', () => {
  it('does not mount private routes until the server has verified a session', async () => {
    let resolveSession
    const delaySession = new Promise(resolve => { resolveSession = resolve })
    await startApp({ session: authenticated, delaySession })
    expect(protectedMounts).toBe(0)
    expect(wrapper.text()).not.toContain('Private report contents')
    resolveSession()
    await flushPromises()
    expect(protectedMounts).toBe(1)
    expect(wrapper.text()).toContain('Private report contents')
  })

  it('sends an access key once, clears the input and adds CSRF to same-origin writes without persisting secrets', async () => {
    const storageWrite = vi.spyOn(Storage.prototype, 'setItem')
    await startApp()
    expect(protectedMounts).toBe(0)
    await login()
    expect(JSON.parse(lastRequest('/api/auth/login').data)).toEqual({ access_key: 'test-private-owner-key' })
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    expect(protectedMounts).toBe(1)
    await service.post('/api/work', { value: 1 })
    expect(lastRequest('/api/work').headers.get('X-CSRF-Token')).toBe('test-session-csrf')
    expect(lastRequest('/api/work').baseURL).toBe('')
    expect(lastRequest('/api/work').withCredentials).toBe(false)
    expect(storageWrite).not.toHaveBeenCalled()
    await service.post('https://other.example/api/work', {})
    expect(lastRequest('https://other.example/api/work').headers.get('X-CSRF-Token')).toBeUndefined()
  })

  it('clears a rejected access key and never logs credential-bearing Axios errors', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    const consoleLog = vi.spyOn(console, 'log').mockImplementation(() => {})
    await startApp({ loginError: 'Invalid access key' })
    await login()
    expect(wrapper.get('input[type="password"]').element.value).toBe('')
    expect(wrapper.text()).toContain('Invalid access key')
    expect(protectedMounts).toBe(0)
    expect(consoleError).not.toHaveBeenCalled()
    expect(consoleLog).not.toHaveBeenCalled()
  })

  it('unmounts private routes and clears the old CSRF token when any API request returns 401', async () => {
    await startApp({ session: authenticated })
    expect(wrapper.text()).toContain('Private report contents')
    await expect(service.get('/api/expired')).rejects.toThrow('Authentication required')
    await flushPromises()
    expect(wrapper.text()).not.toContain('Private report contents')
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
    await service.post('/api/work', {})
    expect(lastRequest('/api/work').headers.get('X-CSRF-Token')).toBeUndefined()
  })

  it('protects logout with CSRF, then removes the private view and stale token', async () => {
    await startApp({ session: authenticated })
    await wrapper.get('button[aria-label="Sign out"]').trigger('click')
    await flushPromises()
    expect(lastRequest('/api/auth/logout').headers.get('X-CSRF-Token')).toBe('test-session-csrf')
    expect(wrapper.text()).not.toContain('Private report contents')
    await service.post('/api/work', {})
    expect(lastRequest('/api/work').headers.get('X-CSRF-Token')).toBeUndefined()
  })

  it('shows setup instructions and no key form if the server has no owner key configured', async () => {
    await startApp({ session: { authenticated: false, configured: false } })
    expect(wrapper.text()).toContain('MIROFISH_ACCESS_KEY')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    expect(protectedMounts).toBe(0)
  })

  it('keeps private routes unmounted when session verification cannot reach the server', async () => {
    await startApp({ failSession: true })
    expect(protectedMounts).toBe(0)
    expect(wrapper.text()).toContain('Retry')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
  })

  it('rejects an incomplete authenticated session without a CSRF token', async () => {
    await startApp({ session: { authenticated: true, configured: true } })
    expect(protectedMounts).toBe(0)
    expect(wrapper.text()).toContain('Retry')
  })
})
