"""Keep durable task fixtures isolated from local application data."""
import pytest


@pytest.fixture(autouse=True)
def isolated_task_store(tmp_path, monkeypatch):
    from app.config import Config
    from app.models.task import TaskManager
    monkeypatch.setattr(Config, 'JOBS_DB_PATH', str(tmp_path / 'jobs.sqlite3'))
    monkeypatch.setattr(Config, 'JOBS_AUTOSTART', False)
    monkeypatch.setattr(Config, 'BUDGET_DB_PATH', str(tmp_path / 'budgets.sqlite3'))
    TaskManager._instance = None
    yield
    TaskManager._instance = None
