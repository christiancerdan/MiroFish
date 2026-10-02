# Ollama Cloud

MiroFish uses the OpenAI-compatible Chat Completions API for text, JSON profile/configuration generation, reports, and OASIS tool actions. A successful text response alone does not establish simulation compatibility: the selected model must also produce JSON and function tool calls.

## Connect directly to Ollama Cloud

Set these values in the project-root `.env`:

```dotenv
LLM_PROVIDER=ollama_cloud
LLM_API_KEY=
OLLAMA_API_KEY=your_ollama_api_key_here
LLM_BASE_URL=https://ollama.com/v1
LLM_MODEL_NAME=
LLM_TOKEN_LIMIT=32768
```

Replace the API-key placeholder with a key created in your Ollama account. `LLM_API_KEY` is also accepted and takes precedence over `OLLAMA_API_KEY`. The key is sent as a Bearer authorization header. Keep credentials out of Git and shell history.

Select a model from the direct API instead of assuming that a local `-cloud` model alias is accepted remotely. From the repository root, after installing backend dependencies:

```sh
cd backend
uv run python scripts/check_llm_provider.py --list-models
```

Copy the desired ID into `LLM_MODEL_NAME`, then test the selected model:

```sh
uv run --extra simulation python scripts/check_llm_provider.py
```

The command sends four small synthetic requests: text, JSON mode, a function tool call, and a function tool call through the same asynchronous CAMEL backend OASIS uses. It does not send project uploads or run any returned tool. It reports success only when all checks pass. Provider failures report the error class and HTTP status without printing credentials or response bodies. `--skip-camel` checks only the application SDK and cannot establish simulation compatibility.

To verify that OASIS actually executes and saves a model-selected action:

```sh
uv run --extra simulation python scripts/check_oasis_provider.py
```

This creates one synthetic Reddit agent, runs one step with only `CREATE_POST` available, and checks the exact saved post and matching action trace in SQLite. Its temporary database and logs are deleted afterward. It does not create a public social-media post or require Zep. The check fails if OASIS silently swallows an agent error. Use `--timeout 120` to bound the check and `--boost` to check the optional second provider.

For direct cloud mode the base URL must be `https://ollama.com/v1`. The native Ollama endpoint `https://ollama.com/api` uses a different API and must not be entered here. Explicit cloud mode rejects other endpoints to prevent sending an Ollama key to a leftover provider URL.

## Use a signed-in local Ollama daemon for cloud inference

A local daemon can route its cloud model aliases to Ollama Cloud using its existing sign-in. This is a different connection mode:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_API_KEY=ollama
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL_NAME=gpt-oss:20b-cloud
LLM_TOKEN_LIMIT=32768
```

Here `ollama` is the local OpenAI-client placeholder key, not a direct-cloud credential. Use `ollama signin`, inspect the models available through your daemon, and run the same probe. The example model alias must be available in your account/daemon. Model availability and capabilities can change; the probe verifies your selected model at the time it runs.

## Optional second provider

Twitter and Reddit normally share the primary configuration. Parallel Reddit can use a second endpoint only when all three fields are valid:

```dotenv
LLM_BOOST_PROVIDER=openai_compatible
LLM_BOOST_API_KEY=
LLM_BOOST_BASE_URL=
LLM_BOOST_MODEL_NAME=
```

Leave all three empty to disable it. Unmodified `your_*_here` placeholders from older example files are also treated as disabled. A partially filled real configuration raises a configuration error. Set `LLM_BOOST_PROVIDER=ollama_cloud` when the second endpoint is direct Ollama Cloud. If omitted, the boost provider inherits `LLM_PROVIDER`. Test it with `check_llm_provider.py --boost`. The two backends receive their own credentials and URLs; they never overwrite global OpenAI environment variables.

`LLM_TOKEN_LIMIT` is CAMEL's input-memory budget, not the server context-window size. `LLM_BOOST_TOKEN_LIMIT` can override it for the second model. Simulations cap output at 4096 tokens separately. Choose an input budget below the model's actual context capacity, leaving room for output and tool schemas. CAMEL's token counting is approximate for non-OpenAI models. Local server context settings are configured in Ollama, not by the OpenAI `max_tokens` parameter.

## Verification boundaries

Live verification on 2026-10-02 UTC passed text, JSON, function tools, and asynchronous CAMEL tools using `gpt-oss:20b-cloud` through the signed-in local daemon at `http://127.0.0.1:11434/v1`. Direct `https://ollama.com/v1` authentication has not been live-tested because no direct-cloud API key was available. These are distinct routes. The complete upload → Zep graph → simulation → report workflow was not live-tested because no Zep Cloud API key was available.

The real OASIS probe also passed on that route: one Reddit agent, one step, one exact-content post, and one matching `create_post` SQLite trace. The probe used no HTTP mocks. The database and library logs were removed afterward. This verifies a persisted social action; it does not verify Twitter recommendations or a complete prediction workflow.

The initial hardening pass used CAMEL 0.2.78, OASIS 0.2.5, OpenAI SDK 1.109.1, HTTPX 0.28.1, MCP 1.30.0, Torch 2.14.1, Transformers 4.57.6, and Sentence Transformers 3.0.0, matching the refreshed lock for those packages. It was not a full frozen-lock installation: NumPy was 2.4.6 versus locked 2.3.5, Pydantic 2.13.5 versus 2.12.5, and Flask-CORS 6.0.5 versus 6.0.2.

The modernized locked environment subsequently passed the real OASIS persisted-action probe with CAMEL 0.2.90 and the maintained OASIS package, including restricted worker startup. The API and simulation installations are now separate; see [dependency details](dependency-security.md). Use `.venv-simulation/bin/python` for capability checks after `npm run setup:all`, or `uv run --extra simulation` to select the extra explicitly.

Automated tests use the real OpenAI SDK and pinned CAMEL backend with synthetic HTTP responses to verify authentication, arbitrary model IDs, JSON requests, synchronous/asynchronous tools, provider isolation, and error redaction. They do not establish live account access, billing status, model availability, or prediction quality. The probe establishes those selected API capabilities for one model/endpoint; a full simulation additionally depends on the selected graph backend, generated profiles, and OASIS. OASIS can swallow agent failures, so inspect persisted actions when validating a complete run.

Official references: [API introduction](https://docs.ollama.com/api/introduction), [authentication](https://docs.ollama.com/api/authentication), [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility).
