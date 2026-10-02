# Security remediation, 2026-10-02

Based on upstream commit `7657031ac01184afe2cb220f5ee3545573b5e843`.

| Demonstrated issue | Change | Evidence |
| --- | --- | --- |
| Unauthenticated project reads/deletion and cloud-backed work | Required owner key; signed HttpOnly sessions; bearer access for scripts; unconfigured API fails closed | Real Flask request tests |
| Wildcard CORS and browser write exposure | Explicit/same-origin policy, SameSite=Strict cookie, CSRF checks, login attempt limit | Foreign-origin, login/session, CSRF regressions |
| Script execution through generated Markdown | One Markdown renderer, raw HTML disabled, DOMPurify sanitization across report/interview/chat sinks | DOM tests with executable payloads |
| Arbitrary SQLite reads and path traversal | Strict storage IDs, platform allowlist, resolved path containment including symlinks, read-only SQLite | Temporary-file regression fixtures |
| Duplicate preparation workers | Atomic in-process claims released on completion/error/start failure | Overlapping-request regressions |
| Unbounded input coercion | Integer bounds for rounds and profile concurrency | Type/range regressions |
| Tracebacks and internal errors in responses | Central API error boundary, opaque stored failure messages, correlation IDs; request bodies no longer logged | Failure and malformed-input tests |
| Partial JSON storage writes | Temporary file + fsync + atomic replacement | Storage and IPC regressions |
| Development servers exposed by default deployment | Loopback host publication, one Waitress process, built same-origin frontend, non-root container | Startup tests; Compose validation |
| Vulnerable compatible dependencies | Refreshed npm and Python locks; CI tests/build/core audit; visible full dependency report | See dependency analysis |

The owner key is generated locally and never committed. Browser sessions last at most eight hours and are invalidated when the owner key changes. Logging out clears the browser cookie; this is not a server-side session revocation database. Use key rotation to invalidate a stolen session. This version does not provide separate user accounts or project-level access controls.

Residual limits: six simulation packages still have advisory matches; storage containment assumes an application-owned filesystem; job coordination is process-local; background work is not restart-safe. Uploaded material still goes to the configured model provider and Zep Cloud. Logs and downloadable simulation artifacts are private owner data and may include document content or model output. Keep the service private while the [next security improvements](ROADMAP.md) are completed.

The final lightweight backend/root run passed 362 tests (one CAMEL module skipped), and the frontend passed 40 tests plus its production build. A separate full CAMEL/OASIS test environment passed 52 provider-focused tests. A live Waitress HTTP smoke check passed frontend serving, anonymous denial, cookie login, CSRF, identifier validation, foreign-origin denial, and logout. The signed-in local Ollama Cloud route passed text, JSON, function tools, asynchronous CAMEL tools, and one real OASIS Reddit post with a matching action trace.

The full Docker image and complete Zep-backed document-to-report pipeline require separate validation. The live provider environment was not an exact frozen-lock installation; see [provider verification boundaries](ollama-cloud.md#verification-boundaries). Provider checks use synthetic prompts only and do not establish forecasting accuracy.
