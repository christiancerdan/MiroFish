# Dependency security and validation

Reviewed 2026-10-02. The backend web/API runtime subset passes the current
`pip-audit` scan. The complete simulation dependency tree still has unresolved
advisories. A successful test run or green CI does not mean the full simulation
installation has no known vulnerable packages.

## Installation contract

Use `cd backend && uv sync --locked` for the reviewed resolution. Both
`pyproject.toml` and `requirements.txt` retain CAMEL AI **0.2.78**, OASIS
**0.2.5**, and Zep Cloud **3.25.0**. Zep's version is deliberate because the
application and mocked contract tests use that SDK's graph APIs. The OpenAI SDK
remains **1.109.1**, compatible with CAMEL's `<2` requirement.

MCP is explicitly constrained to `>=1.30.0,<2` in both manifests. CAMEL 0.2.78
imports `FastMCP` from `mcp.server`; MCP 2 removes that import path. Resolving an
unbounded current MCP version breaks the OASIS import. This is a compatibility
constraint, not an advisory suppression.

`requirements.txt` is a compatibility manifest, not the transitive lock. An
unconstrained pip installation can select versions different from those tested.
Do not override OASIS's exact dependencies merely to obtain a clean scanner
result: changing those contracts needs simulation integration validation.

Primary metadata:
[CAMEL AI 0.2.78](https://pypi.org/pypi/camel-ai/0.2.78/json),
[OASIS 0.2.5](https://pypi.org/pypi/camel-oasis/0.2.5/json), and
[Zep Cloud 3.25.0](https://pypi.org/pypi/zep-cloud/3.25.0/json).

## Changes in this refresh

Flask moved from **3.1.2 to 3.1.3**, Werkzeug from **3.1.4 to 3.1.9**, and
python-dotenv from **1.2.1 to 1.2.4**. Their manifest minimums are now 3.1.3,
3.1.6, and 1.2.2 respectively. Waitress **3.0.2** was added for the production
WSGI entry point. Flask's release is published on
[PyPI](https://pypi.org/project/Flask/3.1.3/).

The lock also refreshes the following packages within their parents' declared
requirements:

| Package | Previous | Reviewed lock |
| --- | --- | --- |
| anyio | 4.12.0 | 4.14.2 |
| click | 8.3.1 | 8.5.0 |
| cryptography | 46.0.3 | 50.0.2 |
| filelock | 3.20.1 | 3.32.7 |
| idna | 3.11 | 3.20 |
| lxml | 6.0.2 | 6.1.3 |
| marshmallow | 3.26.1 | 3.26.2 |
| mcp | 1.24.0 | 1.30.0 |
| nltk | 3.10.0 | 3.10.3 |
| oauthlib | 3.3.1 | 4.0.0 |
| pydantic-settings | 2.12.0 | 2.15.0 |
| pyjwt | 2.10.1 | 2.15.1 |
| pypdf | 6.4.2 | 6.19.0 |
| python-multipart | 0.0.21 | 0.0.32 |
| requests | 2.32.5 | 2.34.2 |
| setuptools | 80.9.0 | 83.0.0 |
| soupsieve | 2.8 | 2.10 |
| starlette | 0.50.0 | 1.7.0 |
| torch | 2.9.1 | 2.14.1 |
| transformers | 4.57.3 | 4.57.6 |
| urllib3 | 2.6.2 | 2.8.0 |
| virtualenv | 20.35.4 | 21.14.3 |

Torch's platform-specific CUDA dependencies also change with its version.
The resolver accepts the complete graph; that alone does not validate GPU
execution or an end-to-end OASIS simulation. Local OAuth 1 signing and OAuth 2
authorization URL generation passed with requests-oauthlib 2.0.0/OAuthlib 4.0.0.
Pre-commit 3.7.1 starts and virtualenv 21.14.3 creates a local environment.

## Audit inventory and remaining constraints

The original audit recorded **274 raw findings**, or **162 distinct
package/advisory-ID pairs**, across **28 packages**. A fresh `pip-audit 2.10.1`
scan of the refreshed runtime lock on macOS ARM64/Python 3.12 examined **134
marker-applicable packages** and reported **50 raw findings**, or **29 distinct
package/advisory-ID pairs**, across **6 packages**. The core runtime subset
examined **32 packages** and reported **zero findings**. Linux and other Python
versions can include different packages; CI publishes its own current inventory.
The database can also change independently of the lockfile.

Raw counts include repeated advisory records. Deduplication here uses
`(package name, advisory ID)` and does not claim to merge every possible alias.
A package/version match is evidence to investigate, not proof that an attacker
can exploit this application's exposed routes. No advisories are globally
ignored by the scanners.

| Remaining package | Distinct IDs | Constraint and application exposure |
| --- | ---: | --- |
| Pillow 10.3.0 | 17 | OASIS pins exactly 10.3.0 and CAMEL requires `<11`; the scan's fixes are in 12.x. The web upload parser handles PDF through PyMuPDF and text through decoders; it does not pass uploads to Pillow. Image decoding/rendering in simulation dependencies still needs separate review before accepting untrusted images. |
| pytest 8.2.0 | 1 | OASIS includes this exact test-tool version as a runtime dependency. Its temporary-directory advisory concerns local test execution. CI's isolated core environment uses pytest 9.1.1, which is outside that advisory range, but a full OASIS installation still contains 8.2.0. |
| sentence-transformers 3.0.0 | 1 | OASIS pins this exact version. The advisory concerns loading attacker-controlled model/module configuration; its listed fix is 5.6.0. OASIS imports it eagerly, and its alternate Twitter recommendation path loads `paraphrase-MiniLM-L6-v2`. Model artifacts and the local model cache are a relevant trust boundary. |
| transformers 4.57.6 | 6 | Sentence Transformers 3.0.0 requires `transformers<5`, preventing the listed 5.x fixes. Default Twitter simulation loads `Twitter/twhin-bert-base` through `AutoTokenizer/AutoModel.from_pretrained` when recommendation refresh has posts to process, so model loading is reachable. That model identifier is fixed upstream; this is not proof of an arbitrary user-controlled checkpoint route. Some advisories concern different model/conversion/training helpers or narrower versions than the broad scanner match; they remain unresolved rather than being declared exploitable or dismissed. |
| unstructured 0.13.7 | 3 | OASIS pins it exactly. Listed fixes span 0.14.3–0.24.0 for XML parsing, URL fetching, and MSG attachment handling. The application's upload path does not call Unstructured; enabling those dependency features would create a different trust boundary. |
| nltk 3.10.3 | 1 | The refreshed version fixes the earlier reports but the current database also lists PYSEC-2026-3740, with no fixed version. It concerns caller-controlled model-artifact paths. No direct application route invokes those artifact APIs; transitive model/data loading remains a review requirement. |

Representative advisory records are
[NLTK artifact paths](https://github.com/advisories/GHSA-8mgp-746c-j5xp),
[Sentence Transformers model loading](https://github.com/advisories/GHSA-jhr6-gm9c-rqjv),
[Transformers model configuration](https://github.com/advisories/GHSA-29pf-2h5f-8g72),
and [Unstructured attachment traversal](https://github.com/advisories/GHSA-gm8q-m8mv-jj5m).
These residuals require an upstream OASIS/CAMEL update or a separately tested
maintained dependency fork. The current refresh makes neither change.

Reachability was checked against OASIS 0.2.5/CAMEL 0.2.78 source: Twitter's
default recommendation selection is in `oasis/environment/env.py`;
`Platform.update_rec_table()` reaches the lazy model loader in
`oasis/social_platform/recsys.py` once posts exist. An empty first round can
return before loading a checkpoint. Reddit/random recommendation
selection does not initialize those embedding checkpoints. CAMEL imports Pillow
in `camel/messages/base.py`; image decoding requires an image input. Its
Unstructured loaders are lazy/type-checking imports, and no NLTK or Unstructured
calls were found in the current text-only OASIS paths. These observations limit
the demonstrated exposure; they do not erase the installed-package advisories.

## CI coverage and limits

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`/`master`, and
manual dispatch. It performs:

- Lock freshness validation, backend regression/security tests, and root tests
  on Python 3.11 and 3.12, using dummy provider keys. The LLM endpoint points to
  an unused loopback port; provider interactions in the tests are mocked.
- A lightweight test installation derived from the current manifest and
  constrained to `uv.lock`. CAMEL, OASIS, and MCP are excluded from this test
  environment to avoid downloading the simulation/ML tree. The test runner is
  pytest 9.1.1. This validates API and mocked provider contracts, not actual
  simulation execution, GPU compatibility, or live Zep/Ollama service behavior.
- A blocking `pip-audit` scan of that locked core runtime, with a JSON artifact.
- Root/frontend `npm ci`, frontend security tests and production build on
  Node 22, and blocking `npm audit --audit-level=high` for both npm lockfiles.
- A complete runtime-lock audit without installing the heavy packages. Its
  findings are advisory-only while the constraints above remain; each run
  writes package counts to the job summary and uploads the raw report and
  exported requirements. A missing or invalid scanner report fails the job,
  so a network/scanner failure is not reported as a clean audit.

For a reproducible full-inventory scan from the repository root:

```sh
uv lock --check --directory backend
uv export --directory backend --locked --no-dev --no-emit-project --no-hashes \
  --output-file /tmp/mirofish-requirements.txt
uvx pip-audit==2.10.1 --disable-pip --no-deps \
  -r /tmp/mirofish-requirements.txt --format json \
  --output /tmp/mirofish-audit.json
```

`--no-deps` is safe for this inventory operation because `uv export` already
lists the resolved transitive packages; it must not be used with the direct-only
compatibility manifest to claim a complete audit. The scanner returns nonzero
when it finds advisories. Keep its JSON result when reviewing that exit status.
