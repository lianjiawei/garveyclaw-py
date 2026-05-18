# Multi-Agent SPEC

## Target

WeClaw should evolve into a flexible Agent cluster platform:

- users can run one Agent directly
- users can define custom Agents
- multiple Agents can collaborate on one user goal
- the dashboard can observe Agent execution, tasks, messages, and status changes

## Phase 1: AgentSpec Foundation

### Scope

Introduce a first-class `AgentSpec` definition layer without changing the current single-Agent runtime behavior.

### Acceptance Criteria

- built-in `planner`, `executor`, and `reviewer` Agent specs are available
- external JSON Agent definitions can be loaded from `AGENT_DEFINITIONS_DIR`
- user definitions can override built-in specs by name
- cluster blueprints are built from Agent specs instead of hardcoded display metadata
- tests cover loading, validation, override behavior, and cluster blueprint integration

### Out Of Scope

- independent planner/reviewer model calls
- task DAG scheduling
- multi-executor parallel execution
- user-facing Agent CRUD commands

## Future Phases

## Phase 2: Agent Task Runtime

### Scope

Add a minimal execution entrypoint that can run one assigned task as one `AgentSpec`.

### Acceptance Criteria

- task execution has explicit `AgentTask`, `AgentTaskContext`, and `AgentTaskResult` models
- `run_agent_task(agent_spec, task, context, sender)` renders an Agent-scoped prompt
- the default runner can call the existing provider router without re-entering the full conversation runtime
- tests can inject a fake runner so the contract is verified without real model calls
- cluster tasks can be adapted into Agent tasks

### Out Of Scope

- replacing the existing cluster wrapper in `agents/runtime.py`
- planner-generated task DAG
- parallel task scheduling
- per-Agent provider override enforcement

## Future Phases

## Phase 3: Cluster Orchestrator Skeleton

### Scope

Add a serial orchestrator that can execute blueprint tasks as real Agent task calls.

### Acceptance Criteria

- blueprint planned steps can be converted into precise `ClusterTask` records
- orchestrator resolves each task's assigned Agent to an `AgentSpec`
- orchestrator calls `run_agent_task()` for each task in order
- cluster store can update task state by exact `task_id`
- each task result is recorded as cluster task output and message
- execution stops on first failed task with a structured orchestration result

### Out Of Scope

- replacing the current main runtime cluster wrapper
- planner-generated dynamic DAG
- parallel execution

## Future Phases

## Phase 4: Optional Runtime Integration

### Scope

Connect the serial cluster orchestrator to the main conversation runtime behind a feature flag.

### Acceptance Criteria

- default behavior is unchanged
- `AGENT_CLUSTER_ORCHESTRATOR_ENABLED=1` enables real serial Agent task execution when cluster is active
- the runtime returns a normal `AgentReply` summarizing orchestration output
- execution outcome and cluster completion are persisted through the existing runtime lifecycle

### Out Of Scope

- enabling the orchestrator by default
- dynamic planner-generated DAG
- parallel execution

## Future Phases

## Phase 5: Planner-Generated Task DAG

### Scope

Introduce a structured task-plan contract that planner Agents can produce and the cluster runtime can validate.

### Planner JSON Contract

```json
{
  "objective": "optional high-level objective",
  "tasks": [
    {
      "id": "research",
      "title": "Research current options",
      "agent": "researcher",
      "depends_on": [],
      "input": "optional task input",
      "expected_output": "optional expected output"
    }
  ]
}
```

### Acceptance Criteria

- planner task plans can be parsed from raw JSON or fenced JSON
- invalid plans are rejected with clear errors
- task ids must be unique
- dependencies must point to defined tasks
- assigned agents must exist in the cluster blueprint
- planner tasks can be converted into `ClusterTask` records
- the serial orchestrator can accept precomputed `ClusterTask` records

### Out Of Scope

- asking the planner model to generate this JSON automatically
- topological sorting and parallel execution

## Future Phases

## Phase 6: First Runnable Dynamic Cluster

### Scope

Allow the runtime to call the planner Agent first, parse its JSON task plan, replace static cluster tasks, and then execute the resulting tasks.

### Acceptance Criteria

- `AGENT_CLUSTER_DYNAMIC_PLANNER_ENABLED=1` enables planner-generated DAG when the cluster orchestrator is enabled
- planner is asked to return only the Planner JSON Contract
- planner output is parsed and topologically ordered
- dependency cycles are rejected
- parsed tasks replace the static blueprint task list in the runtime store
- task execution continues through the existing serial orchestrator

### Out Of Scope

- parallel execution
- per-Agent tool allowlist enforcement
- automatic recovery from invalid planner JSON beyond surfacing a clear failure

## Future Phases

1. Agent-to-agent message protocol
2. User-defined Agent CRUD and discovery UX
3. Parallel task execution with per-Agent tool and memory scopes
4. Dashboard projection for Agent tasks, messages, and live runtime state
