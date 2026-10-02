# MiroFish — maintained fork

[christiancerdan/MiroFish](https://github.com/christiancerdan/MiroFish) is a maintained fork of [666ghj/MiroFish](https://github.com/666ghj/MiroFish), focused on a private, single-owner simulation workspace, safer defaults, and Ollama integration.

Upload source documents, build a knowledge graph, generate agent profiles, run simulated social interactions, and explore reports or interview the agents. These are model-generated scenarios; their accuracy as forecasts has not been established.

The original project remains credited in [README-UPSTREAM.md](./README-UPSTREAM.md). [中文说明](./README-ZH.md) preserves the upstream Chinese documentation with a maintenance notice. The [AGPL-3.0 license](./LICENSE) is unchanged.

## Local setup

Requirements: **Node.js 22.12+**, **Python 3.11**, and **uv**. The backend installs from `uv.lock`; npm installs from the committed lockfiles.

```sh
git clone https://github.com/christiancerdan/MiroFish.git
cd MiroFish
npm run setup:config
```

This creates a Git-ignored `.env` with a random `MIROFISH_ACCESS_KEY` and restrictive file permissions. It preserves any existing `.env`. Open the file locally, fill in **`ZEP_API_KEY`**, and review the model settings. Zep Cloud is required for document ingestion and graph workflows; documents are sent to that service. Keep workspace and provider keys private.

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

See [deployment instructions](./docs/deployment.md) for persistent data, migration from upstream bind mounts, HTTPS configuration, and the single-process limit. Existing simulation and task state does not support multiple backend workers or replicas.

## Verification and maintenance

Text, JSON, function tools, asynchronous CAMEL tool calls, and a real OASIS Reddit action with a saved post and matching SQLite trace were verified with the configured local Ollama cloud alias on **2026-10-02 UTC**. Direct hosted Ollama authentication and the complete document → Zep → simulation → report workflow have not been live-verified. Run the capability checks for your own account and model before starting simulations; hosted model usage may incur charges.

- [Ollama Cloud and local daemon configuration](./docs/ollama-cloud.md)
- [Private deployment and access-key setup](./docs/deployment.md)
- [Security changes and verification boundaries](./docs/security-remediation.md)
- [Dependency audit results and remaining constraints](./docs/dependency-security.md)
- [Maintenance roadmap](./docs/ROADMAP.md)
