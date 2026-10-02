"""Prepared simulations require profile files only for their enabled platforms."""
import json

import pytest
from flask import Flask

from app.api import simulation as simulation_api
from app.config import Config
from app.models.project import ProjectManager, ProjectStatus
from app.services.simulation_manager import SimulationManager, SimulationStatus
from app.services.simulation_runner import SimulationRunState, RunnerStatus


@pytest.fixture
def prepared_simulation(tmp_path, monkeypatch):
    simulations = tmp_path / 'simulations'
    projects = tmp_path / 'projects'
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(simulations))
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(simulations))
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(projects))
    project = ProjectManager.create_project('Platform preparation regression')
    project.graph_id = 'graph-platforms'
    project.status = ProjectStatus.GRAPH_COMPLETED
    ProjectManager.save_project(project)

    def create(*, twitter=True, reddit=True, files=(), legacy=False):
        manager = SimulationManager()
        state = manager.create_simulation(
            project.project_id, project.graph_id,
            enable_twitter=twitter, enable_reddit=reddit,
        )
        state.status = SimulationStatus.COMPLETED
        state.config_generated = True
        state.profiles_generated = True
        manager._save_simulation_state(state)
        folder = simulations / state.simulation_id
        (folder / 'simulation_config.json').write_text('{}', encoding='utf-8')
        for filename in files:
            content = '[{"name": "Test agent"}]' if filename.endswith('.json') else 'name\nTest agent\n'
            (folder / filename).write_text(content, encoding='utf-8')
        if legacy:
            state_data = state.to_dict()
            state_data.pop('enable_twitter')
            state_data.pop('enable_reddit')
            (folder / 'state.json').write_text(json.dumps(state_data), encoding='utf-8')
        return state, folder

    return create


@pytest.mark.parametrize('twitter, reddit, files', [
    (False, True, ['reddit_profiles.json']),
    (True, False, ['twitter_profiles.csv']),
    (True, True, ['reddit_profiles.json', 'twitter_profiles.csv']),
])
def test_prepared_helper_accepts_files_for_enabled_platforms(prepared_simulation, twitter, reddit, files):
    state, _folder = prepared_simulation(twitter=twitter, reddit=reddit, files=files)
    ready, info = simulation_api._check_simulation_prepared(state.simulation_id)
    assert ready is True, info
    assert info['status'] == 'completed'
    assert set(info['existing_files']) == {'state.json', 'simulation_config.json', *files}


@pytest.mark.parametrize('twitter, reddit, files, missing', [
    (False, True, [], 'reddit_profiles.json'),
    (True, False, [], 'twitter_profiles.csv'),
    (True, True, ['reddit_profiles.json'], 'twitter_profiles.csv'),
    (True, True, ['twitter_profiles.csv'], 'reddit_profiles.json'),
])
def test_prepared_helper_rejects_missing_enabled_platform_file(prepared_simulation, twitter, reddit, files, missing):
    state, _folder = prepared_simulation(twitter=twitter, reddit=reddit, files=files)
    ready, info = simulation_api._check_simulation_prepared(state.simulation_id)
    assert ready is False
    assert missing in info['missing_files']


@pytest.mark.parametrize('files, expected', [
    (['reddit_profiles.json'], False),
    (['twitter_profiles.csv'], False),
    (['reddit_profiles.json', 'twitter_profiles.csv'], True),
])
def test_legacy_prepared_state_defaults_to_both_platforms(prepared_simulation, files, expected):
    state, _folder = prepared_simulation(files=files, legacy=True)
    ready, info = simulation_api._check_simulation_prepared(state.simulation_id)
    assert ready is expected, info
    if not expected:
        assert set(info['missing_files']) == {'reddit_profiles.json', 'twitter_profiles.csv'} - set(files)


def test_completed_reddit_only_simulation_can_force_restart(prepared_simulation, monkeypatch):
    state, folder = prepared_simulation(twitter=False, reddit=True, files=['reddit_profiles.json'])
    assert not (folder / 'twitter_profiles.csv').exists()
    calls = []
    monkeypatch.setattr(simulation_api.SimulationRunner, 'get_run_state', lambda _simulation_id: SimulationRunState(
        state.simulation_id, runner_status=RunnerStatus.COMPLETED,
    ))
    monkeypatch.setattr(simulation_api.ZepGraphMemoryManager, 'get_updater', lambda _simulation_id: None)

    def cleanup(simulation_id):
        assert simulation_id == state.simulation_id
        calls.append('cleanup')
        return {'success': True}

    def start(**kwargs):
        assert kwargs['simulation_id'] == state.simulation_id
        assert kwargs['platform'] == 'reddit'
        assert kwargs['enable_graph_memory_update'] is False
        assert SimulationManager().get_simulation(state.simulation_id).status == SimulationStatus.READY
        calls.append('start')
        return SimulationRunState(state.simulation_id, runner_status=RunnerStatus.RUNNING)

    monkeypatch.setattr(simulation_api.SimulationRunner, 'cleanup_simulation_logs', cleanup)
    monkeypatch.setattr(simulation_api.SimulationRunner, 'start_simulation', start)
    app = Flask(__name__)
    app.register_blueprint(simulation_api.simulation_bp, url_prefix='/api/simulation')
    response = app.test_client().post('/api/simulation/start', json={
        'simulation_id': state.simulation_id, 'platform': 'reddit', 'force': True,
    })
    assert response.status_code == 200, response.get_json()
    assert response.json['data']['force_restarted'] is True
    assert response.json['data']['runner_status'] == 'running'
    assert calls == ['cleanup', 'start']
