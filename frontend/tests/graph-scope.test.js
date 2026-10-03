import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import ReportView from '../src/views/ReportView.vue'
import InteractionView from '../src/views/InteractionView.vue'
import SimulationRunView from '../src/views/SimulationRunView.vue'
import Step3Simulation from '../src/components/Step3Simulation.vue'
import Step4Report from '../src/components/Step4Report.vue'
import Step5Interaction from '../src/components/Step5Interaction.vue'
import GraphPanel from '../src/components/GraphPanel.vue'
import service from '../src/api'

const originalAdapter = service.defaults.adapter
const wrappers = []
const savedReport = {
  report_id: 'report_old', simulation_id: 'simulation_a', graph_id: 'graph_historical',
  status: 'completed', manifest: { execution_id: 'execution_old' }
}
const sourceReplies = {
  '/api/simulation/simulation_a': { project_id: 'project_a', graph_id: 'graph_latest' },
  '/api/simulation/simulation_a/config': { time_config: { minutes_per_round: 30 } },
  '/api/graph/project/project_a': { project_id: 'project_a', graph_id: 'graph_source' },
  '/api/report/report_old': savedReport
}

async function start(component, replies = {}, { path = '/report/report_old', props = {}, realStep3 = false } = {}) {
  const requests = []
  service.defaults.adapter = async config => {
    requests.push(config)
    const reply = replies[config.url] ?? sourceReplies[config.url]
    const data = typeof reply === 'function' ? await reply(config) : reply ?? {}
    return { data: { success: true, data }, status: 200, headers: {}, config }
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/report/:reportId', component: { template: '<div />' } },
    { path: '/interaction/:reportId', component: { template: '<div />' } },
    { path: '/simulation/:simulationId/run', component: { template: '<div />' } }
  ] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, { props, global: {
    plugins: [router, createI18n({ legacy: false, locale: 'en', missingWarn: false, fallbackWarn: false,
      messages: { en: { main: { stepNames: ['Graph', 'Setup', 'Run', 'Report', 'Interaction'] } } } })],
    stubs: { GraphPanel: true, Step3Simulation: !realStep3, Step4Report: true, Step5Interaction: true, LanguageSwitcher: true }
  } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router, requests }
}

function graphRequests(requests) {
  return requests.filter(request => request.url.startsWith('/api/graph/data/')).map(request => request.url)
}

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  service.defaults.adapter = originalAdapter
  vi.useRealTimers()
})

describe('selected report graph', () => {
  it.each([ReportView, InteractionView])('loads and refreshes the historical report graph, without following the latest project/run', async component => {
    const { wrapper, requests } = await start(component, {
      '/api/graph/data/graph_historical': { graph_id: 'graph_historical', nodes: [], edges: [] }
    })
    expect(graphRequests(requests)).toEqual(['/api/graph/data/graph_historical'])
    expect(requests.some(request => request.url.startsWith('/api/simulation/'))).toBe(false)
    const panel = wrapper.getComponent(GraphPanel)
    expect(panel.props('contextLabel')).toBe('Run execution_old')
    panel.vm.$emit('refresh')
    await flushPromises()
    expect(graphRequests(requests)).toEqual(Array(2).fill('/api/graph/data/graph_historical'))
    expect(panel.props('graphData').graph_id).toBe('graph_historical')
  })

  it.each([ReportView, InteractionView])('keeps a report without a graph empty instead of substituting current source data', async component => {
    const { wrapper, requests } = await start(component, {
      '/api/report/report_old': { ...savedReport, graph_id: null }
    })
    wrapper.getComponent(GraphPanel).vm.$emit('refresh')
    await flushPromises()
    expect(graphRequests(requests)).toEqual([])
    expect(wrapper.getComponent(GraphPanel).props('graphData')).toBeNull()
  })

  it.each([ReportView, InteractionView])('discards an older graph response after navigating to another saved report', async component => {
    let finishOldGraph
    const { wrapper, router, requests } = await start(component, {
      '/api/graph/data/graph_historical': () => new Promise(resolve => { finishOldGraph = resolve }),
      '/api/report/report_new': { ...savedReport, report_id: 'report_new', graph_id: 'graph_new', manifest: { execution_id: 'execution_new' } },
      '/api/graph/data/graph_new': { graph_id: 'graph_new', nodes: [], edges: [] }
    })
    await router.push('/report/report_new')
    await flushPromises()
    finishOldGraph({ graph_id: 'graph_historical', nodes: [], edges: [] })
    await flushPromises()
    const panel = wrapper.getComponent(GraphPanel)
    expect(panel.props('graphData').graph_id).toBe('graph_new')
    expect(panel.props('contextLabel')).toBe('Run execution_new')
    panel.vm.$emit('refresh')
    await flushPromises()
    expect(graphRequests(requests).at(-1)).toBe('/api/graph/data/graph_new')
  })

  it('displays a legacy report graph without requiring an execution manifest', async () => {
    const { wrapper, requests } = await start(ReportView, {
      '/api/report/report_old': { report_id: 'report_old', graph_id: 'graph_legacy' },
      '/api/graph/data/graph_legacy': { graph_id: 'graph_legacy', nodes: [], edges: [] }
    })
    expect(graphRequests(requests)).toEqual(['/api/graph/data/graph_legacy'])
    expect(wrapper.getComponent(GraphPanel).props('contextLabel')).toBe('Report report_old')
  })

  it('loads the selected graph when a queued report publishes its metadata', async () => {
    let report = { report_id: 'report_old' }
    const { wrapper, requests } = await start(ReportView, {
      '/api/report/report_old': () => report,
      '/api/graph/data/graph_historical': { graph_id: 'graph_historical', nodes: [], edges: [] }
    })
    expect(graphRequests(requests)).toEqual([])
    report = savedReport
    wrapper.getComponent(Step4Report).vm.$emit('update-status', 'completed')
    await flushPromises()
    expect(graphRequests(requests)).toEqual(['/api/graph/data/graph_historical'])
    expect(wrapper.getComponent(GraphPanel).props('graphData').graph_id).toBe('graph_historical')
  })
})

describe('selected simulation run graph', () => {
  it('receives the saved execution binding from Step3 and keeps manual/timed refresh on that graph', async () => {
    vi.useFakeTimers()
    const { wrapper, requests } = await start(SimulationRunView, {
      '/api/simulation/simulation_a/run-status': { simulation_id: 'simulation_a', runner_status: 'running', execution_id: 'execution_a', execution_graph_id: 'graph_execution' },
      '/api/simulation/simulation_a/run-status/detail': { all_actions: [] },
      '/api/graph/data/graph_execution': { graph_id: 'graph_execution', nodes: [], edges: [] }
    }, { path: '/simulation/simulation_a/run', realStep3: true })
    const panel = wrapper.getComponent(GraphPanel)
    expect(panel.props('graphData').graph_id).toBe('graph_execution')
    expect(panel.props('contextLabel')).toBe('Run execution_a')
    panel.vm.$emit('refresh')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(graphRequests(requests).length).toBeGreaterThanOrEqual(3)
    expect(new Set(graphRequests(requests))).toEqual(new Set(['/api/graph/data/graph_execution']))
    expect(requests.some(request => request.url === '/api/simulation/start')).toBe(false)
  })

  it('receives the new execution graph from the start response', async () => {
    const { wrapper, requests } = await start(SimulationRunView, {
      '/api/simulation/simulation_a/run-status': { runner_status: 'idle' },
      '/api/simulation/start': { runner_status: 'running', execution_id: 'execution_new', execution_graph_id: 'graph_new' },
      '/api/graph/data/graph_new': { graph_id: 'graph_new', nodes: [], edges: [] }
    }, { path: '/simulation/simulation_a/run', realStep3: true })
    expect(graphRequests(requests)).toEqual(['/api/graph/data/graph_new'])
    expect(wrapper.getComponent(GraphPanel).props('contextLabel')).toBe('Run execution_new')
  })

  it('keeps the final graph when an earlier in-flight refresh finishes after completion', async () => {
    let finishEarlierRefresh
    let graphCalls = 0
    const { wrapper } = await start(SimulationRunView, {
      '/api/graph/data/graph_execution': () => {
        graphCalls += 1
        if (graphCalls === 2) return new Promise(resolve => { finishEarlierRefresh = resolve })
        return { graph_id: 'graph_execution', snapshot: graphCalls === 1 ? 'initial' : 'final', nodes: [], edges: [] }
      }
    }, { path: '/simulation/simulation_a/run' })
    const step = wrapper.getComponent(Step3Simulation)
    step.vm.$emit('update-run', { simulation_id: 'simulation_a', execution_id: 'execution_a', execution_graph_id: 'graph_execution' })
    await flushPromises()
    wrapper.getComponent(GraphPanel).vm.$emit('refresh')
    await flushPromises()
    step.vm.$emit('update-status', 'completed')
    await flushPromises()
    finishEarlierRefresh({ graph_id: 'graph_execution', snapshot: 'stale', nodes: [], edges: [] })
    await flushPromises()
    expect(wrapper.getComponent(GraphPanel).props('graphData').snapshot).toBe('final')
  })

  it('clears the previous graph when a run resets and ignores another simulation binding', async () => {
    const { wrapper, requests } = await start(SimulationRunView, {
      '/api/graph/data/graph_old': { graph_id: 'graph_old', nodes: [], edges: [] }
    }, { path: '/simulation/simulation_a/run' })
    const step = wrapper.getComponent(Step3Simulation)
    step.vm.$emit('update-run', { simulation_id: 'simulation_a', execution_id: 'execution_old', execution_graph_id: 'graph_old' })
    await flushPromises()
    expect(wrapper.getComponent(GraphPanel).props('graphData').graph_id).toBe('graph_old')
    step.vm.$emit('update-run', { simulation_id: 'simulation_other', execution_graph_id: 'graph_other' })
    step.vm.$emit('update-run', { simulation_id: 'simulation_a' })
    await flushPromises()
    wrapper.getComponent(GraphPanel).vm.$emit('refresh')
    await flushPromises()
    expect(wrapper.getComponent(GraphPanel).props('graphData')).toBeNull()
    expect(graphRequests(requests)).toEqual(['/api/graph/data/graph_old'])
  })
})

it('pins report-agent chat to the selected historical report', async () => {
  const { wrapper, requests } = await start(Step5Interaction, {
    '/api/report/report_old/agent-log': { logs: [] },
    '/api/simulation/simulation_a/profiles/realtime': { profiles: [] },
    '/api/report/chat': { response: 'Answer from saved report', report_id: 'report_old' }
  }, { props: { reportId: 'report_old', simulationId: 'simulation_a' } })
  await wrapper.get('textarea.chat-input').setValue('Explain this report')
  await wrapper.get('button.send-btn').trigger('click')
  await flushPromises()
  const request = requests.find(item => item.url === '/api/report/chat')
  expect(JSON.parse(request.data)).toMatchObject({
    report_id: 'report_old', simulation_id: 'simulation_a', message: 'Explain this report'
  })
})
