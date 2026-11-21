"""Hermes integration remains the supported AIAgent registry/conversation path."""
import json
import sys
from types import ModuleType, SimpleNamespace

from app import domain, hermes_runtime


def test_registered_hermes_tools_emit_observations_without_replacing_agent(monkeypatch):
    registry = SimpleNamespace(handlers={})
    def register(*, name, toolset, schema, handler, check_fn):
        assert toolset == hermes_runtime.TOOLSET
        assert schema['name'] == name
        assert check_fn()
        registry.handlers[name] = handler
    registry.register = register

    class FakeAgent:
        def __init__(self, **kwargs):
            assert kwargs['enabled_toolsets'] == [hermes_runtime.TOOLSET]
            assert kwargs['skip_memory'] is True
            assert kwargs['save_trajectories'] is False
            self.valid_tool_names = list(registry.handlers)
        def run_conversation(self, message, conversation_history):
            assert conversation_history == []
            output = json.loads(registry.handlers['update_lead_profile'](
                {'property_type': 'House', 'min_bedrooms': 2}))
            assert output['ok'] is True
            return {'final_response': 'I can help you find a two-bedroom house.',
                    'messages': [{'role':'assistant','content':'I can help you find a two-bedroom house.'}]}

    tools_module = ModuleType('tools')
    tools_module.__path__ = []
    registry_module = ModuleType('tools.registry')
    registry_module.registry = registry
    agent_module = ModuleType('run_agent')
    agent_module.AIAgent = FakeAgent
    definitions = ModuleType('model_tools')
    definitions.get_tool_definitions = lambda *a, **kw: []
    monkeypatch.setitem(sys.modules, 'tools', tools_module)
    monkeypatch.setitem(sys.modules, 'tools.registry', registry_module)
    monkeypatch.setitem(sys.modules, 'run_agent', agent_module)
    monkeypatch.setitem(sys.modules, 'model_tools', definitions)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key-not-real')

    state = domain.blank_state()
    domain.begin_turn(state, "I'm looking for a 2-bedroom house")
    notifications = []
    outcome = hermes_runtime.chat("I'm looking for a 2-bedroom house", [], state,
                                  on_tool_state=notifications.append)
    assert outcome['reply'].startswith('I can help')
    assert len(registry.handlers) == 9
    assert notifications and notifications[0]['tool'] == 'update_lead_profile'
    assert notifications[0]['profile']['lead']['min_bedrooms'] == 2
    assert state['tool_observations'][-1]['message_id'] == state['_active_source_message_id']
