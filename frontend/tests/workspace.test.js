import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import WorkspacePanel from '../src/components/WorkspacePanel.vue'
import service from '../src/api'

const originalAdapter = service.defaults.adapter
const wrappers = []
const status = {
  memory: { backend: 'local', location: 'this computer' },
  llm: { configured: true, provider: 'ollama_cloud', model: 'kimi-k2.5', execution: 'cloud' },
  jobs: { storage: 'sqlite' }
}
const makeBudget = overrides => ({
  run_id: 'project_a', status: 'active', reason: null,
  limits: { max_calls: 20, max_tokens: 20000, max_output_tokens: 5000, max_wall_seconds: 300, max_cost_usd: null },
  usage: { calls: 3, tokens: 142, input_tokens: 100, output_tokens: 42, wall_seconds: 12, estimated_cost_usd: null },
  pricing_configured_models: [], ...overrides
})
const makeJob = overrides => ({ task_id: 'job_a', task_type: 'graph_build', status: 'interrupted', attempts: 1, retryable: true, metadata: { project_id: 'project_a' }, ...overrides })

async function start({ budget = makeBudget(), jobs = [], path = '/process/project_a', projects = [{ project_id: 'project_a', name: 'First project' }], intercept } = {}) {
  const requests = []
  service.defaults.adapter = async config => {
    requests.push(config)
    if (intercept) {
      const result = await intercept(config)
      if (result) return { data: result, status: 200, headers: {}, config }
    }
    const routes = {
      '/api/system/status': status,
      '/api/graph/project/list': projects,
      '/api/budget/project_a': budget,
      '/api/budget/project_b': makeBudget({ run_id: 'project_b', usage: { ...budget.usage, calls: 8 } }),
      '/api/graph/tasks': jobs,
      '/api/report/report_a': { simulation_id: 'simulation_a' },
      '/api/simulation/simulation_a': { project_id: 'project_a' }
    }
    let data = routes[config.url]
    if (config.method === 'put') data = { ...budget, limits: { ...budget.limits, ...JSON.parse(config.data).limits } }
    if (config.url.endsWith('/retry')) data = makeJob({ status: 'pending', attempts: 1 })
    if (config.url.endsWith('/cancel')) data = jobs[0]?.status === 'processing' ? makeJob({ status: 'processing', cancel_requested: true }) : makeJob({ status: 'cancelled' })
    return { data: { success: true, data }, status: 200, headers: {}, config }
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/process/:projectId', component: { template: '<div />' } },
    { path: '/report/:reportId', component: { template: '<div />' } }
  ] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(WorkspacePanel, { attachTo: document.body, global: { plugins: [router] } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, requests }
}

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  service.defaults.adapter = originalAdapter
  vi.restoreAllMocks()
})

describe('workspace controls', () => {
  it('distinguishes local graph storage from Ollama Cloud and explains cloud prompts', async () => {
    const { wrapper } = await start()
    expect(wrapper.text()).toContain('Local graph · SQLite on the application server')
    expect(wrapper.text()).toContain('Ollama Cloud · kimi-k2.5')
    expect(wrapper.text()).toContain('Prompts and relevant source text are sent')
    expect(wrapper.text()).toContain('Estimated costUnknown')
    expect(wrapper.text()).not.toContain('$0')
    expect(wrapper.get('#max_cost_usd').element.disabled).toBe(true)
    expect(wrapper.get('[role="dialog"]').attributes('aria-modal')).toBe('true')
  })

  it('selects the current report project and filters saved jobs', async () => {
    const { wrapper, requests } = await start({ path: '/report/report_a', projects: [{ project_id: 'project_b', name: 'Another' }, { project_id: 'project_a', name: 'Current' }] })
    expect(wrapper.get('#workspace-project').element.value).toBe('project_a')
    expect(requests.find(request => request.url === '/api/graph/tasks').params).toEqual({ project_id: 'project_a' })
    await wrapper.get('#workspace-project').setValue('project_b')
    await flushPromises()
    expect(requests.filter(request => request.url === '/api/graph/tasks').at(-1).params).toEqual({ project_id: 'project_b' })
    expect(wrapper.get('.usage-grid').text()).toContain('Model calls8')
  })

  it('saves numeric caps and retains actual usage without fabricating a monetary cap', async () => {
    const { wrapper, requests } = await start()
    await wrapper.get('#max_calls').setValue('30')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const request = requests.find(request => request.method === 'put')
    expect(JSON.parse(request.data)).toEqual({ limits: { max_calls: 30, max_tokens: 20000, max_output_tokens: 5000, max_wall_seconds: 300 } })
    expect(wrapper.get('.usage-grid').text()).toContain('Model calls3')
    expect(wrapper.text()).toContain('Limits saved. Existing usage was retained.')
  })

  it('rejects fractional call caps before sending a write', async () => {
    const { wrapper, requests } = await start()
    await wrapper.get('#max_calls').setValue('1.5')
    await wrapper.get('form').trigger('submit')
    expect(requests.some(request => request.method === 'put')).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toContain('positive whole number')
  })

  it('enables a monetary cap only when this model has a configured price', async () => {
    const { wrapper, requests } = await start({ budget: makeBudget({ pricing_configured_models: ['kimi-k2.5'], usage: { calls: 3, tokens: 142, output_tokens: 42, wall_seconds: 12, estimated_cost_usd: .12 } }) })
    expect(wrapper.get('#max_cost_usd').element.disabled).toBe(false)
    expect(wrapper.text()).toContain('$0.12')
    await wrapper.get('#max_cost_usd').setValue('2.50')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(JSON.parse(requests.find(request => request.method === 'put').data).limits.max_cost_usd).toBe(2.5)
  })

  it('does not retry interrupted jobs automatically and requires acknowledging repeated effects', async () => {
    const { wrapper, requests } = await start({ jobs: [makeJob()] })
    const retry = wrapper.findAll('button').find(button => button.text() === 'Retry job')
    expect(retry.element.disabled).toBe(true)
    expect(requests.every(request => request.method === 'get')).toBe(true)
    await wrapper.get('.retry-ack input').setValue(true)
    await retry.trigger('click')
    await flushPromises()
    const request = requests.find(request => request.url.endsWith('/retry'))
    expect(JSON.parse(request.data)).toEqual({ acknowledge_effects: true })
    expect(request.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(wrapper.get('.job-status').text()).toBe('pending')
  })

  it('retains a retry idempotency key after a lost response', async () => {
    let attempts = 0
    const { wrapper, requests } = await start({ jobs: [makeJob()], intercept: config => {
      if (config.url.endsWith('/retry') && ++attempts === 1) throw new Error('Connection interrupted')
    } })
    await wrapper.get('.retry-ack input').setValue(true)
    await wrapper.findAll('button').find(button => button.text() === 'Retry job').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Connection interrupted')
    await wrapper.findAll('button').find(button => button.text() === 'Retry job').trigger('click')
    await flushPromises()
    const retries = requests.filter(request => request.url.endsWith('/retry'))
    expect(retries).toHaveLength(2)
    expect(retries[0].headers.get('Idempotency-Key')).toBe(retries[1].headers.get('Idempotency-Key'))
  })

  it('keeps budget-exceeded retries disabled until limits are raised', async () => {
    const { wrapper } = await start({ budget: makeBudget({ status: 'budget_exceeded', reason: 'calls' }), jobs: [makeJob({ status: 'budget_exceeded' })] })
    await wrapper.get('.retry-ack input').setValue(true)
    expect(wrapper.text()).toContain('Budget exceeded: calls')
    expect(wrapper.findAll('button').find(button => button.text() === 'Retry job').element.disabled).toBe(true)
  })

  it('shows cooperative cancellation as requested instead of claiming the running job is stopped', async () => {
    const { wrapper, requests } = await start({ jobs: [makeJob({ status: 'processing', retryable: false })] })
    await wrapper.findAll('button').find(button => button.text() === 'Cancel job').trigger('click')
    await flushPromises()
    expect(requests.filter(request => request.url.endsWith('/cancel'))).toHaveLength(1)
    expect(wrapper.get('.job-status').text()).toBe('processing')
    expect(wrapper.text()).toContain('Cancellation requested; waiting')
  })

  it('contains keyboard focus and closes on Escape', async () => {
    const { wrapper } = await start()
    const close = wrapper.get('[aria-label="Close workspace"]')
    close.element.focus()
    await close.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(wrapper.get('button.primary').element)
    await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
