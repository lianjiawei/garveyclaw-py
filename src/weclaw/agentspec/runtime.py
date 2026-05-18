from __future__ import annotations



from typing import Awaitable, Callable



from weclaw.agentspec.models import AgentSpec, AgentTask, AgentTaskContext, AgentTaskResult

from weclaw.cluster.models import ClusterTask

from weclaw.core.delivery import MessageSender

from weclaw.core.response import AgentReply

from weclaw.core.types import ConversationRef



AgentTaskRunner = Callable[[str, AgentSpec, AgentTask, AgentTaskContext, MessageSender], Awaitable[AgentReply]]





def build_agent_task_prompt(spec: AgentSpec, task: AgentTask, context: AgentTaskContext) -> str:

    sections = [

        f"# Agent: {spec.title}",

        f"Role: {spec.role}",

        "",

        "## System Prompt",

        spec.system_prompt,

        "",

        "## Task",

        f"Task ID: {task.task_id}",

        f"Title: {task.title}",

        f"Objective: {task.objective}",

    ]

    if task.expected_output:

        sections.extend(["", "## Expected Output", task.expected_output])

    if task.input_payload:

        sections.extend(["", "## Task Input", task.input_payload])

    if task.depends_on:

        sections.extend(["", "## Dependencies", "\n".join(f"- {item}" for item in task.depends_on)])

    if context.shared_context:

        sections.extend(["", "## Shared Context", context.shared_context])

    if spec.allowed_tools:

        sections.extend(["", "## Tool Scope", ", ".join(spec.allowed_tools)])

    if spec.allowed_workflows:

        sections.extend(["", "## Workflow Scope", ", ".join(spec.allowed_workflows)])

    if spec.allowed_skills:

        sections.extend(["", "## Skill Scope", ", ".join(spec.allowed_skills)])

    sections.extend(

        [

            "",

            "## Output Contract",

            "Return the final result for this assigned task. If you are blocked, explain the blocker and the exact information needed.",

        ]

    )

    return "\n".join(sections).strip()





def agent_task_from_cluster_task(task: ClusterTask) -> AgentTask:

    return AgentTask(

        task_id=task.task_id,

        title=task.title,

        objective=task.title,

        input_payload=task.input_payload,

        depends_on=task.depends_on,

        expected_output=task.output_payload,

    )





def agent_task_context_from_conversation(conversation: ConversationRef, *, cluster_id: str = "", shared_context: str = "") -> AgentTaskContext:

    return AgentTaskContext(

        cluster_id=cluster_id,

        session_scope=conversation.session_scope,

        channel=conversation.channel,

        target_id=conversation.target_id,

        conversation_key=conversation.conversation_key,

        shared_context=shared_context,

    )





async def default_agent_task_runner(

    prompt: str,

    _spec: AgentSpec,

    _task: AgentTask,

    context: AgentTaskContext,

    sender: MessageSender,

) -> AgentReply:

    from weclaw.agents.router import run_agent



    base_scope = context.session_scope or context.conversation_key or "agent-task"

    task_scope = f"{base_scope}:cluster:{context.cluster_id}:{_spec.name}:{_task.task_id}" if context.cluster_id else base_scope

    return await run_agent(

        prompt=prompt,

        sender=sender,

        target_id=context.target_id or context.conversation_key or "agent-task",

        continue_session=True,

        record_text=f"[AgentTask:{_spec.name}] {_task.title}",

        session_scope=task_scope,

        channel=context.channel,

    )





async def run_agent_task(

    spec: AgentSpec,

    task: AgentTask,

    context: AgentTaskContext,

    sender: MessageSender,

    *,

    runner: AgentTaskRunner | None = None,

) -> AgentTaskResult:

    prompt = build_agent_task_prompt(spec, task, context)

    active_runner = runner or default_agent_task_runner

    try:

        reply = await active_runner(prompt, spec, task, context, sender)

    except Exception as exc:

        return AgentTaskResult(

            agent_name=spec.name,

            task_id=task.task_id,

            text="",

            success=False,

            error=str(exc) or exc.__class__.__name__,

        )

    return AgentTaskResult(

        agent_name=spec.name,

        task_id=task.task_id,

        text=reply.text,

        provider=reply.provider,

        success=True,

    )
