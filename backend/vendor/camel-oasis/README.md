# MiroFish's maintained OASIS package

This directory carries OASIS 0.2.5, published by CAMEL-AI.org under Apache-2.0.
`UPSTREAM.json` records the official PyPI wheel URL, SHA-256, and each upstream
module's original SHA-256. Original module copyright headers and `LICENSE` are
retained. The local distribution version is `0.2.5+mirofish.1`; it is not an
upstream release.

The upstream release's metadata pins older model-processing dependencies and
includes unrelated test, pre-commit, document-processing and service-client
packages in every installation. This fork declares the packages actually
imported by OASIS, supports Python 3.11/3.12, and uses CAMEL 0.2.90 with patched
Sentence Transformers/Transformers. CAMEL 0.2.90 is also selected by upstream
OASIS's current main branch. Real OASIS agents, social actions, SQLite traces,
and recommendation implementations are retained.

Runtime dependencies are resolved normally by uv/pip. No dependency checks or
package metadata are bypassed. MiroFish keeps the package in its optional
`simulation` extra, installed into a separate worker environment. The API
environment does not require these ML libraries.

Downstream changes are listed in `PATCHES.md`. Review that list, run the real
runtime contract tests, regenerate `uv.lock`, and audit the simulation extra
when changing the package. Upstream: https://github.com/camel-ai/oasis
