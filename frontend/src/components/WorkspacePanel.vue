<template>
  <div class="workspace-backdrop" @click.self="$emit('close')">
    <section ref="panel" class="workspace-panel" role="dialog" aria-modal="true" aria-labelledby="workspace-title" tabindex="-1" @keydown="handleKeydown">
      <header class="workspace-header">
        <div><p class="eyebrow">MIROFISH</p><h2 id="workspace-title">Workspace</h2></div>
        <button type="button" aria-label="Close workspace" @click="$emit('close')">Close</button>
      </header>
      <p v-if="loading" role="status">Loading workspace…</p>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <section v-if="status" class="workspace-section" aria-labelledby="storage-title">
        <h3 id="storage-title">Memory &amp; model</h3>
        <dl class="facts">
          <div><dt>Graph storage</dt><dd>{{ status.memory?.backend === 'local' ? 'Local graph · SQLite on the application server' : 'Zep Cloud' }}</dd></div>
          <div><dt>Model provider</dt><dd>{{ providerName }} · {{ status.llm?.model || 'Not configured' }}</dd></div>
          <div><dt>Model execution</dt><dd>{{ status.llm?.configured ? executionLabel : 'Not configured' }}</dd></div>
          <div><dt>Saved jobs</dt><dd>{{ status.jobs?.storage === 'sqlite' ? 'SQLite on the application server' : status.jobs?.storage }} · queued jobs recover automatically</dd></div>
        </dl>
        <p class="notice">{{ modelDisclosure }}</p>
        <p class="hint">Interrupted jobs wait for your review and retry.</p>
      </section>
      <section class="workspace-section" aria-labelledby="project-title">
        <h3 id="project-title">Project controls</h3>
        <label for="workspace-project">Project</label>
        <select id="workspace-project" v-model="selectedProject" :disabled="loading || saving || !!actionBusy">
          <option value="">{{ projects.length ? 'Choose a project' : 'No projects yet' }}</option>
          <option v-for="project in projects" :key="project.project_id" :value="project.project_id">{{ project.name || project.project_name || project.project_id }}</option>
        </select>
        <p v-if="!selectedProject" class="hint">Create or select a project to view its spending limits and jobs.</p>
        <p v-if="projectLoading" role="status">Loading project controls…</p>
        <p v-if="projectError" class="notice error" role="alert">{{ projectError }}</p>
        <template v-if="selectedProject">
          <div class="section-heading"><h3>Spending limits</h3><button type="button" :disabled="projectLoading || saving || !!actionBusy" @click="loadProject">Refresh usage &amp; jobs</button></div>
          <template v-if="budget">
            <p v-if="budget.status === 'budget_exceeded'" class="notice" role="status">Budget exceeded: {{ budget.reason || 'limit reached' }}. Increase the relevant limit, then review the interrupted job before retrying.</p>
            <dl class="usage-grid">
              <div><dt>Model calls</dt><dd>{{ number(budget.usage.calls) }}</dd></div>
              <div><dt>Total tokens</dt><dd>{{ number(budget.usage.tokens) }}</dd></div>
              <div><dt>Output tokens</dt><dd>{{ number(budget.usage.output_tokens) }}</dd></div>
              <div><dt>Active model time</dt><dd>{{ number(Math.ceil(budget.usage.wall_seconds || 0)) }} s</dd></div>
              <div><dt>Estimated cost</dt><dd>{{ formatCost(budget.usage.estimated_cost_usd) }}</dd></div>
            </dl>
            <form class="limit-form" @submit.prevent="saveLimits">
              <label v-for="field in limitFields" :key="field.key" :for="field.key">{{ field.label }}<input :id="field.key" v-model="limits[field.key]" type="number" min="1" max="1000000000000" step="1" required :disabled="saving" /></label>
              <label for="max_cost_usd">Cost cap (USD, optional)<input id="max_cost_usd" v-model="limits.max_cost_usd" type="number" min="0.000001" max="1000000000" step="any" placeholder="No monetary cap" :disabled="saving || !pricingAvailable" /></label>
              <p class="hint form-wide">{{ pricingAvailable ? 'Cost estimates use configured model prices. Leave the cost cap blank for no monetary cap.' : 'Cost is unknown without configured pricing for this model. Call, token, and active-time limits still apply.' }}</p>
              <p class="hint form-wide">Usage is cumulative across this project. Once work begins, limits may only be increased. Active time counts model activity, including overlapping calls once; idle time is excluded.</p>
              <p v-if="budgetError" class="error form-wide" role="alert">{{ budgetError }}</p>
              <p v-if="saved" class="form-wide" role="status">Limits saved. Existing usage was retained.</p>
              <button class="primary form-wide" type="submit" :disabled="saving || projectLoading">{{ saving ? 'Saving…' : 'Save limits' }}</button>
            </form>
          </template>
          <div class="section-heading"><h3>Saved jobs</h3><span class="hint">{{ jobs.length }}</span></div>
          <p v-if="!projectLoading && !jobs.length" class="hint">No saved jobs for this project.</p>
          <p v-if="actionError" class="notice error" role="alert">{{ actionError }}</p>
          <ul class="job-list">
            <li v-for="job in jobs" :key="job.task_id" class="job">
              <div class="job-heading"><strong>{{ job.task_type.replaceAll('_', ' ') }}</strong><span :class="['job-status', { attention: ['interrupted', 'budget_exceeded', 'failed'].includes(job.status) }]">{{ job.status.replaceAll('_', ' ') }}</span></div>
              <p class="hint job-id">{{ job.task_id }} · attempt {{ job.attempts || 0 }}</p>
              <p v-if="job.message">{{ job.message }}</p>
              <p v-if="job.error" class="error">{{ job.error }}</p>
              <p v-if="job.cancel_requested && job.status === 'processing'" role="status">Cancellation requested; waiting for the current operation to stop.</p>
              <template v-if="job.retryable && ['failed', 'interrupted', 'budget_exceeded'].includes(job.status)">
                <label class="retry-ack"><input v-model="retryAcknowledged[job.task_id]" type="checkbox" /> I understand retry may repeat model calls or external effects and add to usage.</label>
                <button type="button" :disabled="!retryAcknowledged[job.task_id] || !!actionBusy || budget?.status === 'budget_exceeded'" @click="runAction(job, 'retry')">{{ actionBusy === job.task_id ? 'Working…' : 'Retry job' }}</button>
              </template>
              <button v-if="['pending', 'processing', 'interrupted'].includes(job.status)" type="button" :disabled="!!actionBusy || job.cancel_requested" @click="runAction(job, 'cancel')">Cancel job</button>
            </li>
          </ul>
        </template>
      </section>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import service from '../api'

const emit = defineEmits(['close'])
const route = useRoute()
const panel = ref(null)
const status = ref(null)
const projects = ref([])
const selectedProject = ref('')
const budget = ref(null)
const jobs = ref([])
const limits = reactive({})
const loading = ref(true)
const projectLoading = ref(false)
const saving = ref(false)
const saved = ref(false)
const error = ref('')
const projectError = ref('')
const budgetError = ref('')
const actionError = ref('')
const actionBusy = ref('')
const retryAcknowledged = reactive({})
const retryKeys = new Map()
let loadVersion = 0
let previousFocus
const limitFields = [
  { key: 'max_calls', label: 'Maximum model calls' },
  { key: 'max_tokens', label: 'Maximum total tokens' },
  { key: 'max_output_tokens', label: 'Maximum output tokens' },
  { key: 'max_wall_seconds', label: 'Maximum active seconds' }
]
const providerName = computed(() => ({ ollama_cloud: 'Ollama Cloud', openai: 'OpenAI compatible' })[status.value?.llm?.provider] || status.value?.llm?.provider || 'Unknown')
const executionLabel = computed(() => ({ cloud: 'Cloud', local: 'Local endpoint', remote: 'Remote provider', unknown: 'Unknown' })[status.value?.llm?.execution] || 'Unknown')
const modelDisclosure = computed(() => {
  if (status.value?.llm?.execution === 'local') return 'The model endpoint is local. Cloud model names or proxy configuration can still route prompts remotely.'
  if (['cloud', 'remote'].includes(status.value?.llm?.execution)) return 'Prompts and relevant source text are sent to the configured model provider. Local graph storage does not make model processing private or offline.'
  return 'Model routing is not configured or could not be verified. Local graph storage alone does not guarantee offline processing.'
})
const pricingAvailable = computed(() => budget.value?.pricing_configured_models?.includes(status.value?.llm?.model) || false)
const number = value => Number(value || 0).toLocaleString()
const formatCost = value => value == null ? 'Unknown' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 6 }).format(value)

function applyBudget(data) {
  budget.value = data
  Object.assign(limits, data.limits, { max_cost_usd: data.limits.max_cost_usd ?? '' })
}

async function currentProject() {
  if (route.params.projectId) return route.params.projectId
  let simulationId = route.params.simulationId
  if (route.params.reportId) {
    const report = await service.get(`/api/report/${encodeURIComponent(route.params.reportId)}`)
    simulationId = report.data?.simulation_id
  }
  if (simulationId) {
    const simulation = await service.get(`/api/simulation/${encodeURIComponent(simulationId)}`)
    return simulation.data?.project_id || ''
  }
  return ''
}

async function loadProject() {
  const projectId = selectedProject.value
  const version = ++loadVersion
  budget.value = null
  jobs.value = []
  saved.value = false
  projectError.value = ''
  budgetError.value = ''
  actionError.value = ''
  if (!projectId) { projectLoading.value = false; return }
  projectLoading.value = true
  const results = await Promise.allSettled([
    service.get(`/api/budget/${encodeURIComponent(projectId)}`),
    service.get('/api/graph/tasks', { params: { project_id: projectId } })
  ])
  if (version !== loadVersion) return
  if (results[0].status === 'fulfilled') applyBudget(results[0].value.data)
  if (results[1].status === 'fulfilled') jobs.value = results[1].value.data || []
  const failures = results.filter(result => result.status === 'rejected')
  if (failures.length) projectError.value = failures.map(result => result.reason.message || 'Unable to load project controls.').join(' ')
  projectLoading.value = false
}

async function saveLimits() {
  budgetError.value = ''
  saved.value = false
  const parsed = {}
  for (const field of limitFields) {
    const value = Number(limits[field.key])
    if (!Number.isSafeInteger(value) || value < 1 || value > 1e12) {
      budgetError.value = `${field.label} must be a positive whole number.`
      return
    }
    parsed[field.key] = value
  }
  if (pricingAvailable.value) {
    parsed.max_cost_usd = limits.max_cost_usd === '' ? null : Number(limits.max_cost_usd)
    if (parsed.max_cost_usd !== null && (!Number.isFinite(parsed.max_cost_usd) || parsed.max_cost_usd <= 0 || parsed.max_cost_usd > 1e9)) {
      budgetError.value = 'Cost cap must be a positive amount or blank.'
      return
    }
  }
  saving.value = true
  try {
    const result = await service.put(`/api/budget/${encodeURIComponent(selectedProject.value)}`, { limits: parsed })
    applyBudget(result.data)
    saved.value = true
  } catch (err) { budgetError.value = err.message || 'Unable to save limits.' }
  finally { saving.value = false }
}

async function runAction(job, action) {
  if (actionBusy.value || (action === 'retry' && !retryAcknowledged[job.task_id])) return
  actionBusy.value = job.task_id
  actionError.value = ''
  try {
    let config
    if (action === 'retry') {
      if (!retryKeys.has(job.task_id)) retryKeys.set(job.task_id, crypto.randomUUID())
      config = { headers: { 'Idempotency-Key': retryKeys.get(job.task_id) } }
    }
    const result = await service.post(`/api/graph/task/${encodeURIComponent(job.task_id)}/${action}`, action === 'retry' ? { acknowledge_effects: true } : {}, config)
    jobs.value = jobs.value.map(item => item.task_id === job.task_id ? result.data : item)
    retryKeys.delete(job.task_id)
    retryAcknowledged[job.task_id] = false
  } catch (err) { actionError.value = err.message || `Unable to ${action} job.` }
  finally { actionBusy.value = '' }
}

function handleKeydown(event) {
  if (event.key === 'Escape') { emit('close'); return }
  if (event.key !== 'Tab') return
  const elements = [...panel.value.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]')]
  const first = elements[0]
  const last = elements.at(-1)
  if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.value)) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.value)) { event.preventDefault(); first?.focus() }
}

watch(selectedProject, loadProject)
onMounted(async () => {
  previousFocus = document.activeElement
  await nextTick()
  panel.value?.focus()
  const results = await Promise.allSettled([service.get('/api/system/status'), service.get('/api/graph/project/list'), currentProject()])
  if (results[0].status === 'fulfilled') status.value = results[0].value.data
  if (results[1].status === 'fulfilled') projects.value = results[1].value.data || []
  if (results[0].status === 'rejected' || results[1].status === 'rejected') error.value = 'Some workspace details could not be loaded. Close and reopen to retry.'
  selectedProject.value = results[2].status === 'fulfilled' && results[2].value ? results[2].value : (projects.value.length === 1 ? projects.value[0].project_id : '')
  loading.value = false
})
onUnmounted(() => { loadVersion += 1; previousFocus?.focus() })
</script>

<style scoped>
.workspace-backdrop { position: fixed; inset: 0; z-index: 500; background: #10182066; display: flex; justify-content: flex-end; }
.workspace-panel { width: min(620px, 100%); height: 100dvh; overflow-y: auto; background: #fff; color: #182026; padding: 28px; box-shadow: -12px 0 40px #0002; font: 14px/1.6 'Space Grotesk', system-ui, sans-serif; }
.workspace-header, .section-heading, .job-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.eyebrow { font-size: 10px; letter-spacing: .15em; color: #5d6973; }
h2 { font-size: 25px; } h3 { font-size: 15px; font-weight: 650; }
.workspace-section { margin-top: 26px; padding-top: 22px; border-top: 1px solid #e0e5e7; }
.facts { margin: 14px 0; } .facts > div { display: grid; grid-template-columns: 120px 1fr; gap: 12px; margin: 7px 0; }
dt, .hint { color: #647079; font-size: 12px; } dd { margin: 0; overflow-wrap: anywhere; }
.notice { padding: 12px; background: #f3f6f8; border-left: 3px solid #8296a5; margin: 12px 0; font-size: 12px; }
.error { color: #a32b2b; } .notice.error { background: #fff4f2; border-color: #b45042; }
button, input, select { font: inherit; border: 1px solid #cdd5d9; border-radius: 5px; padding: 8px 10px; background: #fff; color: inherit; }
button { cursor: pointer; font-size: 12px; } button:disabled, input:disabled, select:disabled { opacity: .55; cursor: default; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid #245cc9; outline-offset: 2px; }
select { width: 100%; margin: 7px 0; } label { display: block; font-size: 12px; }
.section-heading { margin: 24px 0 12px; }
.usage-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; background: #f7f9fa; padding: 14px; margin-bottom: 16px; }
.usage-grid dd { font-size: 18px; font-weight: 600; }
.limit-form { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.limit-form input { display: block; width: 100%; margin-top: 4px; }.form-wide { grid-column: 1 / -1; }
.primary { background: #182026; color: #fff; border-color: #182026; }
.job-list { list-style: none; padding: 0; } .job { border-top: 1px solid #e4e9eb; padding: 16px 0; } .job p { margin: 6px 0; font-size: 12px; overflow-wrap: anywhere; }.job-id { font-family: monospace; }
.job-status { border-radius: 4px; padding: 3px 7px; background: #edf1f3; font-size: 11px; white-space: nowrap; }.job-status.attention { background: #fff0d8; color: #775114; }
.retry-ack { display: flex; align-items: flex-start; gap: 8px; margin: 10px 0; }.retry-ack input { margin-top: 3px; }.job button { margin-right: 8px; }
@media (max-width: 480px) { .workspace-panel { padding: 20px; }.usage-grid { grid-template-columns: repeat(2, 1fr); }.facts > div { grid-template-columns: 100px 1fr; }.limit-form { grid-template-columns: 1fr; } }
</style>
