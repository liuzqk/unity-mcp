import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from transport.models import CommandReceiptMessage, CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest_asyncio.fixture
async def receipt_hub():
    registry = PluginRegistry()
    PluginHub.configure(registry, asyncio.get_running_loop())
    yield registry
    PluginHub._connections.clear()
    PluginHub._pending.clear()
    PluginHub._receipts.clear()
    PluginHub._registry = None
    PluginHub._lock = None


async def _session(registry: PluginRegistry, session_id: str = "session"):
    websocket = AsyncMock()
    await registry.register(
        session_id,
        "Project",
        "project-hash",
        "2022.3.62f3",
        receipt_protocol=PluginHub.RECEIPT_PROTOCOL_VERSION,
    )
    PluginHub._connections[session_id] = websocket
    return websocket


def _hub() -> PluginHub:
    return object.__new__(PluginHub)


@pytest.mark.asyncio
async def test_durable_command_returns_cached_result_without_reexecution(receipt_hub):
    websocket = await _session(receipt_hub)
    params = {"value": 1}
    digest = PluginHub.command_envelope_sha256("probe", params)

    pending = asyncio.create_task(
        PluginHub.send_command(
            "session",
            "probe",
            params,
            command_id="command-one",
            envelope_sha256=digest,
            project_hash="project-hash",
        )
    )
    await asyncio.sleep(0)
    await _hub()._handle_command_result(
        websocket,
        CommandResultMessage(id="command-one", result={"success": True, "count": 1}),
    )

    assert await pending == {"success": True, "count": 1}
    cached = await PluginHub.send_command(
        "session",
        "probe",
        params,
        command_id="command-one",
        envelope_sha256=digest,
        project_hash="project-hash",
    )
    assert cached == {"success": True, "count": 1}
    execute_messages = [
        call.args[0]
        for call in websocket.send_json.await_args_list
        if call.args[0].get("type") == "execute"
    ]
    assert len(execute_messages) == 1


@pytest.mark.asyncio
async def test_completed_receipt_recovers_disconnected_command(receipt_hub):
    old_websocket = await _session(receipt_hub, "old-session")
    params = {"value": 2}
    digest = PluginHub.command_envelope_sha256("probe", params)
    pending = asyncio.create_task(
        PluginHub.send_command(
            "old-session",
            "probe",
            params,
            command_id="command-two",
            envelope_sha256=digest,
            project_hash="project-hash",
        )
    )
    await asyncio.sleep(0)
    await _hub().on_disconnect(old_websocket, 1006)

    new_websocket = await _session(receipt_hub, "new-session")
    await _hub()._handle_command_receipt(
        new_websocket,
        CommandReceiptMessage(
            id="command-two",
            project_hash="project-hash",
            name="probe",
            envelope_sha256=digest,
            state="completed",
            result={"success": True, "count": 1},
        ),
    )

    assert await pending == {"success": True, "count": 1}
    status = await PluginHub.command_receipt_status("command-two", "project-hash")
    assert status["state"] == "completed"


@pytest.mark.asyncio
async def test_started_receipt_is_ambiguous_and_never_replayed(receipt_hub):
    websocket = await _session(receipt_hub)
    params = {"value": 3}
    digest = PluginHub.command_envelope_sha256("probe", params)

    await _hub()._handle_command_receipt(
        websocket,
        CommandReceiptMessage(
            id="command-three",
            project_hash="project-hash",
            name="probe",
            envelope_sha256=digest,
            state="started",
        ),
    )
    result = await PluginHub.send_command(
        "session",
        "probe",
        params,
        command_id="command-three",
        envelope_sha256=digest,
        project_hash="project-hash",
    )

    assert result["receipt_state"] == "ambiguous"
    assert not any(
        call.args[0].get("type") == "execute"
        for call in websocket.send_json.await_args_list
    )


@pytest.mark.asyncio
async def test_receipt_ack_removes_server_state_and_notifies_plugin(receipt_hub):
    websocket = await _session(receipt_hub)
    PluginHub._receipts["command-four"] = {
        "state": "completed",
        "project_hash": "project-hash",
        "command_type": "probe",
        "envelope_sha256": "a" * 64,
        "result": {"success": True},
        "updated_at": 1e20,
    }

    assert await PluginHub.ack_command_receipt("command-four", "project-hash")
    assert "command-four" not in PluginHub._receipts
    websocket.send_json.assert_awaited_with(
        {"type": "command_receipt_ack", "id": "command-four"}
    )


@pytest.mark.asyncio
async def test_receipt_ack_preserves_state_while_plugin_is_disconnected(receipt_hub):
    PluginHub._receipts["command-five"] = {
        "state": "completed",
        "project_hash": "project-hash",
        "command_type": "probe",
        "envelope_sha256": "b" * 64,
        "result": {"success": True},
        "updated_at": 1e20,
    }

    assert not await PluginHub.ack_command_receipt("command-five", "project-hash")
    assert "command-five" in PluginHub._receipts


@pytest.mark.asyncio
async def test_unsolicited_receipt_respects_capacity(receipt_hub, monkeypatch):
    websocket = await _session(receipt_hub)
    monkeypatch.setattr(PluginHub, "MAX_RECEIPTS", 1)
    PluginHub._receipts["existing"] = {
        "state": "completed",
        "project_hash": "project-hash",
        "command_type": "probe",
        "envelope_sha256": "c" * 64,
        "result": {"success": True},
        "updated_at": 1e20,
    }

    await _hub()._handle_command_receipt(
        websocket,
        CommandReceiptMessage(
            id="command-six",
            project_hash="project-hash",
            name="probe",
            envelope_sha256="d" * 64,
            state="completed",
            result={"success": True},
        ),
    )

    assert "command-six" not in PluginHub._receipts


@pytest.mark.asyncio
async def test_expired_pending_command_becomes_ambiguous(receipt_hub):
    future = asyncio.get_running_loop().create_future()
    PluginHub._pending["command-seven"] = {
        "future": future,
        "session_id": None,
        "command_type": "probe",
        "project_hash": "project-hash",
        "envelope_sha256": "e" * 64,
        "durable": True,
        "needs_resend": True,
        "created_at": 0,
    }

    status = await PluginHub.command_receipt_status("command-seven", "project-hash")

    assert status["state"] == "ambiguous"
    assert "command-seven" not in PluginHub._pending
    assert (await future)["receipt_state"] == "ambiguous"


@pytest.mark.asyncio
async def test_server_operation_deduplicates_and_caches_final_result(receipt_hub):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"success": True, "count": calls}

    params = {"tool_name": "custom", "parameters": {"value": 1}}
    digest = PluginHub.command_envelope_sha256("execute_custom_tool", params)
    first = asyncio.create_task(
        PluginHub.run_durable_operation(
            "command-eight",
            "execute_custom_tool",
            params,
            "project-hash",
            operation,
            envelope_sha256=digest,
        )
    )
    await started.wait()
    second = asyncio.create_task(
        PluginHub.run_durable_operation(
            "command-eight",
            "execute_custom_tool",
            params,
            "project-hash",
            operation,
            envelope_sha256=digest,
        )
    )
    release.set()

    assert await first == {"success": True, "count": 1}
    assert await second == {"success": True, "count": 1}
    assert calls == 1
    cached = await PluginHub.run_durable_operation(
        "command-eight",
        "execute_custom_tool",
        params,
        "project-hash",
        operation,
        envelope_sha256=digest,
    )
    assert cached == {"success": True, "count": 1}
    assert calls == 1


@pytest.mark.asyncio
async def test_server_operation_ack_cascades_to_child_receipts(receipt_hub):
    websocket = await _session(receipt_hub)
    PluginHub._receipts["command-child"] = {
        "state": "completed",
        "project_hash": "project-hash",
        "command_type": "custom",
        "envelope_sha256": "f" * 64,
        "result": {"success": True},
        "updated_at": 1e20,
    }
    PluginHub._receipts["command-parent"] = {
        "state": "completed",
        "project_hash": "project-hash",
        "command_type": "execute_custom_tool",
        "envelope_sha256": "0" * 64,
        "result": {"success": True},
        "server_only": True,
        "children": ["command-child"],
        "updated_at": 1e20,
    }

    assert await PluginHub.ack_command_receipt("command-parent", "project-hash")
    assert "command-parent" not in PluginHub._receipts
    assert "command-child" not in PluginHub._receipts
    websocket.send_json.assert_awaited_once_with(
        {"type": "command_receipt_ack", "id": "command-child"}
    )
