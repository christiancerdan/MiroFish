# Security remediation, 2026-10-02

Based on upstream commit `7657031ac01184afe2cb220f5ee3545573b5e843`.

| Demonstrated issue | Change | Evidence |
| --- | --- | --- |
| Unauthenticated project reads/deletion and cloud-backed work | Required owner key; signed HttpOnly sessions; bearer access for scripts; unconfigured API fails closed | Real Flask request tests |
| Wildcard CORS and browser write exposure | Explicit/same-origin policy, SameSite=Strict cookie, CSRF checks, login attempt limit | Foreign-origin, login/session, CSRF regressions |
| Script execution through generated Markdown | One Markdown renderer, raw HTML disabled, DOMPurify sanitization across report/interview/chat sinks | DOM tests with executable payloads |
| Arbitrary SQLite reads and path traversal | Strict storage IDs, platform allowlist, resolved path containment including symlinks, read-only SQLite | Temporary-file regression fixtures |
| Duplicate or lost background work | Durable SQLite claims, leases, idempotency and explicit interrupted recovery; lifecycle locks retained | Overlap, restart, stale-worker and subprocess regressions |
| Unbounded input coercion | Integer bounds for rounds and profile concurrency | Type/range regressions |
| Tracebacks and internal errors in responses | Central API error boundary, opaque stored failure messages, correlation IDs; request bodies no longer logged | Failure and malformed-input tests |
| Partial JSON storage writes | Temporary file + fsync + atomic replacement | Storage and IPC regressions |
| Development servers exposed by default deployment | Loopback host publication, one Waitress process, built same-origin frontend, non-root container | Startup tests; Compose validation |
| Vulnerable compatible dependencies | Refreshed npm and Python locks; CI tests/build/core audit; visible full dependency report | See dependency analysis |

The owner key is generated locally and never committed. Browser sessions last at most eight hours and are invalidated when the owner key changes. Logging out clears the browser cookie; this is not a server-side session revocation database. Use key rotation to invalidate a stolen session. This version does not provide separate user accounts or project-level access controls.

The ownership/reliability pass adds local SQLite graph memory, durable jobs, persistent project budgets, a separate modernized simulation environment, restricted worker startup, source snapshots and a forecast evaluation harness. The current public dependency inventories have zero known scan findings and zero skipped packages; the locally maintained OASIS source is tracked and tested separately. See [dependency details](dependency-security.md).

Residual limits: storage containment assumes an application-owned filesystem; project/simulation lifecycle coordination still requires one server process. Simulations share the application OS identity/container and do not have an outbound network allowlist. Calls already accepted by a provider may continue after local cancellation. Interrupted jobs require explicit retry because external effects cannot be made exactly-once. Logs and downloadable artifacts may contain source text or model output. Local graph storage keeps the graph on the server, while the configured model still receives prompts. Zep Cloud is optional. Keep this a private owner workspace; there are no separate user accounts or project-level authorization.

The initial hardening pass passed 362 backend/root tests, 40 frontend tests, and a live Waitress authentication/CSRF/origin smoke. The follow-up adds real SQLite restart/concurrency tests, full modern CAMEL/OASIS action and recommendation contracts, budget admission before network calls, source snapshot verification, historical cutoff evaluation, and frontend recovery/evidence tests. Current validation results are recorded in the pull request and the [runtime](deployment.md), [dependency](dependency-security.md), and [provider](ollama-cloud.md) documentation.

Provider checks use synthetic prompts and do not establish forecasting accuracy. Direct hosted Ollama credentials and the complete Zep-backed pipeline remain unverified. Citation IDs and hashes establish reference integrity, not whether generated claims follow from the sources.
