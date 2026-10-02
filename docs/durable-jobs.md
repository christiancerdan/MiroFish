# Durable background jobs

Graph builds, simulation preparation, and report generation persist their handler,
parameters, project identity, locale, progress, and result in `JOBS_DB_PATH`
(default: `backend/uploads/jobs.sqlite3`). Keep this database on the same persistent
volume as project, simulation, report, and local memory data.

The normal `run.py` and `serve.py` entrypoints start a bounded dispatcher. Library
users can explicitly call `start_job_dispatcher(app)` from
`app.services.job_dispatcher`; `JOBS_AUTOSTART=true` enables the app factory hook.
Tests suppress automatic dispatch. `JOB_WORKERS` defaults to 2 per server process
and `JOB_LEASE_SECONDS` defaults to 60. Run one server process for the existing
project/simulation lifecycle locks; SQLite job claims themselves are atomic across
multiple connections/processes sharing the same database.

Each worker claims a single pending job in a transaction and renews its lease.
Short artifact writes verify the current owner, cancellation flag, and lease
inside a SQLite writer transaction, so lease recovery or an explicit retry cannot
grant a replacement worker ownership during publication. This protects against
stale workers overwriting replacement output; it does not make filesystem writes
and SQLite an atomic commit or hold a transaction across external calls. Existing
atomic file replacement remains necessary. Worker pool callbacks carry execution
identity and check it before starting queued work.
After restarting the server:

- Pending jobs are dispatched automatically using their saved named handler.
- A running job whose lease expires becomes `interrupted`, with
  `error_code: "worker_interrupted"` and an explanation of the recovery action.
- Completed, failed, cancelled, and budget-exceeded jobs retain their results and
  are not automatically replayed.

A crash can occur after a provider accepts a request and before its response is
saved. These jobs do **not** claim exactly-once external effects. Inspect saved
project/batch/report state before an explicit retry. A graph retry reuses a
persisted processing/succeeded Zep batch; an incomplete or ambiguous Zep draft
requires a deliberate forced rebuild. Local graph retries may replace incomplete
local graph state. Preparation/report retries may repeat already paid calls.

`Idempotency-Key` is supported by all three submission routes. Repeating a key
with the same parameters returns the original task, including after completion.
Reusing it for different parameters is rejected. Concurrent requests for the same
operation share an active job; every supplied matching key is persisted, including
keys first seen through deduplication. Ordinary task cleanup retains keyed jobs.

## Inspect and recover

Existing authentication requirements apply to these endpoints:

- `GET /api/graph/tasks?project_id=...` lists project jobs. `task_type` is an
  optional additional filter. Responses contain `data` (a task array) and `count`.
- `GET /api/graph/task/<task_id>` returns one task in `data`.
- `POST /api/graph/task/<task_id>/retry` requires a fresh `Idempotency-Key` for the
  retry intent and JSON `{"acknowledge_effects": true}`. The response is HTTP 202
  with the queued task. Repeating the same retry key never queues it twice.
  Failed, interrupted, and budget-exceeded jobs are eligible; an active newer
  job for the same operation prevents a conflicting retry.
- `POST /api/graph/task/<task_id>/cancel` cancels a pending or interrupted job.
  For running jobs, it sets `cancel_requested`; handlers stop at the next progress
  checkpoint. Calls already in flight may finish, and completed external effects
  remain. This is cooperative cancellation, not a rollback.

Task fields include `status`, `progress`, `message`, `error`, `error_code`,
`attempts`, `retryable`, `cancel_requested`, and project/simulation/report IDs in
`metadata`. Internal saved input parameters are not returned in task status APIs.
A budget limit stops work with `status: "budget_exceeded"`; increasing the budget
alone does not silently restart the job. Use the explicit retry endpoint after
reviewing its output and updated limits.

Shutting down stops new claims. Workers still executing at process termination
retain their lease until it expires and then become interrupted. The queue and
terminal results remain in SQLite. Do not delete this database to clear a failed
job: that also deletes the deduplication and idempotency history.

## Verification

`backend/tests/test_durable_jobs.py` covers real SQLite persistence, concurrent
claims, lease fencing, cancellation, retry idempotency, graph reconstruction,
forced rebuilds, and refusal to automatically replay an ambiguous external call.
`backend/tests/test_durable_api_handlers.py` submits preparation/report jobs via
the API, launches a fresh Python interpreter, reconstructs the named handlers,
and verifies a second restart performs no further provider work. Network sockets
are forbidden in those restart tests.
