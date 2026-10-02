import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const markdown = new MarkdownIt({ html: false, breaks: true })

const classes = {
  paragraph_open: 'md-p',
  blockquote_open: 'md-quote',
  bullet_list_open: 'md-ul',
  ordered_list_open: 'md-ol',
  code_inline: 'inline-code',
  hr: 'md-hr'
}

// Retain the report/chat styles while letting the parser build valid nested
// lists and escape text, attributes and code before they reach an HTML sink.
markdown.core.ruler.push('report_styles', state => {
  const decorate = tokens => {
    const lists = []
    for (const [index, token] of tokens.entries()) {
      if (classes[token.type]) token.attrJoin('class', classes[token.type])
      if (token.type === 'bullet_list_open' || token.type === 'ordered_list_open') {
        lists.push(token.type)
      } else if (token.type === 'bullet_list_close' || token.type === 'ordered_list_close') {
        lists.pop()
      } else if (token.type === 'list_item_open') {
        token.attrJoin('class', lists.at(-1) === 'ordered_list_open' ? 'md-oli' : 'md-li')
      }
      if (token.type === 'heading_open' || token.type === 'heading_close') {
        token.tag = `h${Math.min(Number(token.tag.slice(1)) + 1, 6)}`
        if (token.type === 'heading_open') token.attrJoin('class', `md-${token.tag}`)
        if (token.type === 'heading_open') {
          const citation = tokens[index + 1]?.content?.match(/^Source (e-[0-9a-f]{24})$/)
          if (citation) token.attrSet('id', `source-${citation[1]}`)
        }
      }
      if (token.children) decorate(token.children)
    }
  }
  decorate(state.tokens)
})

for (const type of ['fence', 'code_block']) {
  const render = markdown.renderer.rules[type]
  markdown.renderer.rules[type] = (...args) => render(...args).replace('<pre>', '<pre class="code-block">')
}

export function renderMarkdown(content) {
  if (typeof content !== 'string' || !content) return ''

  // The enclosing report section already displays its leading level-two title.
  const body = content.replace(/^##\s+.+\n+/, '')
  return DOMPurify.sanitize(markdown.render(body), { USE_PROFILES: { html: true } })
}
