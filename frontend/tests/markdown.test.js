import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import { JSDOM } from 'jsdom'
import Step4Report from '../src/components/Step4Report.vue'
import Step5Interaction from '../src/components/Step5Interaction.vue'

const wrappers = []

function mountComponent(component, props = {}) {
  const wrapper = mount(component, {
    props,
    global: {
      plugins: [
        createI18n({ legacy: false, locale: 'en', messages: { en: {} }, missingWarn: false, fallbackWarn: false }),
        createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
      ]
    }
  })
  wrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  wrappers.splice(0).reverse().forEach(wrapper => wrapper.unmount())
})

async function renderSection(component, content) {
  const wrapper = mountComponent(component)
  wrapper.vm.reportOutline = { title: 'Report', summary: 'Summary', sections: [{ title: 'Findings' }] }
  wrapper.vm.generatedSections = { 1: content }
  await nextTick()
  return wrapper.get('.generated-content')
}

async function renderChat(content) {
  const wrapper = mountComponent(Step5Interaction)
  wrapper.vm.chatHistory = [{ role: 'assistant', content, timestamp: '2026-01-01T00:00:00Z' }]
  await nextTick()
  return wrapper.get('.message-text')
}

async function renderSurvey(content) {
  const wrapper = mountComponent(Step5Interaction)
  wrapper.vm.activeTab = 'survey'
  wrapper.vm.surveyResults = [{ agent_name: 'Agent', profession: 'Researcher', question: 'Why?', answer: content }]
  await nextTick()
  return wrapper.get('.result-answer')
}

function renderInterview(content, field = 'answer') {
  const report = mountComponent(Step4Report)
  const wrapper = mountComponent(report.vm.InterviewDisplay, {
    result: {
      successCount: 1,
      totalCount: 1,
      topic: 'Feedback',
      summary: field === 'summary' ? content : '',
      interviews: [{
        name: 'Agent',
        role: 'Researcher',
        questions: ['Why?'],
        twitterAnswer: field === 'answer' ? content : 'An answer',
        redditAnswer: '',
        quotes: field === 'quote' ? [content] : []
      }]
    }
  })
  return wrapper.get({ answer: '.answer-text', quote: '.quote-item', summary: '.summary-content' }[field])
}

// Replay the real rendered HTML in a script-enabled DOM and dispatch a failed
// image load: ordinary jsdom defaults cannot detect inline-handler execution.
function expectNoImageHandlerExecution(html) {
  const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'dangerously' })
  try {
    dom.window.markdownXss = 0
    dom.window.document.body.innerHTML = html
    dom.window.document.querySelectorAll('img').forEach(img => {
      img.dispatchEvent(new dom.window.Event('error'))
    })
    expect(dom.window.markdownXss).toBe(0)
  } finally {
    dom.window.close()
  }
}

const sinks = [
  ['Step4 report sections', content => renderSection(Step4Report, content)],
  ['Step5 report sections', content => renderSection(Step5Interaction, content)],
  ['Step5 chat', renderChat],
  ['Step5 survey answers', renderSurvey],
  ['Step4 interview answers', content => renderInterview(content)],
  ['Step4 interview quotes', content => renderInterview(content, 'quote')],
  ['Step4 interview summaries', content => renderInterview(content, 'summary')]
]

describe.each(sinks)('%s', (_name, render) => {
  it('keeps raw HTML inert and prevents image error handlers from executing', async () => {
    const payload = '<img src=x onerror="window.markdownXss=1"><script>window.markdownXss=2</script>'
    const result = await render(payload)
    expectNoImageHandlerExecution(result.html())
    expect(result.find('img, script').exists()).toBe(false)
    expect(result.text()).toContain('<img src=x onerror=')
  })

  it('displays entity-encoded HTML as text without turning it into elements', async () => {
    const result = await render('&#x3C;img src=x onerror="window.markdownXss=1"&#x3E; &amp; safe')
    expectNoImageHandlerExecution(result.html())
    expect(result.find('img').exists()).toBe(false)
    expect(result.text()).toContain('<img src=x onerror="window.markdownXss=1"> & safe')
  })

  it('rejects executable link schemes including entity-obfuscated URLs', async () => {
    const result = await render('[bad](javascript:alert%281%29) [encoded](javascript&#x3a;alert%281%29) <a href="javascript:alert(1)">raw</a>')
    for (const link of result.findAll('a')) {
      expect(link.attributes('href') || '').not.toMatch(/^\s*(?:javascript|vbscript|data):/i)
    }
    expect(result.text()).toContain('bad')
    expect(result.text()).toContain('encoded')
  })
})

describe.each(sinks.slice(0, 5))('%s markdown formatting', (_name, render) => {
  it('preserves nested lists, quotes, emphasis and safe links', async () => {
    const result = await render('- Parent\n  - Child\n\n3. Third\n\n> A **bold** and *emphasized* quote.\n\n[Source](https://example.com/report?q=1&lang=en)')
    expect(result.get('ul > li > ul > li').text()).toBe('Child')
    expect(result.get('ol').attributes('start')).toBe('3')
    expect(result.findAll('ol > li').map(item => item.text())).toEqual(['Third'])
    expect(result.get('blockquote strong').text()).toBe('bold')
    expect(result.get('blockquote em').text()).toBe('emphasized')
    expect(result.get('a').attributes('href')).toBe('https://example.com/report?q=1&lang=en')
  })

  it('keeps code literal, including HTML and markdown inside code blocks', async () => {
    const result = await render('`<img src=x onerror="window.markdownXss=1">`\n\n```html\n<strong>**literal**</strong>\n```')
    expectNoImageHandlerExecution(result.html())
    expect(result.find('img, strong').exists()).toBe(false)
    expect(result.get('code.inline-code').text()).toBe('<img src=x onerror="window.markdownXss=1">')
    expect(result.get('pre code').element.textContent).toBe('<strong>**literal**</strong>\n')
  })
})

it('preserves interview placeholder text through the same safe renderer', () => {
  const result = renderInterview('[无回复]')
  expect(result.classes()).toContain('placeholder-text')
  expect(result.text()).toBe('[无回复]')
})
