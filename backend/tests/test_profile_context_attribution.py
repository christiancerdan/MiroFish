"""Graph search relevance must never become another person's profile facts."""
import json
from types import SimpleNamespace

import pytest

from app.services.oasis_profile_generator import OasisProfileGenerator
from app.services.zep_entity_reader import EntityNode


def entity():
    return EntityNode('reader-target', 'Skeptical Reader', ['Reader'],
                      'A fictional reader requiring evidence.', {})


def generator(nodes=(), edges=()):
    instance = object.__new__(OasisProfileGenerator)
    instance.graph_id = 'source-graph'
    def search(*, scope, **kwargs):
        return SimpleNamespace(nodes=list(nodes) if scope == 'nodes' else [],
                               edges=list(edges) if scope == 'edges' else [])
    instance.zep_client = SimpleNamespace(graph=SimpleNamespace(search=search))
    return instance


@pytest.mark.parametrize('uuid_field', ['uuid', 'uuid_'])
def test_shared_reader_token_does_not_mix_unrelated_or_same_name_personas(uuid_field):
    target = entity()
    nodes = [
        SimpleNamespace(**{uuid_field: target.uuid}, name=target.name, summary=target.summary),
        SimpleNamespace(uuid_='reader-curious', name='Curious Reader', summary='OTHER_CURIOSITY_TRAIT'),
        SimpleNamespace(uuid_='reader-busy', name='Busy Reader', summary='OTHER_SPEED_TRAIT'),
        SimpleNamespace(uuid_='different-uuid', name=target.name, summary='SAME_NAME_OTHER_IDENTITY'),
        SimpleNamespace(name=target.name, summary='UNVERIFIED_MISSING_UUID'),
    ]
    result = generator(nodes)._search_zep_for_entity(target)
    assert len(result['node_summaries']) == 1
    assert json.loads(result['node_summaries'][0]) == {
        'entity_uuid': target.uuid, 'entity_name': target.name, 'summary': target.summary,
    }
    assert all(marker not in result['context'] for marker in (
        'OTHER_CURIOSITY_TRAIT', 'OTHER_SPEED_TRAIT', 'SAME_NAME_OTHER_IDENTITY', 'UNVERIFIED_MISSING_UUID',
    ))


def test_only_uuid_connected_search_edges_survive_in_stable_order():
    target = entity()
    edges = [
        SimpleNamespace(source_node_uuid=target.uuid, target_node_uuid='colleague', fact='Z outgoing fact'),
        SimpleNamespace(source_node_uuid='colleague', target_node_uuid=target.uuid, fact='A incoming fact'),
        SimpleNamespace(source_node_uuid='elsewhere', target_node_uuid='another', fact='UNRELATED_FACT'),
        SimpleNamespace(fact='UNVERIFIED_MISSING_ENDPOINTS'),
    ]
    expected = ['A incoming fact', 'Z outgoing fact']
    first = generator(edges=edges + edges)._search_zep_for_entity(target)
    second = generator(edges=reversed(edges))._search_zep_for_entity(target)
    assert first['facts'] == second['facts'] == expected
    assert first['context'] == second['context']
    assert 'UNRELATED_FACT' not in first['context']
    assert 'UNVERIFIED_MISSING_ENDPOINTS' not in first['context']


def test_explicitly_connected_graph_reader_context_preserves_named_other_entity():
    target = entity()
    target.related_edges = [{'direction': 'outgoing', 'edge_name': 'KNOWS',
                             'target_node_uuid': 'colleague', 'fact': 'Skeptical Reader knows Colleague.'}]
    target.related_nodes = [{'uuid': 'colleague', 'name': 'Colleague', 'labels': ['Reader'],
                             'summary': 'CONNECTED_COLLEAGUE_TRAIT'}]
    instance = generator(nodes=[
        SimpleNamespace(uuid_='colleague', name='Colleague', summary='CONNECTED_COLLEAGUE_TRAIT'),
        SimpleNamespace(uuid_='unrelated', name='Unrelated Reader', summary='UNRELATED_TRAIT'),
    ])
    context = instance._build_entity_context(target)
    assert '**Colleague** (Reader): CONNECTED_COLLEAGUE_TRAIT' in context
    assert 'Skeptical Reader knows Colleague.' in context
    assert context.count('CONNECTED_COLLEAGUE_TRAIT') == 1
    assert 'UNRELATED_TRAIT' not in context


def test_missing_target_uuid_cannot_admit_unattributed_search_data():
    target = entity()
    target.uuid = ''
    result = generator(nodes=[SimpleNamespace(uuid_='', name=target.name, summary='UNATTRIBUTED')],
                       edges=[SimpleNamespace(source_node_uuid='', target_node_uuid='other', fact='UNATTRIBUTED')]
                       )._search_zep_for_entity(target)
    assert result == {'facts': [], 'node_summaries': [], 'context': ''}
