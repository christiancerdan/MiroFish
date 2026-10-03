"""Unknown source demographics remain unknown in the actual OASIS prompt."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator


@pytest.fixture
def user_info_type(monkeypatch):
    # Load the vendored prompt implementation without requiring the optional
    # simulation stack. TextPrompt is only a type annotation on an unused method.
    prompts = ModuleType('camel.prompts')
    prompts.TextPrompt = str
    monkeypatch.setitem(sys.modules, 'camel.prompts', prompts)
    backend = Path(__file__).resolve()
    while backend.name != 'backend':
        backend = backend.parent
    path = backend / 'vendor/camel-oasis/oasis/social_platform/config/user.py'
    spec = importlib.util.spec_from_file_location('vendored_user_info_contract', path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module.UserInfo


def saved_profile(tmp_path, **demographics):
    profile = OasisAgentProfile(
        user_id=0, user_name='reader', name='Reader', bio='A fictional reader.',
        persona='Reader is an explicitly fictional participant.', **demographics,
    )
    target = tmp_path / 'reddit.json'
    object.__new__(OasisProfileGenerator)._save_reddit_json([profile], str(target))
    return profile, json.loads(target.read_text())[0]


def prompt_for(user_info_type, record):
    return user_info_type(name=record['name'], recsys_type='reddit', profile={
        'other_info': {'user_profile': record['persona'], **{
            key: record[key] for key in ('age', 'gender', 'mbti', 'country')
        }},
    }).to_system_message()


def test_unknown_demographics_roundtrip_as_null_without_fabricated_prompt(tmp_path, user_info_type, capsys):
    _, record = saved_profile(tmp_path)
    assert {key: record[key] for key in ('age', 'gender', 'mbti', 'country')} == {
        'age': None, 'gender': None, 'mbti': None, 'country': None,
    }
    capsys.readouterr()  # Serializer logs its file path; prompt rendering must stay silent.
    prompt = prompt_for(user_info_type, record)
    assert record['persona'] in prompt
    assert 'Unspecified demographics:' in prompt
    assert 'Do not infer or invent values' in prompt
    assert all(value not in prompt for value in ('None', '30 years old', 'ISTJ', '中国'))
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('age', [0, 37])
def test_known_demographics_and_age_zero_survive_export_and_prompt(tmp_path, user_info_type, age):
    profile, record = saved_profile(tmp_path, age=age, gender='nonbinary', mbti='INTP', country='Canada')
    assert record['age'] == age
    assert profile.to_reddit_format()['age'] == age
    assert profile.to_twitter_format()['age'] == age
    assert record['gender'] == 'nonbinary'
    prompt = prompt_for(user_info_type, record)
    for sentence in (f'Your age is {age}.', 'Your gender is nonbinary.',
                     'Your MBTI personality type is INTP.', 'Your country is Canada.'):
        assert sentence in prompt
    assert 'Unspecified demographics:' not in prompt


def test_partial_demographics_do_not_fill_missing_fields(tmp_path, user_info_type):
    _, record = saved_profile(tmp_path, gender='女', country='Japan')
    assert record['gender'] == 'female'
    assert record['age'] is None and record['mbti'] is None
    prompt = prompt_for(user_info_type, record)
    assert 'Your gender is female.' in prompt
    assert 'Your country is Japan.' in prompt
    assert 'Unspecified demographics: age, mbti.' in prompt
    assert 'Your age is' not in prompt
    assert 'Your MBTI personality type is' not in prompt


@pytest.mark.parametrize('profile', [None, {}, {'other_info': {}}, {'other_info': {'user_profile': None}}])
def test_absent_profile_details_produce_safe_name_and_unknown_demographics(user_info_type, profile):
    prompt = user_info_type(name='Reader', profile=profile, recsys_type='reddit').to_system_message()
    assert 'Your name is Reader.' in prompt
    assert 'Unspecified demographics:' in prompt
    assert 'None' not in prompt
