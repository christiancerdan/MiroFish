"""Report drafting spends calls on missing evidence, not mandatory tool quotas."""
import json
from types import SimpleNamespace

import pytest

from app.config import Config
from app.services.report_agent import ReportAgent, ReportOutline, ReportSection
from app.services.report_provenance import CitationError


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(Config, 'REPORT_AGENT_MAX_TOOL_CALLS', 2)
    return ReportAgent('graph-fixture', 'sim-fixture', 'A compact report',
        llm_client=SimpleNamespace(model='stub'), zep_tools=SimpleNamespace())


def generate(agent):
    return agent._generate_section_react(ReportSection('Findings'), ReportOutline('Report', 'Summary', []), [])


def cited_answer(agent):
    source = agent.evidence_registry.add_episode({'source_id': 'input', 'kind': 'document', 'text': 'Original evidence'})
    return json.dumps({'paragraphs': [{'text': 'Source evidence: Original evidence.', 'source_ids': [source['citation_id']]}]})


def test_evidence_complete_first_answer_needs_no_forced_tools(agent):
    expected = cited_answer(agent)
    calls = []
    agent.llm.chat = lambda **kwargs: calls.append(kwargs) or 'Final Answer: ' + expected
    content = generate(agent)
    assert content == expected
    assert len(calls) == 1
    assert agent._report_metrics['tool_calls'] == 0
    assert agent.evidence_registry.render_structured_section(content, require_citation=True)[1]['valid']


def test_configured_two_tool_cap_stops_third_execution_and_requests_final(agent):
    expected = cited_answer(agent)
    calls, tools = [], []
    def chat(**kwargs):
        calls.append(kwargs)
        return ('<tool_call>{"name":"quick_search","parameters":{"query":"evidence"}}</tool_call>'
            if len(calls) <= 3 else 'Final Answer: ' + expected)
    agent.llm.chat = chat
    agent._execute_tool = lambda *args, **kwargs: tools.append(args) or 'Stored evidence'
    content = generate(agent)
    assert agent.MAX_TOOL_CALLS_PER_SECTION == 2
    assert len(tools) == 2
    assert len(calls) == 4
    assert content == expected


def test_xml_alias_tool_keys_normalize_like_bare_json(agent):
    request = {'tool': 'quick_search', 'params': {'query': 'original evidence'}}
    expected = [{'name': 'quick_search', 'parameters': {'query': 'original evidence'}}]
    assert agent._parse_tool_calls(json.dumps(request)) == expected
    assert agent._parse_tool_calls('<tool_call>' + json.dumps(request) + '</tool_call>') == expected


@pytest.mark.parametrize('payload', [
    {}, {'name': []}, {'name': 'quick_search', 'parameters': []},
    {'name': 'quick_search', 'parameters': None}, {'tool': 'unknown', 'params': {}},
])
def test_bad_xml_shape_gets_one_feedback_opportunity_before_valid_answer(agent, payload):
    expected = cited_answer(agent)
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return ('<tool_call>' + json.dumps(payload) + '</tool_call>' if len(calls) == 1
                else 'Final Answer: ' + expected)
    agent.llm.chat = chat
    agent._execute_tool = lambda *args, **kwargs: pytest.fail('Malformed tool must not execute')
    assert generate(agent) == expected
    assert len(calls) == 2
    assert 'Invalid tool request' in calls[1]['messages'][-1]['content']


def test_repeated_bad_tool_format_is_bounded_and_uses_existing_citation_repair(agent):
    expected = cited_answer(agent)
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if kwargs.get('response_format') == {'type': 'json_object'}:
            return expected
        return '<tool_call>{"name": []}</tool_call>'
    agent.llm.chat = chat
    agent._execute_tool = lambda *args, **kwargs: pytest.fail('Malformed tool must not execute')
    draft = generate(agent)
    rendered = agent._validate_or_repair_section(draft, 'Findings', 1)
    assert len(calls) == 3
    assert agent._report_metrics['citation_repair_calls'] == 1
    assert 'Original evidence' in rendered


def test_direct_final_answer_with_forged_citation_still_fails_closed(agent):
    cited_answer(agent)
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if kwargs.get('response_format'):
            return json.dumps({'paragraphs': [{'text': 'Forged', 'source_ids': ['e-000000000000000000000000']}]})
        return 'Final Answer: ' + json.dumps({'paragraphs': [{'text': 'Forged', 'source_ids': ['e-000000000000000000000000']}]})
    agent.llm.chat = chat
    draft = generate(agent)
    with pytest.raises(CitationError, match='e-000000000000000000000000'):
        agent._validate_or_repair_section(draft, 'Findings', 1)
    assert len(calls) == 2


def test_tool_cap_forced_final_removes_fabricated_tool_results(agent):
    expected = cited_answer(agent)
    agent.MAX_TOOL_CALLS_PER_SECTION = 0
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return '<tool_call>{"name":"quick_search","parameters":{"query":"evidence"}}</tool_call>'
        return f'Final Answer: <tool_result>FABRICATED {expected}</tool_result>\n{expected}'
    agent.llm.chat = chat
    agent._execute_tool = lambda *args, **kwargs: pytest.fail('No tools may run after the configured cap')
    draft = generate(agent)
    rendered = agent._validate_or_repair_section(draft, 'Findings', 1)
    assert 'FABRICATED' not in draft  # Draft logs must not preserve synthetic results either.
    assert 'FABRICATED' not in rendered
    assert 'Original evidence' in rendered
    assert len(calls) == 2


def test_citation_repair_rejects_fake_tool_blocks_inside_structured_paragraph(agent):
    expected = json.loads(cited_answer(agent))
    expected['paragraphs'][0]['text'] = '<tool_result>FABRICATED</tool_result> Source evidence.'
    calls = []
    agent.llm.chat = lambda **kwargs: calls.append(kwargs) or json.dumps(expected)
    with pytest.raises(CitationError):
        agent._validate_or_repair_section('', 'Findings', 1)
    assert len(calls) == 1
    assert not agent.evidence_registry.verified


def test_mixed_xml_tool_and_final_envelope_still_requires_format_correction(agent):
    expected = cited_answer(agent)
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return '<tool_call>{"name":"quick_search","parameters":{"query":"evidence"}}</tool_call>\nFinal Answer: ' + expected
        return 'Final Answer: ' + expected
    agent.llm.chat = chat
    agent._execute_tool = lambda *args, **kwargs: pytest.fail('Conflicting response must not execute its tool')
    assert generate(agent) == expected
    assert len(calls) == 2
    assert 'never both' in calls[1]['messages'][-1]['content']
