# Tool System Architecture

## Goal

Provide one durable architecture for WeClaw tool capabilities so Claude MCP, OpenAI function calling, future workflows, and user-defined tools can extend from the same source of truth.

## Current Target State

### 1. Shared Registry
- All tool capabilities are registered in `src/weclaw/capabilities/tools.py`.
- Capability source files now live outside runtime code:
  - workflow definitions in `capabilities/workflows/`
  - future external tool definitions in `capabilities/tools/`
- Each tool has one metadata record:
  - `name`
  - `description`
  - `parameters`
  - `providers`
  - `risk_level`
  - `summary_builder`
  - `availability`
  - `handler`

### 2. Shared Execution Model
- All tool execution flows through:
  - `ToolContext`
  - `ToolResult`
  - `execute_tool()`
- Runtime confirmation also flows through the shared layer:
  - `ConfirmationPolicy`
  - `ToolConfirmationRequest`
  - channel-provided `confirm_tool_use()` handlers
- Provider adapters should not own business logic.
- Provider adapters only translate registry output into SDK-specific shapes.

### 3. Thin Provider Adapters
- Claude:
  - `src/weclaw/agents/tools.py`
  - builds MCP wrappers from registry entries
  - generates allowlist from registry automatically
- OpenAI:
  - `src/weclaw/agents/openai_tools.py`
  - builds function definitions from registry entries
  - executes the same shared handlers

### 4. Automated Projection Rules
- Registry decides provider support per tool.
- Claude built-in tools (`Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`) stay separate from custom registry tools.
- MCP-prefixed allowlist names are generated instead of maintained by hand.

## Capability Source Layer

- Configurable directories:
  - `CAPABILITIES_DIR`
  - `WORKFLOW_DEFINITIONS_DIR`
  - `TOOL_DEFINITIONS_DIR`
- External workflow definitions are loaded from disk and compiled into normal `ToolSpec` entries.
- Registry rebuild is explicit via `rebuild_tool_registry()`, which is the intended basis for future hot reload.
- Registry refresh now compares workflow directory snapshots and safely replaces the registry only when rebuild succeeds.
- On reload failure, the last known-good registry remains active and the error is exposed through catalog status.
- A background capability watcher can proactively trigger the same refresh path, but it does not own any separate reload logic.

## Why This Is the Long-Term Base

This structure is intentionally chosen to support the next stages without redesign:

1. Tool hot reload
- Add loader layer above the registry.
- Registry remains the runtime source of truth.

2. Workflow tools
- Register composed capabilities as first-class entries.
- Reuse `ToolContext`, `ToolResult`, and provider projection.
- Built-in workflows are now compiled into `ToolSpec` entries and automatically inherit provider projection, confirmation rules, and `/tools` discovery.
- Workflow management now also has a dedicated user-facing discovery surface (`/workflows`) instead of living only inside `/tools`.

3. User-defined tools
- Store user-authored metadata and workflow definitions, then inject into registry.
- The first supported user-defined capability type is declarative workflow definitions managed through runtime tools and stored under `capabilities/workflows/`.

4. Confirmation and permissions
- Attach policy fields like `risk_level`, `requires_confirmation`, `channel_support`.

5. Observability
- Add execution metrics, failure rates, and usage counts at the shared executor layer.

## Implementation Phases

### Phase 1
- Create shared registry and move provider projection to it.
- Remove manual Claude allowlist drift.
- Add regression tests.

### Phase 2
- Add registry-backed `/tools` discovery view and structured tool descriptions.
- Add confirmation policy hooks for risky tools.
- Status: foundation complete in TUI, Telegram, and Feishu; execution-time confirmation enforcement is the next step.

### Phase 2.5
- Add runtime confirmation enforcement at the shared executor layer.
- First enable real interactive confirmation in TUI.
- Keep Telegram/Feishu on the same confirmation protocol so they can adopt it without redesign.
- Status: shared protocol complete; TUI, Telegram, and Feishu all use the same confirmation state machine.

### Phase 3
- Add workflow capability type.
- Allow agent-managed reusable tool recipes.
- Status: workflow foundation complete; built-in workflows are now registry-backed capabilities.

### Phase 3.5
- Add capability source directories and definition loader.
- Move built-in workflows from Python literals to external definition files.
- Add registry rebuild seam for upcoming hot reload.

### Phase 3.6
- Add snapshot-based safe hot reload.
- Keep the previous registry active if reload fails.
- Surface registry state and source information in capability discovery.

### Phase 3.7
- Add a proactive background watcher that triggers the same safe refresh contract.
- Keep watcher behavior cross-platform and polling-based until a stronger need for OS watchers appears.

### Phase 4
- Add user-defined natural-language tool creation with validation and sandbox policy.

### Phase 4.1
- Add runtime management for user-defined workflow definitions.
- Keep user-defined capability scope declarative until a stronger sandbox model exists.

### Phase 4.2
- Add natural-language compilation into the existing workflow definition schema.
- Keep natural-language generation as a producer of JSON definitions, not a parallel runtime model.

### Phase 4.3
- Add workflow schema v2 for richer parameter mapping.
- Keep backward compatibility with `arguments_from_input`.

### Phase 4.4
- Add dedicated workflow discovery and detail entrypoints for user-facing channels.
- Keep workflow management visible without requiring users to browse the entire tool catalog.

### Phase 5
- Add registry hot reload and capability analytics.
