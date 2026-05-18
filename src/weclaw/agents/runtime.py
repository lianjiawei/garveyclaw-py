from __future__ import annotations

from typing import Any

import weclaw.config as config
from weclaw.agents.router import run_agent
from weclaw.cluster.coordinator import (

    build_cluster_blueprint,

    cluster_enabled_for_plan,

    finish_cluster_run,

    mark_executor_finished,

    mark_executor_started,

    mark_executor_waiting,

    mark_reviewer_finished,

    start_cluster_run,

)

from weclaw.core.agent_activity import mark_agent_run_finished, mark_agent_run_started, mark_agent_waiting, reply_requires_waiting

from weclaw.core.response import AgentReply

from weclaw.core.delivery import MessageSender

from weclaw.core.types import ConversationRef





async def run_agent_for_conversation(

    prompt: str,

    conversation: ConversationRef,

    sender: MessageSender,

    continue_session: bool = True,

    record_text: str | None = None,

    uploaded_image: Any | None = None,

    uploaded_file: Any | None = None,

) -> AgentReply:

    from weclaw.capabilities.tools import ToolContext

    from weclaw.core.provider_state import get_provider

    from weclaw.decision.candidates import persist_workflow_candidate

    from weclaw.decision.models import ExecutionOutcome

    from weclaw.decision.router import build_decision_plan

    from weclaw.decision.store import append_execution_outcome, save_session_capability_preferences, save_session_task_line, save_session_user_constraints

    from weclaw.decision.trace import clear_execution_trace, get_execution_trace_details, get_execution_trace_snapshot, start_execution_trace

    from weclaw.decision.workflow_gate import attempt_workflow_first



    mark_agent_run_started(conversation, record_text or prompt)

    start_execution_trace(conversation.session_scope, conversation.channel, record_text or prompt)

    requested_provider = get_provider()

    decision_plan = await build_decision_plan(

        prompt=prompt,

        provider=requested_provider,

        session_scope=conversation.session_scope,

        channel=conversation.channel,

    )

    cluster_blueprint = (
        build_cluster_blueprint(decision_plan)
        if config.AGENT_CLUSTER_ORCHESTRATOR_ENABLED and cluster_enabled_for_plan(decision_plan)
        else None
    )
    workflow_ctx = ToolContext(

        sender=sender,

        target_id=conversation.target_id,

        uploaded_image=uploaded_image,

        channel=conversation.channel,

        session_scope=conversation.session_scope,

        enforce_confirmations=hasattr(sender, "confirm_tool_use"),

    )

    wf_result = await attempt_workflow_first(decision_plan, workflow_ctx)



    def _infer_stage(strategy: str, waiting: bool, workflow_first: bool) -> str:

        if waiting:

            return "waiting_user"

        if workflow_first:

            return "workflow_execution"

        if strategy == "answer_directly":

            return "answering"

        if strategy == "prefer_skill":

            return "skill_guided"

        if strategy == "prefer_tools":

            return "tool_execution"

        return "active"



    def _save_task_line(stage: str) -> None:

        active_subtask = decision_plan.task_intent.target or decision_plan.task_intent.goal

        primary_goal = decision_plan.task_line.primary_goal or decision_plan.task_intent.goal

        save_session_task_line(

            conversation.session_scope,

            primary_goal=primary_goal,

            active_subtask=active_subtask,

            stage=stage,

            carried_constraints=decision_plan.task_intent.constraints,

        )



    def _build_outcome(

        success: bool,

        waiting_for_user: bool,

        reply_text: str,

        error_summary: str = "",

    ) -> ExecutionOutcome:

        trace_snapshot = get_execution_trace_snapshot(conversation.session_scope)

        used_workflows_default = decision_plan.selected_workflows

        if wf_result.succeeded:

            used_workflows_default = (wf_result.workflow_name,)

        return ExecutionOutcome(

            strategy=decision_plan.strategy,

            success=success,

            waiting_for_user=waiting_for_user,

            workflow_first_attempted=wf_result.attempted,

            workflow_first_succeeded=wf_result.succeeded,

            workflow_first_fallback=wf_result.fallback,

            workflow_first_name=wf_result.workflow_name,

            workflow_first_reason=wf_result.reason,

            used_skills=decision_plan.selected_skills,

            used_workflows=trace_snapshot.get("used_workflows") or used_workflows_default,

            used_tools=trace_snapshot.get("used_tools", ()),

            tool_sequence=trace_snapshot.get("tool_sequence", ()),

            touched_files=trace_snapshot.get("touched_files", ()),

            error_summary=error_summary,

            reply_text=reply_text,

        )



    async def _persist_run(

        provider: str,

        outcome: ExecutionOutcome,

        persist_candidate: bool,

    ) -> None:

        append_execution_outcome(

            session_scope=conversation.session_scope,

            channel=conversation.channel,

            provider=provider,

            prompt=record_text or prompt,

            plan=decision_plan,

            outcome=outcome,

        )

        save_session_capability_preferences(

            conversation.session_scope,

            strategy=decision_plan.strategy,

            goal=decision_plan.task_intent.goal,

            intent_type=decision_plan.intent_type,

            used_skills=decision_plan.selected_skills,

            used_workflows=outcome.used_workflows,

            used_tools=outcome.used_tools,

            success=outcome.success,

        )

        save_session_user_constraints(conversation.session_scope, decision_plan.task_intent.constraints)

        if persist_candidate:

            trace_details = get_execution_trace_details(conversation.session_scope)

            persist_workflow_candidate(

                session_scope=conversation.session_scope,

                provider=provider,

                prompt=record_text or prompt,

                plan=decision_plan,

                outcome=outcome,

                trace_details=trace_details,

            )

        clear_execution_trace(conversation.session_scope)



    if wf_result.succeeded:
        reply = AgentReply.from_text(wf_result.output_text, provider=requested_provider)
        outcome = _build_outcome(True, False, reply.text)

        await _persist_run(requested_provider, outcome, True)

        _save_task_line(_infer_stage(decision_plan.strategy, False, True))

        if cluster_blueprint is not None:

            mark_executor_started(conversation, cluster_blueprint, "执行 workflow")

            mark_executor_finished(conversation, cluster_blueprint, wf_result.output_text[:160] or "workflow 执行完成")

            mark_reviewer_finished(conversation, cluster_blueprint, "Workflow path completed successfully")

            finish_cluster_run(conversation, cluster_blueprint, True, wf_result.output_text[:180])

        mark_agent_run_finished(conversation)
        return reply

    if cluster_blueprint is not None and config.AGENT_CLUSTER_ORCHESTRATOR_ENABLED:
        from weclaw.cluster.orchestrator import (
            run_cluster_tasks_serial,
            run_cluster_with_dynamic_planner,
        )
        from weclaw.cluster.response import render_cluster_orchestration_reply, render_cluster_user_reply

        start_cluster_run(conversation, cluster_blueprint, decision_plan)
        if config.AGENT_CLUSTER_DYNAMIC_PLANNER_ENABLED:
            orchestration = await run_cluster_with_dynamic_planner(
                conversation,
                cluster_blueprint,
                sender,
                user_prompt=record_text or prompt,
            )
        else:
            orchestration = await run_cluster_tasks_serial(conversation, cluster_blueprint, sender)
        if conversation.channel in {"feishu", "telegram"}:
            reply_text = render_cluster_user_reply(orchestration)
        else:
            reply_text = render_cluster_orchestration_reply(orchestration)
        reply = AgentReply.from_text(reply_text, provider="cluster")
        outcome = _build_outcome(orchestration.success, False, reply.text, orchestration.error)
        await _persist_run("cluster", outcome, orchestration.success)
        _save_task_line("cluster_orchestration" if orchestration.success else "blocked_error")
        finish_cluster_run(conversation, cluster_blueprint, orchestration.success, reply.text[:180] or orchestration.error)
        mark_agent_run_finished(conversation, None if orchestration.success else orchestration.error)
        return reply

    try:
        if cluster_blueprint is not None:

            mark_executor_started(conversation, cluster_blueprint, decision_plan.summary or decision_plan.task_intent.goal)

        reply = await run_agent(

            prompt=prompt, sender=sender, target_id=conversation.target_id,

            continue_session=continue_session, record_text=record_text,

            uploaded_image=uploaded_image, uploaded_file=uploaded_file,

            session_scope=conversation.session_scope, channel=conversation.channel,

            decision_plan=decision_plan,

        )

    except Exception as exc:

        outcome = _build_outcome(False, False, "", str(exc) or exc.__class__.__name__)

        await _persist_run(requested_provider, outcome, False)

        _save_task_line("blocked_error")

        if cluster_blueprint is not None:

            mark_executor_finished(conversation, cluster_blueprint, f"error: {str(exc) or exc.__class__.__name__}"[:160])

            finish_cluster_run(conversation, cluster_blueprint, False, str(exc) or exc.__class__.__name__)

        mark_agent_run_finished(conversation, str(exc) or exc.__class__.__name__)

        raise



    effective_provider = reply.provider or requested_provider



    if reply_requires_waiting(reply.text):

        outcome = _build_outcome(True, True, reply.text)

        await _persist_run(effective_provider, outcome, False)

        _save_task_line(_infer_stage(decision_plan.strategy, True, wf_result.succeeded))

        if cluster_blueprint is not None:

            mark_executor_waiting(conversation, cluster_blueprint, reply.text)

        mark_agent_waiting(conversation, reply.text)

        return reply



    outcome = _build_outcome(True, False, reply.text)

    await _persist_run(effective_provider, outcome, True)

    _save_task_line(_infer_stage(decision_plan.strategy, False, wf_result.succeeded))

    if cluster_blueprint is not None:

        mark_executor_finished(conversation, cluster_blueprint, reply.text[:160] or "execution completed")

        reviewer_summary = "Reviewed final response"

        if outcome.used_tools:

            reviewer_summary = f"Reviewed tool path: {', '.join(outcome.used_tools[:3])}"

        elif outcome.used_workflows:

            reviewer_summary = f"Reviewed workflow path: {', '.join(outcome.used_workflows[:3])}"

        mark_reviewer_finished(conversation, cluster_blueprint, reviewer_summary)

        finish_cluster_run(conversation, cluster_blueprint, True, reply.text[:180])

    mark_agent_run_finished(conversation)

    return reply
