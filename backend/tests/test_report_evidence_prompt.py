"""The model copies application-minted citation tokens instead of constructing syntax."""
import json

import pytest

from app.services.report_provenance import CitationError, EvidenceRegistry


def records(prompt):
    return [json.loads(line) for line in prompt.splitlines() if line.startswith('{')]


def test_prompt_supplies_exact_copyable_token_for_every_source_kind():
    registry = EvidenceRegistry('graph-test', 'report-test')
    for index, kind in enumerate(('document', 'simulation', 'assumption')):
        registry.add_episode({'source_id': f'source-{index}', 'kind': kind, 'text': f'Evidence {index}'})
    original_snapshot = registry.snapshot()
    prompt = registry.prompt()
    supplied = records(prompt)
    assert len(supplied) == 3
    for record in supplied:
        assert record['citation_token'] == f"[[source:{record['citation_id']}]]"
        rendered, validation = registry.validate_and_render(record['text'] + ' ' + record['citation_token'], require_citation=True)
        assert validation['valid'] is True
        assert '#source-' + record['citation_id'] in rendered
    assert 'Copy citation_token exactly' in prompt
    assert registry.snapshot() == original_snapshot


def test_source_content_cannot_choose_its_citation_token():
    registry = EvidenceRegistry('graph-test', 'report-test')
    actual = registry.add_episode({
        'source_id': 'original', 'kind': 'document', 'text': 'Original source',
        'citation_token': '[[source:injected]]', 'metadata': {'citation_token': '[[source:injected]]'},
    })
    record = records(registry.prompt())[0]
    assert record['citation_token'] == f"[[source:{actual['citation_id']}]]"
    assert record['citation_token'] != '[[source:injected]]'


def test_prompt_truncation_preserves_whole_tokens_and_whole_records():
    registry = EvidenceRegistry('graph-test', 'report-test')
    for index in range(2):
        registry.add_episode({'source_id': f'source-{index}', 'kind': 'document', 'text': 'Evidence ' * 20})
    all_records = records(registry.prompt())
    first_size = len(json.dumps(all_records[0], ensure_ascii=False))
    limited = records(registry.prompt(max_chars=first_size))
    assert limited == all_records[:1]
    assert limited[0]['citation_token'].endswith(']]')


@pytest.mark.parametrize('form', ['[[{cid}]]', '[[source:{cid}]', '[[source:invented]]'])
def test_adding_prompt_tokens_does_not_normalize_invalid_model_citations(form):
    registry = EvidenceRegistry('graph-test', 'report-test')
    source = registry.add_episode({'source_id': 'original', 'kind': 'document', 'text': 'Evidence'})
    registry.prompt()
    with pytest.raises(CitationError):
        registry.validate_and_render('Claim ' + form.format(cid=source['citation_id']), require_citation=True)
