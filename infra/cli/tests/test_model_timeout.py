"""The model predict call must carry an explicit, bounded timeout.

Without it the vendor SDK default (~600s) lets one slow/hung request stall a worker
for ten minutes. We assert the NavigatorAgent passes MODEL_CALL_TIMEOUT_S both to
the client constructor and to each create() call.
"""

from __future__ import annotations

import benchmark.agent_yutori as ay
from benchmark.config import MODEL_CALL_TIMEOUT_S


class _RecordingClient:
    """Captures the kwargs the agent uses, so no network is touched."""

    last_init_kwargs: dict = {}

    def __init__(self, **kwargs):
        _RecordingClient.last_init_kwargs = kwargs
        self.create_kwargs: dict = {}

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.create_kwargs = kwargs
                raise RuntimeError("stop-before-network")  # we only inspect kwargs

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_predict_call_is_bounded(monkeypatch):
    monkeypatch.setenv("YUTORI_API_KEY", "test-key")
    monkeypatch.setattr(ay, "OpenAI", _RecordingClient)

    agent = ay.NavigatorAgent.__new__(ay.NavigatorAgent)
    # Minimal init of just what _call_model needs (avoid filesystem/save_dir setup).
    agent.cfg = type("C", (), {"model_id": "n1.5-latest"})()
    agent.client = ay.OpenAI(base_url="x", api_key="test-key", timeout=MODEL_CALL_TIMEOUT_S)
    agent.messages = [{"role": "user", "content": "hi"}]

    # client constructor got the timeout
    assert _RecordingClient.last_init_kwargs.get("timeout") == MODEL_CALL_TIMEOUT_S

    try:
        agent._call_model()
    except RuntimeError as e:
        assert "stop-before-network" in str(e)
    else:
        raise AssertionError("expected the recording client to short-circuit")

    # each create() call also got the timeout
    assert agent.client.create_kwargs.get("timeout") == MODEL_CALL_TIMEOUT_S
