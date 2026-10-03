# Synthetic workflow verification

`backend/scripts/check_local_workflow.py` exercises the authenticated application workflow with real model calls and local SQLite memory. It is an opt-in integration check, not an accuracy benchmark. The fixture describes two fictional Reddit users deciding when to organize a neighborhood cleanup.

After `npm run setup:all`, configure a signed-in local Ollama daemon endpoint and model in `.env`, then run from the repository root:

```sh
backend/.venv-simulation/bin/python backend/scripts/check_local_workflow.py \
  --evidence-path /tmp/mirofish-workflow-proof.json
```

On Windows, use `backend/.venv-simulation/Scripts/python.exe`. This check currently requires a loopback model endpoint; a `-cloud` alias still sends prompts to the hosted model through Ollama. It preserves the workspace `.env`, creates its own owner key, forces local graph storage with an empty Zep key, and places all project data in a separate temporary directory.

The check uses at most 60 model calls and a 1200-second active-model budget by default, with finite token/output limits. Provider calls can consume your account allowance. It uploads the synthetic text, generates an ontology, builds the local graph, prepares profiles/configuration, runs one Reddit round, drains graph memory, and generates an evidence-linked report. It checks stored graph entities/relationships, successful model-driven actions distinct from seed posts, source snapshots, citation integrity, run metadata, uncertainty labels and cumulative project usage.

To make a two-agent, one-round fixture meaningful, the check records explicit schedule overrides after preparation: both agents are active in the first hour, activity multipliers are one, and seed posts are assigned across the two participants. It does not claim the unmodified generated schedule will always produce an active first round. Model-selected actions and report text remain nondeterministic, and the check fails if no successful non-idle model action occurs or citations remain invalid.

Proof JSON records stage status, usage, action types, artifact hashes, runtime location and source revision/diff hashes. Unique attempt logs and temporary data remain available for inspection. Secrets are not included in proof JSON. Reports verify the existence and content hashes of referenced evidence; they do not establish that every generated claim follows from that evidence or that a forecast is accurate.

A failed attempt that completed preparation can resume from its saved proof:

```sh
backend/.venv-simulation/bin/python backend/scripts/check_local_workflow.py \
  --resume-proof /tmp/mirofish-workflow-proof.json \
  --evidence-path /tmp/mirofish-workflow-resumed.json
```

Resuming reopens persisted state in a new process and reruns the simulation/report stages with the same cumulative project budget and a fresh execution graph. It avoids repeating ontology, graph and profile generation. Legacy fixtures whose source graph already contains simulation observations require a fresh fixture rather than resume. This is an explicit retry with possible repeated provider effects; it is not continuation of a crashed simulation process or an exactly-once guarantee. Do not use this fixture script to resume a real user project.

Add `--report-only` with `--resume-proof` to reuse a completed simulation and regenerate only the report. This mode requires previously verified model-driven actions and an independent execution binding, retains the same project budget, and checks that the resulting outline has 2–5 nonempty unique section titles. It does not rerun ingestion, preparation, or simulation.

## Recorded result

The 2026-10-02 UTC run passed through `gpt-oss:20b-cloud` on a signed-in local daemon: four successful model-selected Reddit actions plus two seeds, two new simulation-memory episodes, and a three-section report citing five saved sources. A separate read-only process verified persisted jobs, budget usage, source text and report hashes. The project ledger retained prior failed attempts and the report-only retry: 30 calls and 84,817 tokens total, with unknown monetary cost. This is a controlled synthetic integration result, not an accuracy measurement.
