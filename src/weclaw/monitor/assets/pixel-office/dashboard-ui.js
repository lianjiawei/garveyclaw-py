(function () {
  const ui = {
    metricState: document.getElementById("metricState"),
    metricTask: document.getElementById("metricTask"),
    metricTool: document.getElementById("metricTool"),
    metricLastActive: document.getElementById("metricLastActive"),
    metricChannel: document.getElementById("metricChannel"),
    activeRunsBadge: document.getElementById("activeRunsBadge"),
    runList: document.getElementById("runList"),
    agentList: document.getElementById("agentList"),
    eventList: document.getElementById("eventList"),
  };

  function setText(node, value) {
    if (node) {
      node.textContent = value;
    }
  }

  function formatStatus(state) {
    return (state || "idle").replace(/^./, (char) => char.toUpperCase());
  }

  function formatTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  }

  function formatToolLabel(toolName) {
    const tool = String(toolName || "");
    const mapping = {
      read_workspace_file: "读文件",
      write_workspace_file: "写文件",
      edit_workspace_file: "改文件",
      glob_workspace_files: "找文件",
      grep_workspace_content: "搜内容",
      bash: "跑命令",
      web_search: "联网搜索",
      create_task: "创建任务",
      cancel_task: "取消任务",
      get_current_time: "看时间",
    };
    return mapping[tool] || tool || "无";
  }

  function buildRunListHtml(runs) {
    if (!runs.length) {
      return '<div class="run-item"><strong>Idle</strong><span>当前没有并发任务，会话角色会在办公室自由活动。</span></div>';
    }

    return runs
      .map((run) => {
        const toolText = run.current_tool ? `动作：${formatToolLabel(run.current_tool)}` : "动作：巡航中";
        return `<div class="run-item"><strong>${run.channel || "unknown"}</strong><span>${run.prompt || "暂无任务"}</span><span>${toolText}</span></div>`;
      })
      .join("");
  }

  function buildAgentListHtml(agents) {
    if (!agents.length) {
      return '<div class="run-item"><strong>No cluster agents</strong><span>当前未启用或未进入 cluster 协作。</span></div>';
    }

    return agents
      .map((agent) => {
        const review = agent.review_outcome ? ` · review=${agent.review_outcome}` : "";
        const attempts = agent.attempt_count ? ` · attempts=${agent.attempt_count}` : "";
        return `<div class="run-item"><strong>${agent.name || agent.agent_id}</strong><span>${agent.role || "agent"} · ${agent.status || "idle"}${review}${attempts}</span><span>${agent.summary || "暂无摘要"}</span></div>`;
      })
      .join("");
  }

  function buildEventListHtml(events) {
    if (!events.length) {
      return '<div class="run-item"><strong>No events</strong><span>协作时间线会显示在这里。</span></div>';
    }

    return events
      .slice(-8)
      .reverse()
      .map((event) => `<div class="run-item"><strong>${event.summary || event.kind || "event"}</strong><span>${event.agent_id || "system"}</span><span>${event.detail || event.created_at || ""}</span></div>`)
      .join("");
  }

  function updateDashboardUi(snapshot) {
    const agentData = snapshot.agent || {};
    const agents = Array.isArray(snapshot.agents) ? snapshot.agents : [];
    const cluster = snapshot.cluster || {};
    const state = agentData.state || "idle";
    const runs = Array.isArray(agentData.active_runs) ? agentData.active_runs : [];

    setText(ui.metricState, formatStatus(state));
    setText(ui.metricTask, agentData.current_task || "暂无任务");
    setText(ui.metricTool, agentData.current_tool || agentData.tool_status || "无");
    setText(ui.metricLastActive, formatTime(agentData.last_active_at));
    setText(ui.metricChannel, agentData.last_channel || "-");
    setText(ui.activeRunsBadge, `${agentData.active_runs_count || 0} active runs`);

    if (ui.runList) {
      ui.runList.innerHTML = buildRunListHtml(runs);
    }
    if (ui.agentList) {
      ui.agentList.innerHTML = buildAgentListHtml(agents);
    }
    if (ui.eventList) {
      ui.eventList.innerHTML = buildEventListHtml(Array.isArray(cluster.events) ? cluster.events : []);
    }
  }

  window.PixelOfficeUI = {
    updateDashboardUi,
  };
})();
