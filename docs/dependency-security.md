# Dependency security and simulation runtime

Reviewed 2026-10-02. The locked API environment and the public packages in the
modernized simulation environment pass the current vulnerability scan. This is
an inventory result, not a guarantee that every dependency or application path
is free of vulnerabilities. The locally maintained OASIS source is reviewed and
tested separately because it is not a PyPI release.

## Separate installations

The default backend dependencies contain the API, file parser, OpenAI-compatible
client, and Zep SDK. CAMEL, OASIS, model frameworks, and their tool integrations
are in the optional `simulation` extra. Install them in separate environments:

```sh
cd backend
uv sync --locked --no-dev
UV_PROJECT_ENVIRONMENT=.venv-simulation uv sync --locked --extra simulation --no-dev
```

The simulation environment includes the application dependencies as well as the
simulation extra. It can import the same backend modules; the API environment
cannot import CAMEL/OASIS. The worker launcher chooses the simulation Python
explicitly. See [deployment.md](deployment.md) for process bounds, credentials,
and container setup.

`requirements.txt` describes the API-only pip installation.
`requirements-simulation.txt` adds the local OASIS package and simulation
requirements; run pip from `backend/` when using that file. These are compatibility
manifests, not transitive locks. `uv sync --locked` is the reviewed reproducible
installation, including Linux's CPU-only Torch source selection. A plain pip
installation can select different transitives and the default CUDA Torch build.

## Why there is a maintained OASIS package

The latest published [OASIS release, 0.2.5](https://pypi.org/pypi/camel-oasis/0.2.5/json),
pins Pillow 10.3.0, Sentence Transformers 3.0.0, Unstructured 0.13.7 and pytest
8.2.0 as mandatory runtime dependencies. Those constraints prevented resolving
several reported vulnerabilities. The
[upstream main manifest](https://github.com/camel-ai/oasis/blob/main/pyproject.toml)
has moved CAMEL to 0.2.90 but retains the older dependency pins. CAMEL 0.2.90
supports current Pillow versions, unlike 0.2.78's `<11` constraint.

`backend/vendor/camel-oasis` is a small maintained fork of the official 0.2.5
wheel, published locally as **0.2.5+mirofish.1**. It retains the OASIS social-agent,
tool-action, SQLite, graph, and recommendation implementation. Its changes are:

- A runtime manifest using CAMEL 0.2.90 and patched model libraries, with actual
  OASIS imports declared explicitly. Cairo remains available for graph plotting.
- Removal of unused Unstructured, pytest, pre-commit, Slack, OAuth and OpenAPI
  tools from OASIS runtime requirements. Tests have their own current dev
  dependencies. NLTK disappears with the unused Unstructured dependency.
- Explicitly safe loading of the two existing recommendation models: fixed
  upstream revisions, `trust_remote_code=False`, and safetensors weights.
- A tested Python 3.11/3.12 support range instead of upstream's `<3.12` metadata.

This is ordinary package resolution against a reviewed local manifest. There
are no `--no-deps` installs or dependency-metadata overrides. The package's
`UPSTREAM.json` records the official wheel URL, SHA-256 and original module
hashes; `LICENSE`, upstream copyright headers and `PATCHES.md` retain provenance
and enumerate changes. Only `oasis/social_platform/recsys.py` differs from the
upstream Python source. Review any future source changes against those hashes.

## Reviewed versions

| Package | Previous maintained lock | Current simulation lock |
| --- | --- | --- |
| CAMEL AI | 0.2.78 | 0.2.90 |
| OASIS | 0.2.5 | 0.2.5+mirofish.1, local maintained source |
| Pillow | 10.3.0 | 12.3.0 |
| Sentence Transformers | 3.0.0 | 6.1.0 |
| Transformers | 4.57.6 | 5.18.0 |
| Torch | 2.14.1 | 2.14.1; official 2.14.1+cpu on Linux |
| Pydantic | 2.12.5 | 2.12.0, required by CAMEL 0.2.90's declared upper bound |
| Pygments | 2.19.2 | 2.21.0 |
| pytest | 8.2.0, required by OASIS at runtime | 9.1.1, development/tests only |
| Unstructured / NLTK | 0.13.7 / 3.10.3 | Removed from the runtime dependency graph |

OpenAI **1.109.1**, MCP **1.30.0**, and Zep Cloud **3.25.0** retain their tested
contracts. MCP remains explicitly `<2` because CAMEL imports `FastMCP` from
`mcp.server`; resolving MCP 2 breaks that path. Zep 3.25.0 is deliberate because
its graph API contracts have dedicated tests. The earlier framework fixes remain:
Flask **3.1.3**, Werkzeug **3.1.9**, python-dotenv **1.2.4**, and Waitress **3.0.2**.

Linux Torch comes only from the explicitly scoped
[official CPU wheel index](https://download.pytorch.org/whl/cpu/torch/), with
wheel hashes in `uv.lock`. Other packages stay on PyPI. This avoids unnecessary
CUDA packages in the default worker image and supports Linux x86_64/aarch64 on
Python 3.11/3.12. GPU workers require a separately reviewed dependency source
and resource configuration.

## Recommendation model boundary

Twitter's default OASIS recommendation path loads the TwHIN model lazily when
recommendations process posts. An empty initial round may return before this
happens. The alternate Twitter path uses Sentence Transformers; Reddit/random
selection does not initialize those embedding checkpoints.

The maintained loaders permit only the existing named models and use these
fixed revisions:

| Model | Revision |
| --- | --- |
| `Twitter/twhin-bert-base` | `82ac392ce81f94560c391311ee2ddd024c5ac1fc` |
| `sentence-transformers/paraphrase-MiniLM-L6-v2` | `c9a2bfebc254878aee8c3aca9e6844d5bbb102d1` |

Public repository metadata confirmed both snapshots contain `model.safetensors`.
The loaders require safetensors and disable remote Python execution. They reject
arbitrary model names/paths. Model weights still enter a worker process and its
cache remains application-owned; these controls do not replace worker resource
limits or justify loading unknown artifacts. Actual production model weights
were not downloaded as part of the offline regression tests.

## Audit results and coverage

The original audit had **274 raw findings / 162 distinct package-advisory pairs
across 28 packages**. The first compatible refresh left **50 raw / 29 distinct
matches across six packages**. Modernization removes those six constraints.

With `pip-audit 2.10.1`, the final local Python 3.12 audit records:

| Inventory | Public packages examined | Findings | Skipped packages |
| --- | ---: | ---: | ---: |
| API runtime | 32 | 0 | 0 |
| Simulation runtime, macOS markers | 94 | 0 | 0 |
| Simulation runtime, Linux 3.12 markers | 94 | 0 | 0 |

The Linux inventory is selected from the lock's environment markers; actual
Linux execution is covered separately by CI/container validation. Counts can
vary by platform and the advisory database can change without a lockfile change.
The local OASIS fork is explicitly omitted from the PyPI lookup, while all its
resolved public transitive dependencies remain included. Source provenance and
real runtime tests cover that local package; omitting it is not an advisory
suppression for a public package.

PyPI's advisory endpoint does not recognize the official Torch `+cpu` build
suffix and otherwise returns a skipped-package result. CI verifies the lock
points to the official CPU index, queries the corresponding upstream version
(e.g. `2.14.1`) for advisories, retains the original inventory as an artifact,
and fails if the scanner skips any public package. No advisory IDs are ignored.

## Validation and CI

The current full Python 3.12 simulation environment passed **476 backend tests**
at the dependency validation checkpoint. Tests include real CAMEL/OASIS imports,
the three simulation entry points, and actual tool execution: a mocked provider
response calls OASIS `create_post`, which must create the correct SQLite post
and action trace. This catches silent OASIS failures that a completed round alone
would miss.

Recommendation tests create tiny local safetensors BERT and Sentence Transformer
checkpoints. They exercise real tokenization, Torch forward passes, vector
output, and both OASIS recommendation calculations with the upgraded libraries.
They verify the fixed revision and safe-loader arguments; they do not stand in
for assessing the quality or memory use of the full production models.

`.github/workflows/ci.yml` now gates pull requests on:

- API tests and root regressions on Python 3.11/3.12 with simulation packages
  absent from the API environment.
- The complete backend suite with a separately installed simulation extra on
  both Python versions, including real agent/action/recommendation contracts
  and integration tests. Provider transport is mocked and model checkpoints
  are generated locally.
- Blocking API and full public simulation dependency scans, including a failure
  for any skipped audit package. Raw inventories and audit JSON are uploaded.
- Frontend security tests, production build, and root/frontend npm high-severity
  vulnerability checks on Node 22.

For a local API inventory scan from the repository root:

```sh
uv export --directory backend --locked --no-dev --no-emit-project --no-hashes \
  --output-file /tmp/mirofish-api-requirements.txt
uvx pip-audit==2.10.1 --disable-pip --no-deps \
  -r /tmp/mirofish-api-requirements.txt --format json \
  --output /tmp/mirofish-api-audit.json
```

For the simulation inventory, add `--extra simulation --no-emit-package
camel-oasis` to `uv export`, then apply the CPU-version normalization shown in
the workflow before scanning on Linux. Inspect `skip_reason` in every JSON
result as well as the exit code. `--no-deps` is used only for an already complete,
locked scanner input; it must not be used to claim a full audit of a direct-only
requirements manifest.
