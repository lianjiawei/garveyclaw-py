from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from weclaw.channels.telegram import bot as telegram_bot
from weclaw.channels.telegram.bot import schedule_in
from weclaw.cluster.coordinator import build_cluster_blueprint
from weclaw.cluster.orchestrator import run_cluster_tasks_serial
from weclaw.cluster.store import load_cluster_runtime_state
from weclaw.core.response import AgentReply
from weclaw.core.types import ConversationRef
from weclaw.decision.models import CapabilityContinuation, DecisionPlan, TaskIntent, TaskLineState
from weclaw.tasks.service import TaskCommandResult


class _RecordingMessage:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def reply_text(self, text: str, *_args, **_kwargs) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_telegram_schedule_in_uses_shared_task_command(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_handle_task_command(conversation, text: str) -> TaskCommandResult:
        seen["conversation"] = conversation
        seen["text"] = text
        return TaskCommandResult(True, "scheduled ok")

    monkeypatch.setattr(telegram_bot, "is_owner", lambda _update: True)
    monkeypatch.setattr(telegram_bot, "handle_task_command", fake_handle_task_command)

    message = _RecordingMessage()
    update = SimpleNamespace(
        message=message,
        effective_chat=SimpleNamespace(id=123),
        effective_user=SimpleNamespace(id=456),
    )
    context = SimpleNamespace(args=["60", "drink water"])

    await schedule_in(update, context)

    conversation = seen["conversation"]
    assert seen["text"] == "/schedule_in 60 drink water"
    assert conversation.channel == "telegram"
    assert conversation.target_id == "123"
    assert message.sent == ["scheduled ok"]


def _make_file_task_plan() -> DecisionPlan:
    return DecisionPlan(
        task_intent=TaskIntent(
            intent_type="file_task",
            goal="check project",
            request_style="execute",
            target="workspace",
            expected_output="report",
            confidence=0.82,
        ),
        task_line=TaskLineState(),
        intent_type="file_task",
        strategy="prefer_tools",
        continuation=CapabilityContinuation(),
        summary="Use tools and review the result.",
    )


@pytest.mark.asyncio
async def test_cluster_implicit_reviewer_changes_requested_drives_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="weclaw_public_beta_cluster_test_"))
    monkeypatch.setattr("weclaw.cluster.store.CLUSTER_RUNTIME_FILE", temp_dir / "cluster_runtime.json")

    from weclaw.cluster.store import start_cluster_run

    class Sender:
        async def send_text(self, target_id: str, text: str) -> None:
            pass

        async def send_file(self, target_id: str, file_data: bytes, file_name: str) -> None:
            pass

    attempts = {"executor": 0, "reviewer": 0}

    async def fake_runner(_prompt, spec, task, _context, _sender):
        if spec.name == "executor":
            attempts["executor"] += 1
            return AgentReply.from_text("executor produced a non-empty result", provider="fake")
        if spec.name == "reviewer":
            attempts["reviewer"] += 1
            return AgentReply.from_text("changes requested: missing verification", provider="fake")
        return AgentReply.from_text(f"{spec.name} ok {task.task_id}", provider="fake")

    conversation = ConversationRef(channel="tui", target_id="cluster-review-text-retry", session_scope="tui:cluster-review-text-retry")
    blueprint = build_cluster_blueprint(_make_file_task_plan())
    start_cluster_run(conversation, blueprint)

    result = await run_cluster_tasks_serial(conversation, blueprint, Sender(), runner=fake_runner)

    assert result.success is False
    assert attempts == {"executor": 2, "reviewer": 2}
    run = load_cluster_runtime_state()["runs"][blueprint.cluster_id]
    executor_tasks = [task for task in run["tasks"] if task["assigned_agent"] == "executor"]
    assert executor_tasks[0]["review_outcome"] == "rejected"
    assert executor_tasks[0]["attempt_count"] == 2
    assert run["state"] == "error"
