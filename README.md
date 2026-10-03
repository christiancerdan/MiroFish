# MiroFish — maintained fork

[christiancerdan/MiroFish](https://github.com/christiancerdan/MiroFish) is a maintained fork of [666ghj/MiroFish](https://github.com/666ghj/MiroFish), focused on a private, single-owner simulation workspace, safer defaults, and Ollama integration.

Upload source documents, build a knowledge graph, generate agent profiles, run simulated social interactions, and explore reports or interview the agents. These are model-generated scenarios; their accuracy as forecasts has not been established.

The original project remains credited in [README-UPSTREAM.md](./README-UPSTREAM.md). [中文说明](./README-ZH.md) preserves the upstream Chinese documentation with a maintenance notice. The [AGPL-3.0 license](./LICENSE) is unchanged.

## Local setup

Requirements: **Node.js 22.12+**, **Python 3.11 or 3.12**, and **uv**. The API and simulation runtimes install into separate environments from `uv.lock`; npm installs from the committed lockfiles.

```sh
git clone https://github.com/christiancerdan/MiroFish.git
cd MiroFish
npm run setup:config
```

This creates a Git-ignored `.env` with a random `MIROFISH_ACCESS_KEY` and restrictive file permissions. It preserves any existing `.env`. Review the model settings locally and keep workspace and provider keys private. **No Zep account is required:** `GRAPH_BACKEND=local` stores graph memory in SQLite on your server. Extraction still sends source text to your configured model; Ollama Cloud therefore receives prompts. Existing Zep projects need explicit `GRAPH_BACKEND=zep` and their original key. Switching backends does not migrate existing data. See [local memory](./docs/local-memory.md).

The example configuration uses **`gpt-oss:20b-cloud` through a signed-in local Ollama daemon** at `http://127.0.0.1:11434/v1`. Sign in with `ollama signin` and confirm the alias is available in `ollama list`. Direct Ollama Cloud authentication and other OpenAI-compatible endpoints are also supported; see [Ollama configuration and capability checks](./docs/ollama-cloud.md).

```sh
npm run setup:all
npm run build
npm start
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001) and sign in using the `MIROFISH_ACCESS_KEY` in `.env`. The production start command uses one Waitress process to serve the built frontend and API from the same origin. Rebuild after frontend changes.

For development, `npm run dev` starts the backend and Vite; open [http://127.0.0.1:3000](http://127.0.0.1:3000).

## Docker

Configure `.env` first, then:

```sh
docker compose up --build -d
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). Compose publishes only a loopback port and stores uploads and logs in named volumes. On Docker Desktop, change the local Ollama URL in `.env` to `http://host.docker.internal:11434/v1`: `127.0.0.1` inside the container refers to the container itself.

See [deployment instructions](./docs/deployment.md) for persistent data, migration from upstream bind mounts, HTTPS configuration, and the single-process limit. Job claims are transactional, but project and simulation lifecycle coordination still requires one server process.

## Workspace controls

The **Workspace** panel shows memory/model location, project usage limits, and background jobs. Queued work resumes at startup; interrupted work requires an explicit acknowledged retry. Model call, token, output, and active-time budgets persist across stages and retries. Monetary estimates require your configured model prices; unknown subscription costs stay unknown.

Reports include saved source evidence, citation checks, run metadata, and explicit uncertainty. Citation checks establish reference integrity, not whether a claim is true. The [forecast evaluation CLI](./docs/forecast-evaluation.md) supports dated holdouts, proper scores, baselines, and repeated-run variability; the bundled example is synthetic.

The [audited 80-trial historical comparison](./docs/benchmark-holdout-2026-10-03.md) found no established predictive advantage over a single-model baseline. The [prospective customer-study kit](./docs/prospective-customer-study.md) prepares text A/B studies, seals manually imported forecasts before launch, and scores operator-attested aggregate responses afterward. It does not launch experiments or contact customers.

## Verification and maintenance

Text, JSON, function tools, asynchronous CAMEL tool calls, and a real OASIS Reddit action with a saved post and matching SQLite trace were verified with the configured local Ollama cloud alias on **2026-10-02 UTC**, including the modernized simulation runtime. A complete synthetic local-memory workflow also passed: ontology and graph extraction, profiles, four model-driven Reddit actions, drained memory updates, and a three-section report with five verified source references. Fresh-process persistence checks passed. The fixture explicitly fixes a two-agent activity schedule; see [the reproducible check](./docs/live-workflow.md). Direct hosted Ollama authentication and the complete Zep-backed workflow have not been live-verified. Run the capability checks for your own account and model before starting simulations; hosted model usage may incur charges.

- [Ollama Cloud and local daemon configuration](./docs/ollama-cloud.md)
- [Private deployment and access-key setup](./docs/deployment.md)
- [Local graph memory and Zep migration boundaries](./docs/local-memory.md)
- [Durable jobs and explicit recovery](./docs/durable-jobs.md)
- [Project usage budgets](./docs/run-budgets.md)
- [Evidence and forecast evaluation](./docs/forecast-evaluation.md)
- [Deterministic citations and live reliability check](./docs/deterministic-citations-2026-10-03.md)
- [Prospective customer-study preparation](./docs/prospective-customer-study.md)
- [Synthetic live workflow check](./docs/live-workflow.md)
- [Security changes and verification boundaries](./docs/security-remediation.md)
- [Dependency audit results and maintained OASIS source](./docs/dependency-security.md)
- [Maintenance roadmap](./docs/ROADMAP.md)
