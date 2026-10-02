import pytest

from app.services.simulation_config_generator import SimulationConfigGenerator
from app.utils.budget import BudgetExceeded


@pytest.mark.parametrize('method,args', [
    ('_generate_time_config', ('context', 2)),
    ('_generate_event_config', ('context', 'requirement', [])),
    ('_generate_agent_configs_batch', ('context', [], 0, 'requirement')),
])
def test_budget_exceeded_cannot_be_replaced_with_fallback_config(monkeypatch, method, args):
    generator = SimulationConfigGenerator.__new__(SimulationConfigGenerator)
    def exceeded(*args, **kwargs):
        raise BudgetExceeded('max_calls')
    monkeypatch.setattr(generator, '_call_llm_with_retry', exceeded)
    with pytest.raises(BudgetExceeded):
        getattr(generator, method)(*args)
