import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import ReportEvidence from '../src/components/ReportEvidence.vue'
import Step4Report from '../src/components/Step4Report.vue'
import Step5Interaction from '../src/components/Step5Interaction.vue'
import service from '../src/api'
import { renderMarkdown } from '../src/utils/markdown'

const originalAdapter = service.defaults.adapter
const wrappers = []
const citation = 'e-123456789012345678901234'
const source = { citation_id: citation, source_id: 'source-1', kind: 'source_fact', text: '<script>unsafe()</script> Saved source text', content_sha256: 'abc123', source_name: 'Input document' }
const report = {
  report_id: 'report_a', status: 'completed',
  outline: { title: 'Saved report', summary: 'Summary', sections: [{ title: 'Findings', content: 'Saved content with [evidence](#source-' + citation + ').' }] },
  evidence: { status: 'available', sources: [source, { ...source, citation_id: 'e-223456789012345678901234', kind: 'simulation_observation', source_name: 'Simulation' }, { ...source, citation_id: 'e-323456789012345678901234', kind: 'assumption', source_name: 'Model assumption' }] },
  citation_validation: { valid: true, scope: 'reference_integrity_only' },
  uncertainty: { calibrated: false, confidence: 'unvalidated', limitations: ['Synthetic agents are not a representative human sample.'] },
  manifest: { model: 'kimi-k2.5', input_hashes: { requirement: 'hash123' }, metrics: { llm_calls: 4, tool_calls: 2 } }
}

function start(component = ReportEvidence, value = report, intercept) {
  const requests = []
  service.defaults.adapter = async config => {
    requests.push(config)
    if (intercept) await intercept(config)
    const data = config.url.includes('/api/evidence/') ? source : config.url.endsWith('/agent-log') ? { logs: [], from_line: 0 } : config.url.endsWith('/console-log') ? { logs: [], from_line: 0 } : value
    return { data: { success: true, data, validation_scope: 'reference_integrity_only' }, status: 200, headers: {}, config }
  }
  const wrapper = mount(component, {
    props: component === ReportEvidence ? { report: value } : { reportId: value.report_id },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en: {} }, missingWarn: false, fallbackWarn: false }), createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })] }
  })
  wrappers.push(wrapper)
  return { wrapper, requests }
}

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  service.defaults.adapter = originalAdapter
})

describe('report evidence', () => {
  it('labels evidence types, unvalidated confidence, limitations and provenance', () => {
    const { wrapper } = start()
    expect(wrapper.text()).toContain('Unvalidated forecast')
    expect(wrapper.text()).toContain('not calibrated probabilities')
    expect(wrapper.text()).toContain('not the truth of a claim')
    expect(wrapper.text()).toContain('Synthetic agents are not a representative human sample.')
    expect(wrapper.findAll('.source-kind').map(label => label.text())).toEqual(['Source evidence', 'Simulation observation', 'Assumption'])
    expect(wrapper.text()).toContain('kimi-k2.5')
    expect(wrapper.text()).toContain('hash123')
  })

  it('opens immutable evidence by report and citation ID, rendering source text inertly', async () => {
    const { wrapper, requests } = start()
    const link = wrapper.get('a')
    expect(link.attributes('href')).toBe(`/api/evidence/report_a/${citation}`)
    expect(wrapper.find('blockquote').exists()).toBe(false)
    await link.trigger('click')
    await flushPromises()
    expect(requests[0].url).toBe(`/api/evidence/report_a/${citation}`)
    expect(wrapper.get('blockquote').text()).toBe(source.text)
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('Saved reference integrity verified')
  })

  it('never claims verification when the evidence endpoint fails integrity checks', async () => {
    const { wrapper } = start(ReportEvidence, report, () => { throw new Error('Evidence integrity mismatch') })
    await wrapper.get('a').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Evidence integrity mismatch')
    expect(wrapper.find('blockquote').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Saved reference integrity verified')
  })

  it('shows missing evidence and uncalibrated fallback for older reports', () => {
    const { wrapper } = start(ReportEvidence, { report_id: 'old_report' })
    expect(wrapper.text()).toContain('No saved evidence is available')
    expect(wrapper.text()).toContain('No linked holdout evaluation or calibrated confidence')
  })

  it.each([['Step4 report', Step4Report], ['Step5 interaction', Step5Interaction]])('%s loads saved evidence and content without relying on agent logs', async (_name, component) => {
    const { wrapper } = start(component)
    await flushPromises()
    expect(wrapper.get('.report-evidence').text()).toContain('Synthetic agents are not a representative human sample.')
    expect(wrapper.get('.generated-content').text()).toContain('Saved content with evidence.')
    expect(wrapper.get('.generated-content a').attributes('href')).toBe(`#source-${citation}`)
    expect(wrapper.find(`#source-${citation}`).exists()).toBe(true)
  })

  it('creates only tightly scoped, safe citation heading anchors', () => {
    const html = renderMarkdown(`### Source ${citation}\n\n### Source constructor\n\n### Source e-123\n\n<script>unsafe()</script>`)
    const node = document.createElement('div')
    node.innerHTML = html
    expect(node.querySelector(`#source-${citation}`)).not.toBeNull()
    expect(node.querySelectorAll('[id]')).toHaveLength(1)
    expect(node.querySelector('script')).toBeNull()
  })
})
