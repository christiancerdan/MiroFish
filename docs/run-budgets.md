# Per-project model budgets

A project ID is the budget identity across ontology generation, graph work, profile/configuration preparation, simulation agents and reports. Retrying a stage uses the same ledger. The authenticated budget API is `GET /api/budget/<project_id>` and `PUT /api/budget/<project_id>` with a body such as:

```json
{"limits":{"max_calls":1000,"max_tokens":1000000,"max_output_tokens":250000,"max_wall_seconds":3600,"max_cost_usd":null}}
```

Defaults come from `BUDGET_MAX_CALLS`, `BUDGET_MAX_TOKENS`, `BUDGET_MAX_OUTPUT_TOKENS`, `BUDGET_MAX_WALL_SECONDS`, and optional `BUDGET_MAX_COST_USD`. The ledger lives at `BUDGET_DB_PATH`. Values must be positive; a null cost cap disables monetary admission. Owners may configure limits before work starts and increase them later to resume an exhausted project. Usage never resets. Decreasing a started project's limits is rejected.

`BudgetContext(project_id)` binds jobs. `bind_budget_client(client)` captures that identity when constructing a client used by thread pools; `copy_budget_context(fn)` propagates context for workers that construct their own clients. Simulation children inherit only `MIROFISH_BUDGET_RUN_ID` and the absolute `MIROFISH_BUDGET_DB_PATH` via `budget_environment()`. Authenticated synchronous report chat/search, direct profile generation and agent interviews bind through `install_request_budget(app)` after the security hooks. A request-local observer turns swallowed budget errors into safe HTTP 429 responses with code `budget_exceeded`; status/read/cleanup routes remain available. Direct SDK smoke scripts without a budget identity remain standalone probes.

## What is enforced

SQLite `BEGIN IMMEDIATE` serializes admission across processes and threads. Each outbound request reserves one call, conservative input tokens, its entire requested output allowance and any configured cost estimate **before** network access. Concurrent calls cannot admit more than the ledger permits. A request that would exceed a cap marks the run `budget_exceeded` and sends no HTTP request. SDK automatic retries are disabled for budgeted calls; application retries must pass admission again.

Text input reservations use serialized UTF-8 JSON bytes, including tool schemas and message metadata, plus message framing allowance. This is deliberately conservative compared with a characters-divided-by-four estimate. The provider's tokenizer and hidden prompt framing remain outside the application's control; this is a hard admission cap on the reservation ledger, not a universal mathematical bound on every provider's internal tokenization. Reported input or output usage exceeding its reservation is recorded truthfully and closes the run with `provider_reservation_overrun`. Provider output caps must be honored by the provider. Budgeted streaming, multiple completions per request, non-text attachments, and extra-body overrides of reserved parameters are rejected.

Every budgeted call has a finite output cap (`max_tokens`, or `max_completion_tokens` for GPT-5). An omitted cap, including a JSON-repair retry, becomes 4096. A successful response with valid provider token usage replaces its reservation with actual usage. Failures, unknown/missing usage, cancellations, and interrupted processes retain the full token and cost reservation: they may have incurred a remote charge. Stale in-flight requests are never automatically refunded.

Wall seconds measure cumulative time with at least one model request in flight. Parallel calls share elapsed wall time; idle gaps between workflow stages do not consume it. `deadline_at` is null when no calls are in flight. Both synchronous and asynchronous SDK wrappers enforce the remaining absolute deadline and close the run on expiry. Synchronous calls use a daemon transport thread so a stalled transport cannot hold the job past its deadline. A provider can continue a request already sent after local timeout or cancellation, so the full reservation remains charged. A process dying with an in-flight reservation conservatively leaves its clock running until the budget expires. Increasing caps permits another attempt without erasing those charges.

The API exposes cumulative `calls`, `tokens`, `input_tokens`, `output_tokens`, active `wall_seconds`, nullable `estimated_cost_usd`, status/reason, limits, and the current deadline. Model prompts, responses, credentials and provider URLs are not stored in this ledger.

## Prices are explicit estimates

No provider price or Ollama Cloud subscription price is inferred. Configure `BUDGET_MODEL_PRICING_JSON` with exact model names and your applicable USD-per-million input/output rates, for example:

```json
{"your-exact-model":{"input_per_million":1.25,"output_per_million":5}}
```

These numbers are an **example configuration**, not quoted pricing. Rates are snapshotted when a project budget is created, so a child process cannot silently change accounting. If any admitted call lacks rates, total `estimated_cost_usd` is null. A monetary cap fails closed with `pricing_unavailable` before requesting an unpriced model. Call and token caps still operate when no monetary rates are configured. The ledger meters this application’s model-client requests. If you explicitly use Zep Cloud, its managed ingestion/service charges are separate and are not metered by these model limits. Estimates assume the configured input/output rates; subscription allowances, cached-token discounts, taxes and provider billing adjustments are not modeled, and this is not a billing guarantee.

## Verification

`backend/tests/test_budget_store.py`, `test_budget_model_calls.py`, and `test_budget_api.py`, and `test_budget_requests.py` exercise transactional process/thread admission, input/output/call/cost caps, failed/missing-usage reservations, deadline behavior, async cancellation, disabled SDK retries, client identity across threads, authenticated configuration, and limit increases. `test_camel_llm_provider.py` verifies both sync and async CAMEL requests against synthetic HTTP transports, including denial before a second HTTP request. These tests do not require a paid provider call.
