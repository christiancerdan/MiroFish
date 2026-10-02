import json

import pytest

from app.services import zep_tools
from app.services.simulation_ipc import (
    CommandStatus,
    CommandType,
    IPCResponse,
    SimulationIPCClient,
    SimulationIPCServer,
)


def test_ipc_status_check_does_not_create_missing_directories(tmp_path):
    simulation_dir = tmp_path / "sim_missing"
    assert SimulationIPCClient(str(simulation_dir)).check_env_alive() is False
    assert not simulation_dir.exists()


def test_ipc_send_creates_directories_and_cleans_up_timed_out_command(tmp_path):
    simulation_dir = tmp_path / "sim_1"
    client = SimulationIPCClient(str(simulation_dir))
    with pytest.raises(TimeoutError):
        client.send_command(CommandType.CLOSE_ENV, {}, timeout=0)
    assert list((simulation_dir / "ipc_commands").iterdir()) == []
    assert list((simulation_dir / "ipc_responses").iterdir()) == []


@pytest.mark.parametrize("directory", ["ipc_commands", "ipc_responses"])
@pytest.mark.parametrize("constructor", [SimulationIPCClient, SimulationIPCServer])
def test_ipc_rejects_directory_symlinks_outside_simulation(tmp_path, directory, constructor):
    simulation_dir = tmp_path / "sim_1"
    simulation_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (simulation_dir / directory).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        constructor(str(simulation_dir))
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("operation", ["read", "write"])
def test_ipc_status_symlink_cannot_access_outside_file(tmp_path, operation):
    server = SimulationIPCServer(str(tmp_path / "sim_1"))
    outside = tmp_path / "private.json"
    outside.write_text('{"status": "alive"}', encoding="utf-8")
    (tmp_path / "sim_1" / "env_status.json").symlink_to(outside)
    with pytest.raises(ValueError):
        if operation == "read":
            SimulationIPCClient(str(tmp_path / "sim_1")).check_env_alive()
        else:
            server.start()
    assert outside.read_text(encoding="utf-8") == '{"status": "alive"}'


@pytest.mark.parametrize("command_id", ["../../outside", "a/b", r"a\b", ".", "", None])
def test_ipc_response_rejects_command_identifier_traversal(tmp_path, command_id):
    server = SimulationIPCServer(str(tmp_path / "sim_1"))
    with pytest.raises(ValueError):
        server.send_response(IPCResponse(command_id, CommandStatus.COMPLETED))
    assert list((tmp_path / "sim_1" / "ipc_responses").iterdir()) == []


def test_ipc_poll_rejects_command_symlink(tmp_path):
    server = SimulationIPCServer(str(tmp_path / "sim_1"))
    outside = tmp_path / "private.json"
    outside.write_text('{"command_id": "cmd_1", "command_type": "close_env"}', encoding="utf-8")
    (tmp_path / "sim_1" / "ipc_commands" / "cmd_1.json").symlink_to(outside)
    with pytest.raises(ValueError):
        server.poll_commands()


def test_ipc_poll_rejects_command_with_traversal_in_payload(tmp_path):
    server = SimulationIPCServer(str(tmp_path / "sim_1"))
    (tmp_path / "sim_1" / "ipc_commands" / "cmd_1.json").write_text(
        '{"command_id": "../../outside", "command_type": "close_env"}', encoding="utf-8"
    )
    with pytest.raises(ValueError):
        server.poll_commands()


def test_ipc_response_cannot_overwrite_symlink_target(tmp_path):
    server = SimulationIPCServer(str(tmp_path / "sim_1"))
    outside = tmp_path / "private.json"
    outside.write_text("private", encoding="utf-8")
    (tmp_path / "sim_1" / "ipc_responses" / "cmd_1.json").symlink_to(outside)
    with pytest.raises(ValueError):
        server.send_response(IPCResponse("cmd_1", CommandStatus.COMPLETED))
    assert outside.read_text(encoding="utf-8") == "private"


def test_valid_ipc_commands_and_responses_round_trip(tmp_path):
    simulation_dir = tmp_path / "sim_1"
    server = SimulationIPCServer(str(simulation_dir))
    server.start()
    client = SimulationIPCClient(str(simulation_dir))
    assert client.check_env_alive() is True
    command_file = simulation_dir / "ipc_commands" / "cmd_1.json"
    command_file.write_text('{"command_id": "cmd_1", "command_type": "interview", "args": {"agent_id": 2}}')
    command = server.poll_commands()
    assert command.command_type == CommandType.INTERVIEW
    assert command.args == {"agent_id": 2}
    server.send_success(command.command_id, {"answer": "hello"})
    assert json.loads((simulation_dir / "ipc_responses" / "cmd_1.json").read_text())["result"] == {"answer": "hello"}
    assert not command_file.exists()


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(zep_tools, "__file__", str(tmp_path / "app" / "services" / "zep_tools.py"))
    return tmp_path / "uploads" / "simulations"


@pytest.mark.parametrize("simulation_id", ["../outside", "a/b", r"a\b", ".", "", None])
def test_profile_loading_rejects_malformed_simulation_id(profiles, simulation_id):
    service = object.__new__(zep_tools.ZepToolsService)
    with pytest.raises(ValueError):
        service._load_agent_profiles(simulation_id)
    assert not profiles.exists()


@pytest.mark.parametrize("filename", ["reddit_profiles.json", "twitter_profiles.csv"])
def test_profile_loading_rejects_symlinked_profile_file(profiles, tmp_path, filename):
    folder = profiles / "sim_1"
    folder.mkdir(parents=True)
    outside = tmp_path / "private.txt"
    outside.write_text("[]", encoding="utf-8")
    (folder / filename).symlink_to(outside)
    service = object.__new__(zep_tools.ZepToolsService)
    with pytest.raises(ValueError):
        service._load_agent_profiles("sim_1")
