<template>
  <aside class="report-evidence" aria-label="Evidence and uncertainty">
    <div class="evidence-heading"><h2>Evidence &amp; uncertainty</h2><span class="confidence">Unvalidated forecast</span></div>
    <p>Simulation findings are scenarios, not calibrated probabilities. Source integrity checks verify the saved reference, not the truth of a claim.</p>
    <ul class="limitations">
      <li v-for="limitation in limitations" :key="limitation">{{ limitation }}</li>
    </ul>
    <p v-if="report.citation_validation?.valid === false" class="evidence-error" role="alert">Some citation references could not be verified. Review the sources before relying on this report.</p>
    <p v-for="warning in report.evidence?.warnings || []" :key="warning" class="evidence-warning">{{ warning }}</p>
    <details class="source-details">
      <summary>Sources ({{ sources.length }}) · {{ citationStatus }}</summary>
      <p v-if="!sources.length">No saved evidence is available for this report. Its claims have not been linked to verified references.</p>
      <ul class="sources">
        <li v-for="source in sources" :id="`source-${source.citation_id}`" :key="source.citation_id">
          <div class="source-heading"><span class="source-kind">{{ sourceKind(source.kind) }}</span><strong>{{ source.source_name || source.source_id || source.citation_id }}</strong></div>
          <p class="source-meta">{{ source.citation_id }}<span v-if="source.reference_time || source.created_at"> · {{ source.reference_time || source.created_at }}</span><span v-else> · Source time unknown</span></p>
          <a :href="evidencePath(source)" @click.prevent="loadSource(source)">{{ sourceLoading === source.citation_id ? 'Checking saved source…' : 'Open saved source' }}</a>
          <p v-if="sourceErrors[source.citation_id]" class="evidence-error" role="alert">{{ sourceErrors[source.citation_id] }}</p>
          <div v-if="verifiedSources[source.citation_id]" class="source-excerpt">
            <p class="verified-label">Saved reference integrity verified</p>
            <blockquote>{{ verifiedSources[source.citation_id].text }}</blockquote>
            <p class="source-meta">SHA-256: {{ verifiedSources[source.citation_id].content_sha256 }}</p>
          </div>
        </li>
      </ul>
    </details>
    <details v-if="report.manifest && Object.keys(report.manifest).length" class="manifest-details">
      <summary>Run provenance</summary>
      <dl><dt>Model</dt><dd>{{ report.manifest.model || 'Unknown' }}</dd><dt>Generated</dt><dd>{{ report.manifest.generated_at || 'Unknown' }}</dd><dt>Model / tool calls</dt><dd>{{ report.manifest.metrics?.llm_calls ?? 'Unknown' }} / {{ report.manifest.metrics?.tool_calls ?? 'Unknown' }}</dd></dl>
      <p v-if="report.manifest.missing_inputs?.length">Missing inputs: {{ report.manifest.missing_inputs.join(', ') }}</p>
      <details><summary>Input hashes &amp; settings</summary><pre>{{ JSON.stringify({ input_hashes: report.manifest.input_hashes || {}, settings: report.manifest.settings || {} }, null, 2) }}</pre></details>
    </details>
  </aside>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import service from '../api'

const props = defineProps({ report: { type: Object, required: true } })
const sources = computed(() => props.report.evidence?.sources || [])
const limitations = computed(() => props.report.uncertainty?.limitations?.length ? props.report.uncertainty.limitations : ['No linked holdout evaluation or calibrated confidence is available.'])
const citationStatus = computed(() => {
  if (!sources.value.length) return 'unavailable'
  if (props.report.citation_validation?.valid === false) return 'reference errors'
  return props.report.citation_validation?.valid === true ? 'reference integrity checked' : 'references not yet checked'
})
const verifiedSources = reactive({})
const sourceErrors = reactive({})
const sourceLoading = ref('')
const sourceKind = kind => ({ source_fact: 'Source evidence', simulation_observation: 'Simulation observation', assumption: 'Assumption', unclassified: 'Unclassified' })[kind] || 'Unclassified'
const evidencePath = source => `/api/evidence/${encodeURIComponent(props.report.report_id)}/${encodeURIComponent(source.citation_id)}`
let version = 0
watch(() => props.report.report_id, () => {
  version += 1
  for (const key of Object.keys(verifiedSources)) delete verifiedSources[key]
  for (const key of Object.keys(sourceErrors)) delete sourceErrors[key]
})
async function loadSource(source) {
  if (sourceLoading.value) return
  const requestVersion = version
  sourceLoading.value = source.citation_id
  sourceErrors[source.citation_id] = ''
  try {
    const result = await service.get(evidencePath(source))
    if (requestVersion !== version) return
    if (result.validation_scope !== 'reference_integrity_only' || result.data?.citation_id !== source.citation_id) throw new Error('The source response could not be verified.')
    verifiedSources[source.citation_id] = result.data
  } catch (error) {
    if (requestVersion === version) sourceErrors[source.citation_id] = error.message || 'Unable to verify the saved source.'
  } finally { sourceLoading.value = '' }
}
</script>

<style scoped>
.report-evidence { margin: 20px 0 30px; padding: 20px; background: #f5f7f8; border: 1px solid #dce3e7; border-radius: 6px; color: #35434e; font: 13px/1.65 'Inter', system-ui, sans-serif; }
.evidence-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 9px; }.evidence-heading h2 { font-size: 16px; margin: 0; }.confidence { font-size: 10px; letter-spacing: .03em; text-transform: uppercase; background: #efe6d6; color: #795621; padding: 3px 7px; border-radius: 3px; }
.limitations { margin: 10px 0; padding-left: 20px; }.limitations li { margin: 5px 0; }.evidence-error { color: #a12a2a; }.evidence-warning { color: #795621; }
summary { cursor: pointer; font-weight: 600; padding: 8px 0; } summary:focus-visible, a:focus-visible { outline: 2px solid #245cc9; outline-offset: 3px; }.source-details, .manifest-details { border-top: 1px solid #dce3e7; margin-top: 10px; padding-top: 4px; }
.sources { list-style: none; padding: 0; }.sources > li { padding: 14px 0; border-top: 1px solid #e1e7ea; scroll-margin-top: 20px; }.source-heading { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }.source-kind { border: 1px solid #c3cfd7; padding: 1px 5px; font-size: 10px; border-radius: 3px; }.source-meta { font-size: 11px; color: #657480; overflow-wrap: anywhere; margin: 6px 0; }a { color: #285b85; }
.source-excerpt { margin-top: 8px; padding: 12px; background: white; }.verified-label { font-size: 11px; color: #356447; }.source-excerpt blockquote { white-space: pre-wrap; overflow-wrap: anywhere; margin: 6px 0; }.manifest-details dl { display: grid; grid-template-columns: 120px 1fr; gap: 5px 10px; }.manifest-details dd { margin: 0; overflow-wrap: anywhere; } pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; }
</style>
