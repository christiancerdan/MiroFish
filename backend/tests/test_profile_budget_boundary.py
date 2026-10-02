"""A budget stop must escape both profile retries and threadpool fallback."""
from types import SimpleNamespace

import pytest

from app.services import oasis_profile_generator as module
from app.services.oasis_profile_generator import OasisProfileGenerator
from app.services.zep_entity_reader import EntityNode
from app.utils.budget import BudgetExceeded


def test_model_budget_error_does_not_retry_or_generate_rule_profile(monkeypatch):
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    generator.model_name = "test-model"
    generator.client = SimpleNamespace()
    calls = []
    def stopped(*args, **kwargs):
        calls.append(True)
        raise BudgetExceeded("max_calls")
    monkeypatch.setattr(module, "create_chat_completion", stopped)
    monkeypatch.setattr(generator, "_generate_profile_rule_based", lambda *a, **k: pytest.fail("Budget stop replaced with fallback"))
    with pytest.raises(BudgetExceeded):
        generator._generate_profile_with_llm("Alice", "Person", "Engineer", {}, "context")
    assert len(calls) == 1


def test_threadpool_budget_error_does_not_return_successful_profiles(monkeypatch):
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    def stopped(*args, **kwargs):
        raise BudgetExceeded("max_calls")
    monkeypatch.setattr(generator, "generate_profile_from_entity", stopped)
    entity = EntityNode("alice", "Alice", ["Person"], "Engineer", {})
    with pytest.raises(BudgetExceeded):
        generator.generate_profiles_from_entities([entity], parallel_count=1)
