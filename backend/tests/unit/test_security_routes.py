"""Route-level security regressions: SSRF, traversal, and LLM live-order escape."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.storage import SaveJsonRequest, save_json_file
from backend.routes.telemetry import trigger_n8n, write_influx
from backend.services.llm_tool_loop import build_trading_tools


class _Response:
    is_success = True
    status_code = 204
    text = ""
    headers = {"content-type": "application/json"}

    def json(self):
        return {"ok": True}


def _async_client(post):
    cm = AsyncMock()
    client = MagicMock()
    client.post = post
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = False
    return cm


@pytest.mark.asyncio
async def test_n8n_telemetry_ignores_caller_supplied_ssrf_url(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://trusted.example/webhook")
    post = AsyncMock(return_value=_Response())
    with patch(
        "backend.routes.telemetry.httpx.AsyncClient",
        return_value=_async_client(post),
    ):
        result = await trigger_n8n({
            "webhookUrl": "http://169.254.169.254/latest/meta-data",
            "event": "trade",
            "payload": {"x": 1},
        })

    assert result["status"] == "ok"
    assert post.call_args.args[0] == "https://trusted.example/webhook"


@pytest.mark.asyncio
async def test_influx_telemetry_uses_only_server_destination_and_token(monkeypatch):
    monkeypatch.setenv("INFLUXDB_URL", "http://influxdb:8086")
    monkeypatch.setenv("INFLUXDB_TOKEN", "server-secret")
    monkeypatch.setenv("INFLUXDB_ORG", "org")
    monkeypatch.setenv("INFLUXDB_BUCKET", "bucket")
    post = AsyncMock(return_value=_Response())
    with patch(
        "backend.routes.telemetry.httpx.AsyncClient",
        return_value=_async_client(post),
    ):
        result = await write_influx({
            "url": "https://attacker.example/steal",
            "token": "caller-token",
            "data": {"symbol": "ETHUSDT"},
        })

    assert result["status"] == "ok"
    assert post.call_args.args[0].startswith("http://influxdb:8086/")
    assert post.call_args.kwargs["headers"]["Authorization"] == "Token server-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", [
    "../../root/.ssh/authorized_keys",
    "/tmp/owned.json",
    "nested/file.json",
    "not-json.txt",
])
async def test_storage_rejects_path_traversal_and_non_json(filename):
    with pytest.raises(HTTPException) as exc:
        await save_json_file(SaveJsonRequest(filename=filename, data={"x": 1}))
    assert exc.value.status_code == 400


def test_llm_trading_tool_is_pinned_to_named_paper_session():
    ut = MagicMock()
    ut.place_order.return_value = MagicMock(
        success=True, order_id="paper-1", message="ok", mode="paper",
        filled_price=100.0, filled_qty=1.0,
    )
    tools = build_trading_tools(
        ut, paper_session_id="ai-agent-paper",
    )

    result = tools.execute("place_paper_order", {
        "symbol": "ETHUSDT",
        "side": "buy",
        "quantity": 1.0,
        "price": 100.0,
    })

    assert result.success is True
    assert ut.place_order.call_args.kwargs["session_id"] == "ai-agent-paper"

