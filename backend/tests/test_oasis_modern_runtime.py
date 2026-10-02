"""Real OASIS/model contracts with local weights and mocked provider transport."""

import importlib.util
import json

import httpx
import pytest

if importlib.util.find_spec("oasis") is None:
    pytest.skip("Simulation extra is not installed", allow_module_level=True)


def test_real_oasis_tool_call_persists_post_and_action_trace(monkeypatch):
    from app.utils.llm_provider import resolve_llm_settings
    from scripts import check_oasis_provider as probe

    requests = []

    async def reply(_transport, request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "http://127.0.0.1:9/v1/chat/completions"
        assert any(tool["function"]["name"] == "create_post" for tool in payload["tools"])
        return httpx.Response(200, json={
            "id": "chatcmpl-local-contract", "object": "chat.completion",
            "created": 1, "model": "local-contract-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call-post", "type": "function", "function": {
                    "name": "create_post", "arguments": json.dumps({"content": probe.PROBE_CONTENT}),
                }}],
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        })

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reply)
    settings = resolve_llm_settings({
        "LLM_PROVIDER": "openai_compatible", "LLM_API_KEY": "dummy",
        "LLM_BASE_URL": "http://127.0.0.1:9/v1", "LLM_MODEL_NAME": "local-contract-model",
    })
    assert probe.run_check(settings, timeout=30) == {"posts": 1, "create_post_traces": 1}
    assert requests


@pytest.fixture
def tiny_bert(tmp_path, monkeypatch):
    """Build a real, tiny safetensors checkpoint without fetching any model."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    import torch
    from transformers import BertConfig, BertModel, BertTokenizer

    torch.set_num_threads(1)
    folder = tmp_path / "tiny-bert"
    vocab = {word: index for index, word in enumerate([
        "[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "hello", "world",
        "local", "post", "one", "two", "profile", "recent", "#", ":",
    ])}
    tokenizer = BertTokenizer(vocab=vocab, model_max_length=64)
    tokenizer.save_pretrained(folder)
    model = BertModel(BertConfig(
        vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1,
        num_attention_heads=2, intermediate_size=32, max_position_embeddings=64,
    ))
    model.save_pretrained(folder, safe_serialization=True)
    assert (folder / "model.safetensors").is_file()
    return folder


def test_twitter_recommendations_load_safe_snapshot_and_run_real_vectors(tiny_bert, monkeypatch):
    import torch
    from transformers import AutoModel, AutoTokenizer
    from oasis.social_platform import recsys
    from oasis.social_platform.process_recsys_posts import generate_post_vector

    original_model = AutoModel.from_pretrained
    original_tokenizer = AutoTokenizer.from_pretrained
    calls = []

    def load_model(*, pretrained_model_name_or_path, **kwargs):
        calls.append(("model", pretrained_model_name_or_path, kwargs.copy()))
        kwargs.pop("revision")
        return original_model(str(tiny_bert), local_files_only=True, **kwargs)

    def load_tokenizer(*, pretrained_model_name_or_path, **kwargs):
        calls.append(("tokenizer", pretrained_model_name_or_path, kwargs.copy()))
        kwargs.pop("revision")
        return original_tokenizer(str(tiny_bert), local_files_only=True, **kwargs)

    monkeypatch.setattr(AutoModel, "from_pretrained", load_model)
    monkeypatch.setattr(AutoTokenizer, "from_pretrained", load_tokenizer)
    monkeypatch.setattr(recsys, "twhin_model", None)
    monkeypatch.setattr(recsys, "twhin_tokenizer", None)
    recsys.reset_globals()
    tokenizer, model = recsys.get_recsys_model("twhin-bert")
    vectors = generate_post_vector(model, tokenizer, ["hello world", "local post"], batch_size=1)
    assert vectors.shape == (2, 16)
    assert torch.isfinite(vectors).all()

    recommendations = recsys.rec_sys_personalized_twh(
        user_table=[{"user_id": 0, "agent_id": 0, "num_followers": 1, "bio": "hello world"}],
        post_table=[
            {"post_id": 1, "user_id": 0, "content": "local post one", "created_at": "0"},
            {"post_id": 2, "user_id": 0, "content": "local post two", "created_at": "0"},
        ],
        latest_post_count=2, trace_table=[], rec_matrix=[[]],
        max_rec_post_len=1, current_time=1,
    )
    assert len(recommendations) == 1
    assert len(recommendations[0]) == 1
    assert recommendations[0][0] in {1, 2}
    assert len(calls) == 2
    for kind, name, kwargs in calls:
        assert name == "Twitter/twhin-bert-base"
        assert kwargs["revision"] == recsys.TWHIN_REVISION
        assert kwargs["trust_remote_code"] is False
        if kind == "model":
            assert kwargs["use_safetensors"] is True


def test_sentence_transformer_recommendations_use_safe_local_weights(tiny_bert, tmp_path, monkeypatch):
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.sentence_transformer import modules as models
    from oasis.social_platform import recsys

    local_path = tmp_path / "tiny-sentence-transformer"
    embedding = models.Transformer(str(tiny_bert), model_args={"local_files_only": True, "use_safetensors": True})
    sentence_model = SentenceTransformer(modules=[embedding, models.Pooling(16)], device="cpu")
    sentence_model.save_pretrained(str(local_path))
    calls = []

    def load_snapshot(name, **kwargs):
        calls.append((name, kwargs.copy()))
        kwargs.pop("revision")
        kwargs.pop("cache_folder")
        return SentenceTransformer(str(local_path), local_files_only=True, **kwargs)

    monkeypatch.setattr(recsys, "SentenceTransformer", load_snapshot)
    monkeypatch.setattr(recsys, "model", None)
    recommendations = recsys.rec_sys_personalized(
        user_table=[{"user_id": 0, "bio": "hello world"}],
        post_table=[
            {"post_id": 1, "user_id": 1, "content": "hello world"},
            {"post_id": 2, "user_id": 1, "content": "local post two"},
        ],
        trace_table=[], rec_matrix=[[]], max_rec_post_len=1,
    )
    assert len(recommendations) == 1
    assert len(recommendations[0]) == 1
    assert recommendations[0][0] in {1, 2}
    assert len(calls) == 1
    name, kwargs = calls[0]
    assert name == "paraphrase-MiniLM-L6-v2"
    assert kwargs["revision"] == recsys.MINILM_REVISION
    assert kwargs["trust_remote_code"] is False
    assert kwargs["model_kwargs"]["use_safetensors"] is True


def test_recommendation_loader_rejects_arbitrary_model_locations():
    from oasis.social_platform import recsys

    with pytest.raises(Exception, match="Failed to load the model"):
        recsys.load_model("/tmp/untrusted-model")
