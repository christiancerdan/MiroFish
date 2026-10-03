"""Profiles preserve source actors and reject incomplete model output."""
import json
from types import SimpleNamespace

import pytest

from app.services import oasis_profile_generator as module
from app.services.oasis_profile_generator import (
    OasisAgentProfile, OasisProfileGenerator, ProfileGenerationError,
)
from app.services.zep_entity_reader import EntityNode


def valid_profile():
    return {
        'bio': 'A fictional time-poor reader.',
        'persona': 'Busy Reader is an explicitly fictional reader who values clear relevance and quickly understood benefits.',
        'age': None, 'gender': None, 'mbti': None, 'country': None,
        'profession': None, 'interested_topics': [],
    }


def response(value, finish_reason='stop'):
    content = value if isinstance(value, str) else json.dumps(value)
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content),
    )])


def generator(monkeypatch, *responses):
    instance = object.__new__(OasisProfileGenerator)
    instance.client, instance.model_name = SimpleNamespace(), 'offline-model'
    instance.zep_client, instance.graph_id = None, None
    calls, remaining = [], list(responses)
    def complete(client, **kwargs):
        calls.append(kwargs)
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(module, 'create_chat_completion', complete)
    monkeypatch.setattr(instance, '_print_generated_profile', lambda *args: None)
    return instance, calls


def generate(instance, entity_type='Reader'):
    return instance._generate_profile_with_llm(
        'Busy Reader', entity_type,
        'A fictional time-poor generalist who values clear relevance and quickly understood benefits.',
        {'assumed': True}, 'The three readers are fictional assumptions, not observed demographics.',
    )


@pytest.mark.parametrize('entity_type', ['Reader', 'Customer', 'NovelActor'])
def test_unknown_actor_types_use_neutral_source_based_prompt(monkeypatch, entity_type):
    instance, calls = generator(monkeypatch, response(valid_profile()))
    result = generate(instance, entity_type)
    prompt = calls[0]['messages'][1]['content']
    assert 'Actor kind is unspecified by the type label' in prompt
    assert 'Known actor kind: organization/group' not in prompt
    assert f'"entity_type": "{entity_type}"' in prompt
    assert '"entity_name": "Busy Reader"' in prompt
    assert 'fictional time-poor generalist' in prompt
    assert 'explicit fictional/assumed status' in prompt
    assert '2000' not in prompt
    assert result == valid_profile()
    assert len(calls) == 1


@pytest.mark.parametrize('entity_type,kind', [
    ('Person', 'individual'), ('Student', 'individual'),
    ('Organization', 'organization/group'), ('Company', 'organization/group'),
])
def test_recognized_actor_types_retain_supported_kind_without_invented_history(monkeypatch, entity_type, kind):
    instance, calls = generator(monkeypatch, response(valid_profile()))
    generate(instance, entity_type)
    prompt = calls[0]['messages'][1]['content']
    assert f'Known actor kind: {kind}' in prompt
    assert 'Do not add personal or organizational history' in prompt
    assert 'Unknown optional details must be null or omitted' in prompt


def test_profile_preserves_name_type_uuid_and_unknown_demographics(monkeypatch):
    instance, calls = generator(monkeypatch, response(valid_profile()))
    entity = EntityNode('source_7', 'Busy Reader', ['Reader'], 'Fictional reader.', {})
    monkeypatch.setattr(instance, '_generate_username', lambda name: 'busy_reader')
    monkeypatch.setattr(module.random, 'randint', lambda *args: pytest.fail('Invented random profile field'))
    profile = instance.generate_profile_from_entity(entity, user_id=7)
    assert (profile.name, profile.source_entity_type, profile.source_entity_uuid) == ('Busy Reader', 'Reader', 'source_7')
    assert profile.persona == valid_profile()['persona']
    assert profile.age is None and profile.gender is None and profile.profession is None
    assert (profile.karma, profile.friend_count, profile.follower_count, profile.statuses_count) == (1000, 100, 150, 500)
    assert len(calls) == 1


@pytest.mark.parametrize('bad_response', [
    response('{"bio":"Fictional reader","persona":"truncated', 'length'),
    response(valid_profile(), 'length'),
    response('{"bio":"only a partial string'),
    response({}),
    response({'bio': 'Missing persona'}),
    response({'bio': 'Bio', 'persona': {'text': 'Wrong type'}}),
])
def test_invalid_or_truncated_profiles_regenerate_without_repair(monkeypatch, bad_response):
    instance, calls = generator(monkeypatch, bad_response, response(valid_profile()))
    monkeypatch.setattr(instance, '_generate_profile_rule_based', lambda *args: pytest.fail('Silent rule fallback'))
    assert generate(instance) == valid_profile()
    assert len(calls) == 2
    assert [call['max_tokens'] for call in calls] == [8192, 16384]
    assert 'validation_error' in calls[1]['messages'][-1]['content']


def test_mixed_parse_and_schema_failures_share_two_attempts_and_raise(monkeypatch):
    instance, calls = generator(monkeypatch,
        response('{"bio":"bad', 'length'), response({}), response(valid_profile()),
    )
    monkeypatch.setattr(instance, '_generate_profile_rule_based', lambda *args: pytest.fail('Silent rule fallback'))
    with pytest.raises(ProfileGenerationError):
        generate(instance)
    assert len(calls) == 2


def test_configured_model_limit_bounds_both_attempts(monkeypatch):
    instance, calls = generator(monkeypatch, response({}), response(valid_profile()))
    instance.token_limit = 6000
    generate(instance)
    assert [call['max_tokens'] for call in calls] == [6000, 6000]


def test_provider_error_is_terminal_and_does_not_invent_profile(monkeypatch):
    instance, calls = generator(monkeypatch, RuntimeError('PRIVATE_ERROR_SENTINEL'))
    with pytest.raises(ProfileGenerationError) as error:
        generate(instance)
    assert len(calls) == 1
    assert 'PRIVATE_ERROR_SENTINEL' not in str(error.value)


@pytest.mark.parametrize('field,value', [
    ('bio', ''), ('bio', 'x' * 281), ('persona', None), ('persona', 'x' * 1201),
    ('age', True), ('age', -1), ('age', '30'), ('gender', {}),
    ('interested_topics', 'science'), ('interested_topics', [None]), ('extra', 'not permitted'),
])
def test_strict_profile_fields_reject_structurally_invalid_response(field, value):
    invalid = valid_profile()
    invalid[field] = value
    with pytest.raises(ValueError):
        OasisProfileGenerator._validate_profile_response(invalid)


def test_model_generation_failure_is_not_published_as_batch_success(monkeypatch, tmp_path):
    instance, calls = generator(monkeypatch, response({}), response({}))
    monkeypatch.setattr(instance, '_generate_profile_rule_based', lambda *args: pytest.fail('Silent fallback'))
    target = tmp_path / 'profiles.json'
    entities = [EntityNode(str(i), f'Reader {i}', ['Reader'], 'A fictional reader.', {}) for i in range(3)]
    with pytest.raises(ProfileGenerationError):
        instance.generate_profiles_from_entities(entities, parallel_count=1, realtime_output_path=str(target))
    assert len(calls) == 2
    assert not target.exists()


def test_intentional_rule_mode_is_preserved_without_paid_calls(monkeypatch):
    instance, calls = generator(monkeypatch)
    entity = EntityNode('reader', 'Busy Reader', ['Reader'], 'A fictional time-poor reader.', {})
    profiles = instance.generate_profiles_from_entities([entity], use_llm=False, parallel_count=1)
    assert len(profiles) == 1
    assert profiles[0].name == 'Busy Reader'
    assert profiles[0].source_entity_type == 'Reader'
    assert profiles[0].persona == entity.summary
    assert calls == []
