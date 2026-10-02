import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import MainView from '../src/views/MainView.vue'
import Step2EnvSetup from '../src/components/Step2EnvSetup.vue'
import Step3Simulation from '../src/components/Step3Simulation.vue'
import Step4Report from '../src/components/Step4Report.vue'
import service from '../src/api'

const originalAdapter = service.defaults.adapter
const wrappers = []
async function start(component, replies, props = {}) {
  const requests = []
  service.defaults.adapter = async config => {
    requests.push(config)
    return { data: { success: true, data: replies[config.url] ?? {} }, status: 200, headers: {}, config }
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/process/:projectId', component: { template: '<div />' } }] })
  await router.push('/process/project_a')
  await router.isReady()
  const wrapper = mount(component, {
    props,
    global: {
      plugins: [router, createI18n({ legacy: false, locale: 'en', messages: { en: { main: { stepNames: ['Graph'] } } }, missingWarn: false, fallbackWarn: false })],
      stubs: { GraphPanel: true, Step1GraphBuild: true, LanguageSwitcher: true }
    }
  })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, requests }
}
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  service.defaults.adapter = originalAdapter
  vi.useRealTimers()
})

describe('saved workflow recovery', () => {
  it('shows an interrupted graph job and stops polling without submitting another build', async () => {
    vi.useFakeTimers()
    const { wrapper, requests } = await start(MainView, {
      '/api/graph/project/project_a': { project_id: 'project_a', status: 'graph_building', graph_build_task_id: 'job_a' },
      '/api/graph/task/job_a': { status: 'interrupted', progress: 30 }
    })
    expect(wrapper.get('[role="alert"]').text()).toContain('Graph build interrupted')
    await vi.advanceTimersByTimeAsync(7000)
    expect(requests.filter(request => request.url === '/api/graph/task/job_a')).toHaveLength(1)
    expect(requests.some(request => request.url === '/api/graph/build')).toBe(false)
  })

  it('shows budget exhaustion during preparation and stops polling', async () => {
    vi.useFakeTimers()
    const { wrapper, requests } = await start(Step2EnvSetup, {
      '/api/simulation/prepare': { task_id: 'job_a' },
      '/api/simulation/prepare/status': { status: 'budget_exceeded', error: 'Call limit reached' }
    }, { simulationId: 'simulation_a', systemLogs: [] })
    await vi.advanceTimersByTimeAsync(2200)
    expect(wrapper.get('[role="alert"]').text()).toContain('Preparation budget exceeded. Call limit reached')
    await vi.advanceTimersByTimeAsync(7000)
    expect(requests.filter(request => request.url === '/api/simulation/prepare/status')).toHaveLength(1)
  })

  it.each(['completed', 'failed'])('reopening a %s simulation never starts or force-restarts it', async runnerStatus => {
    const { wrapper, requests } = await start(Step3Simulation, {
      '/api/simulation/simulation_a/run-status': { runner_status: runnerStatus, error_code: runnerStatus === 'failed' ? 'budget_exceeded' : null },
      '/api/simulation/simulation_a/run-status/detail': { all_actions: [] }
    }, { simulationId: 'simulation_a', systemLogs: [] })
    expect(requests.some(request => request.url === '/api/simulation/start')).toBe(false)
    if (runnerStatus === 'failed') {
      expect(wrapper.get('[role="alert"]').text()).toContain('Simulation budget exceeded')
      expect(wrapper.get('button.action-btn.primary').element.disabled).toBe(true)
      expect(wrapper.find('.waiting-state').exists()).toBe(false)
    } else {
      expect(wrapper.get('button.action-btn.primary').element.disabled).toBe(false)
    }
  })

  it('starts an idle simulation without forcing a restart', async () => {
    const { requests } = await start(Step3Simulation, {
      '/api/simulation/simulation_a/run-status': { runner_status: 'idle' },
      '/api/simulation/start': { runner_status: 'running' }
    }, { simulationId: 'simulation_a', systemLogs: [] })
    const starts = requests.filter(request => request.url === '/api/simulation/start')
    expect(starts).toHaveLength(1)
    expect(JSON.parse(starts[0].data).force).toBeUndefined()
  })

  it('shows a durable report interruption even before the report outline exists', async () => {
    vi.useFakeTimers()
    const { wrapper, requests } = await start(Step4Report, {
      '/api/graph/tasks': [{ task_id: 'job_a', status: 'interrupted', metadata: { report_id: 'report_a' } }],
      '/api/report/report_a': { report_id: 'report_a' },
      '/api/report/report_a/agent-log': { logs: [], from_line: 0 },
      '/api/report/report_a/console-log': { logs: [], from_line: 0 }
    }, { reportId: 'report_a', systemLogs: [] })
    expect(wrapper.get('[role="alert"]').text()).toContain('Report interrupted')
    expect(wrapper.find('.waiting-placeholder').exists()).toBe(false)
    expect(wrapper.emitted('update-status').at(-1)).toEqual(['error'])
    const requestCount = requests.length
    await vi.advanceTimersByTimeAsync(7000)
    expect(requests).toHaveLength(requestCount)
  })
})
