# Downstream changes from the PyPI OASIS 0.2.5 wheel

1. Add a maintained Hatchling build manifest and local version
   `0.2.5+mirofish.1`; permit Python 3.11/3.12, which MiroFish tests.
2. Upgrade CAMEL to 0.2.90 and declare patched compatible model dependencies:
   Sentence Transformers >=6.1, Transformers >=5.18, and Torch >=2.14.1.
3. Declare the actual NumPy, scikit-learn, tqdm, graph, Neo4j and pandas runtime
   imports explicitly. Remove the unused Unstructured, pytest, pytest-asyncio,
   pre-commit, Slack, OAuth, OpenAPI-validation and Prance dependencies. Pillow
   is provided by CAMEL and constrained to >=12.3 by the simulation extra.
4. Keep `UPSTREAM.json`, this patch log, Apache license and upstream headers so
   every downstream source change is reviewable against the original wheel.

5. Harden the two existing, fixed recommendation model loaders in
   `oasis/social_platform/recsys.py`: pin their upstream revisions, explicitly
   disable remote Python execution, and require safetensors weights. Public
   model metadata on 2026-10-02 confirmed `model.safetensors` exists for both
   revisions. No weights were downloaded during the metadata review.

   - `Twitter/twhin-bert-base`: `82ac392ce81f94560c391311ee2ddd024c5ac1fc`
   - `sentence-transformers/paraphrase-MiniLM-L6-v2`:
     `c9a2bfebc254878aee8c3aca9e6844d5bbb102d1`

6. Preserve unknown source demographics in the Reddit system prompt in
   `oasis/social_platform/config/user.py`. Render only supplied age, gender,
   MBTI and country values, label missing fields unspecified, and instruct the
   agent not to infer them. Preserve valid zero ages and arbitrary supplied
   gender labels. Handle missing profile details safely and remove the raw
   profile print. The upstream agent generators still receive all required
   demographic keys; MiroFish exports unknown values as JSON null.

The OASIS social-action implementation is unchanged. Recommendation model
loading and Reddit profile-prompt construction differ from the original wheel.
Additional source changes must be recorded here and covered by a focused
contract test.
