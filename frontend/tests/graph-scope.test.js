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

describe('report-bound live interviews', () => {
  const profile = { username: 'Bound agent', profession: 'Analyst' }
  const currentStatus = { simulation_id: 'simulation_a', execution_id: 'execution_old', runner_status: 'completed' }
  const props = { reportId: 'report_old', simulationId: 'simulation_a' }
  const replies = {
    '/api/report/report_old/agent-log': { logs: [] },
    '/api/simulation/simulation_a/run-status': currentStatus,
    '/api/simulation/simulation_a/profiles/realtime': { profiles: [profile] },
    '/api/simulation/interview/batch': { execution_id: 'execution_old', result: { results: { reddit_0: { response: 'Bound live answer' } } } }
  }

  async function chooseAgent(wrapper) {
    await wrapper.get('.agent-pill').trigger('click')
    await wrapper.get('.dropdown-item').trigger('click')
    await wrapper.get('textarea.chat-input').setValue('Explain your decision')
  }

  it('hides newer agents and disables interviews for a historical execution', async () => {
    const { wrapper, requests } = await start(Step5Interaction, {
      ...replies, '/api/simulation/simulation_a/run-status': { ...currentStatus, execution_id: 'execution_new' }
    }, { props })
    expect(wrapper.get('.live-interview-notice').text()).toContain('earlier execution')
    expect(wrapper.get('.survey-pill').element.disabled).toBe(true)
    expect(wrapper.find('.agent-pill').exists()).toBe(false)
    expect(requests.some(request => request.url.endsWith('/profiles/realtime'))).toBe(false)
    expect(wrapper.get('textarea.chat-input').element.disabled).toBe(false)
    expect(wrapper.text()).not.toContain(profile.username)
  })

  it('keeps live chat and survey enabled for the current execution and pins both requests', async () => {
    const { wrapper, requests } = await start(Step5Interaction, replies, { props })
    expect(wrapper.find('.live-interview-notice').exists()).toBe(false)
    await chooseAgent(wrapper)
    await wrapper.get('button.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Bound live answer')
    await wrapper.get('.survey-pill').trigger('click')
    await wrapper.get('.agent-checkbox input').setValue(true)
    await wrapper.get('textarea.survey-input').setValue('Survey question')
    await wrapper.get('.survey-submit-btn').trigger('click')
    await flushPromises()
    const interviews = requests.filter(request => request.url === '/api/simulation/interview/batch').map(request => JSON.parse(request.data))
    expect(interviews).toEqual([
      { simulation_id: 'simulation_a', expected_execution_id: 'execution_old', interviews: [{ agent_id: 0, prompt: 'Explain your decision' }] },
      { simulation_id: 'simulation_a', expected_execution_id: 'execution_old', interviews: [{ agent_id: 0, prompt: 'Survey question' }] }
    ])
    expect(wrapper.get('.survey-results').text()).toContain(profile.username)
  })

  it.each(['legacy', 'unverified'])('disables live actions for a %s execution while keeping saved report chat', async state => {
    const { wrapper, requests } = await start(Step5Interaction, {
      ...replies,
      '/api/report/report_old': state === 'legacy' ? { ...savedReport, manifest: {} } : savedReport,
      '/api/simulation/simulation_a/run-status': {}
    }, { props })
    expect(wrapper.get('.survey-pill').element.disabled).toBe(true)
    expect(wrapper.get('.live-interview-notice').text()).toContain('saved report chat remains available')
    expect(wrapper.get('textarea.chat-input').element.disabled).toBe(false)
    expect(requests.some(request => request.url.endsWith('/profiles/realtime'))).toBe(false)
  })

  it('rejects profiles when a rerun occurs during the profile request', async () => {
    let status = currentStatus
    let finishProfiles
    const { wrapper } = await start(Step5Interaction, {
      ...replies,
      '/api/simulation/simulation_a/run-status': () => status,
      '/api/simulation/simulation_a/profiles/realtime': () => new Promise(resolve => { finishProfiles = resolve })
    }, { props })
    status = { ...currentStatus, execution_id: 'execution_new' }
    finishProfiles({ profiles: [{ username: 'New execution agent' }] })
    await flushPromises()
    expect(wrapper.get('.live-interview-notice').text()).toContain('earlier execution')
    expect(wrapper.text()).not.toContain('New execution agent')
    expect(wrapper.find('.agent-pill').exists()).toBe(false)
  })

  it('checks execution again before sending, including the Enter shortcut', async () => {
    let status = currentStatus
    const { wrapper, requests } = await start(Step5Interaction, {
      ...replies, '/api/simulation/simulation_a/run-status': () => status
    }, { props })
    await chooseAgent(wrapper)
    status = { ...currentStatus, execution_id: 'execution_new' }
    await wrapper.get('textarea.chat-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(requests.some(request => request.url === '/api/simulation/interview/batch')).toBe(false)
    expect(wrapper.get('.live-interview-notice').text()).toContain('earlier execution')
    expect(wrapper.find('.agent-profile-card').exists()).toBe(false)
  })

  it('clears agent state on an observed rerun and discards an in-flight reply', async () => {
    vi.useFakeTimers()
    let status = currentStatus
    let finishInterview
    const { wrapper } = await start(Step5Interaction, {
      ...replies,
      '/api/simulation/simulation_a/run-status': () => status,
      '/api/simulation/interview/batch': () => new Promise(resolve => { finishInterview = resolve })
    }, { props })
    await chooseAgent(wrapper)
    await wrapper.get('button.send-btn').trigger('click')
    await flushPromises()
    status = { ...currentStatus, execution_id: 'execution_new' }
    await vi.advanceTimersByTimeAsync(10000)
    finishInterview({ execution_id: 'execution_old', result: { results: { reddit_0: { response: 'Stale live reply' } } } })
    await flushPromises()
    expect(wrapper.get('.live-interview-notice').text()).toContain('earlier execution')
    expect(wrapper.find('.agent-pill').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Stale live reply')
    expect(wrapper.text()).not.toContain(profile.username)
  })

  it('rejects an interview result identified as a different execution', async () => {
    const { wrapper } = await start(Step5Interaction, {
      ...replies, '/api/simulation/interview/batch': { execution_id: 'execution_other', result: { results: { reddit_0: { response: 'Wrong execution reply' } } } }
    }, { props })
    await chooseAgent(wrapper)
    await wrapper.get('button.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.get('.live-interview-notice').text()).toContain('another execution')
    expect(wrapper.text()).not.toContain('Wrong execution reply')
  })

  it('handles the server execution guard by disabling live actions visibly', async () => {
    const { wrapper } = await start(Step5Interaction, {
      ...replies, '/api/simulation/interview/batch': () => {
        const error = new Error('Execution changed')
        error.response = { status: 409, data: { success: false, error_code: 'execution_changed', error: 'Execution changed' } }
        throw error
      }
    }, { props })
    await chooseAgent(wrapper)
    await wrapper.get('button.send-btn').trigger('click')
    await flushPromises()
    expect(wrapper.get('.live-interview-notice').text()).toContain('simulation execution changed')
    expect(wrapper.get('.survey-pill').element.disabled).toBe(true)
    expect(wrapper.find('.agent-pill').exists()).toBe(false)
    expect(wrapper.get('textarea.chat-input').element.disabled).toBe(false)
  })

  it('keeps a pending live answer in its originating conversation', async () => {
    let finishInterview
    const { wrapper } = await start(Step5Interaction, {
      ...replies, '/api/simulation/interview/batch': () => new Promise(resolve => { finishInterview = resolve })
    }, { props })
    await chooseAgent(wrapper)
    await wrapper.get('button.send-btn').trigger('click')
    await flushPromises()
    const reportTab = wrapper.get('.action-bar-tabs > .tab-pill')
    expect(reportTab.element.disabled).toBe(true)
    await reportTab.trigger('click')
    expect(wrapper.find('.report-agent-tools-card').exists()).toBe(false)
    finishInterview(replies['/api/simulation/interview/batch'])
    await flushPromises()
    expect(wrapper.text()).toContain('Bound live answer')
    await reportTab.trigger('click')
    expect(wrapper.find('.report-agent-tools-card').exists()).toBe(true)
    expect(wrapper.findAll('.message-text').some(message => message.text().includes('Bound live answer'))).toBe(false)
  })
})
