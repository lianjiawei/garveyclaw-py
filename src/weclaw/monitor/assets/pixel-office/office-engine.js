(function () {
  const canvas = document.getElementById("officeCanvas");
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;

  const office = {
    floorTop: 190,
    floorBottom: 558,
    walkMinX: 132,
    walkMaxX: 806,
    corridorY: 408,
    corridorX1: 210,
    corridorX2: 730,
    seat: { x: 656, y: 364 },
    desk: { x: 620, y: 260, width: 190, height: 84 },
    window: { x: 92, y: 106, width: 188, height: 106 },
  };

  const spriteSources = {
    characters: {
      idle: ["./sprites/character-idle-1.svg", "./sprites/character-idle-2.svg"],
      reading: ["./sprites/character-reading-1.svg", "./sprites/character-reading-2.svg"],
      writing: ["./sprites/character-writing-1.svg", "./sprites/character-writing-2.svg"],
      running: ["./sprites/character-running-1.svg", "./sprites/character-running-2.svg"],
      researching: ["./sprites/character-reading-1.svg", "./sprites/character-reading-2.svg"],
      working: ["./sprites/character-writing-1.svg", "./sprites/character-writing-2.svg"],
      waiting: ["./sprites/character-waiting-1.svg", "./sprites/character-waiting-2.svg"],
    },
    props: {
      desk: "./sprites/desk.svg",
      sofa: "./sprites/sofa.svg",
      plant: "./sprites/plant.svg",
      lamp: "./sprites/lamp.svg",
      window: "./sprites/window.svg",
    },
  };

  const sprites = {};
  const roleColors = [
    { body: "#7e96ff", accent: "#1d2741" },
    { body: "#66e6c2", accent: "#1b3a40" },
    { body: "#ff9ec2", accent: "#512746" },
    { body: "#ffd166", accent: "#4a3412" },
  ];
  const seats = [
    { x: 656, y: 364 },
    { x: 356, y: 364 },
    { x: 522, y: 468 },
    { x: 768, y: 460 },
  ];
  const clusterZones = {
    planner: { seatIndex: 1, idle: { x: 344, y: 420 }, waiting: { x: 300, y: 452 } },
    executor: { seatIndex: 0, idle: { x: 642, y: 420 }, waiting: { x: 612, y: 452 } },
    reviewer: { seatIndex: 2, idle: { x: 520, y: 510 }, waiting: { x: 540, y: 470 } },
  };

  const agents = new Map();

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function drawPixelRect(x, y, w, h, color) {
    ctx.fillStyle = color;
    ctx.fillRect(Math.round(x), Math.round(y), w, h);
  }

  function loadSprite(src) {
    return new Promise((resolve) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => resolve(null);
      image.src = src;
    });
  }

  async function loadSprites() {
    const propEntries = await Promise.all(Object.entries(spriteSources.props).map(async ([key, src]) => [key, await loadSprite(src)]));
    propEntries.forEach(([key, image]) => {
      sprites[key] = image;
    });

    sprites.characters = {};
    for (const [state, frames] of Object.entries(spriteSources.characters)) {
      const loadedFrames = await Promise.all(frames.map((src) => loadSprite(src)));
      sprites.characters[state] = loadedFrames.filter(Boolean);
    }
  }

  function ensureAgent(id, index = 0) {
    if (agents.has(id)) return agents.get(id);
    const palette = roleColors[index % roleColors.length];
    const agent = {
      id,
      x: 180 + index * 40,
      y: 442 + (index % 2) * 16,
      targetX: 180 + index * 40,
      targetY: 442 + (index % 2) * 16,
      direction: 1,
      state: "idle",
      wanderCooldown: 0,
      bob: 0,
      typingPhase: 0,
      animationTime: 0,
      palette,
      currentTool: "",
      task: "",
      channel: "",
      seatIndex: index % seats.length,
      label: id,
      route: [],
      routeIndex: 0,
      helper: false,
      role: "primary",
      bubbleText: "",
      bubbleCooldown: 0,
      eventKind: "",
    };
    agents.set(id, agent);
    return agent;
  }

  function chooseIdleTarget() {
    return {
      x: Math.round(office.walkMinX + Math.random() * (office.walkMaxX - office.walkMinX)),
      y: Math.round(408 + Math.random() * 88),
    };
  }

  function classifyAgentAction(state, toolName) {
    if (state === "waiting") return "waiting";
    if (state !== "working") return "idle";
    const tool = String(toolName || "").toLowerCase();
    if (tool.includes("read") || tool.includes("grep") || tool.includes("glob")) return "reading";
    if (tool.includes("write") || tool.includes("edit")) return "writing";
    if (tool.includes("bash")) return "running";
    if (tool.includes("search")) return "researching";
    return "working";
  }

  function buildSkinForChannel(channel) {
    const key = String(channel || "unknown").toLowerCase();
    if (key === "telegram") return { band: "#7c9bff", badge: "#dce6ff", glow: "rgba(124,155,255,0.3)" };
    if (key === "feishu") return { band: "#64e9c4", badge: "#dffbf2", glow: "rgba(100,233,196,0.28)" };
    if (key === "tui") return { band: "#ffd166", badge: "#fff2c9", glow: "rgba(255,209,102,0.26)" };
    return { band: "#ff9ec2", badge: "#ffe3ef", glow: "rgba(255,158,194,0.24)" };
  }

  function mapClusterStatus(status) {
    const value = String(status || "idle").toLowerCase();
    if (value === "working") return "working";
    if (value === "waiting") return "waiting";
    if (value === "error") return "waiting";
    return "idle";
  }

  function actionForClusterAgent(role, status, summary) {
    if (status === "waiting") return "waiting";
    const text = `${role} ${summary || ""}`.toLowerCase();
    if (text.includes("read") || text.includes("分析") || text.includes("检查")) return "reading";
    if (text.includes("write") || text.includes("修改") || text.includes("执行")) return "writing";
    if (text.includes("search") || text.includes("调研")) return "researching";
    if (role === "planner") return "reading";
    if (role === "reviewer") return "researching";
    return status === "working" ? "working" : "idle";
  }

  function skinForRole(role, index) {
    if (role === "planner") {
      return { band: "#8ea3ff", badge: "#eef3ff", glow: "rgba(142,163,255,0.28)" };
    }
    if (role === "executor") {
      return { band: "#63e6be", badge: "#e6fff6", glow: "rgba(99,230,190,0.26)" };
    }
    if (role === "reviewer") {
      return { band: "#ff9ec2", badge: "#ffe8f1", glow: "rgba(255,158,194,0.24)" };
    }
    return buildSkinForChannel(index % 2 === 0 ? "telegram" : "feishu");
  }

  function bubbleForEvent(event, role) {
    if (!event) return "";
    const kind = String(event.kind || "").toLowerCase();
    if (kind === "task_dispatched") return role === "planner" ? "PLAN" : "TASK";
    if (kind === "agent_started") return role === "executor" ? "GO" : "SYNC";
    if (kind === "agent_finished") return role === "reviewer" ? "CHECK" : "DONE";
    if (kind === "agent_note") return "NOTE";
    if (kind === "cluster_finished") return "OK";
    return "MSG";
  }

  function summarizeEvent(event) {
    if (!event) return "";
    const summary = String(event.summary || "").trim();
    const detail = String(event.detail || "").trim();
    return summary || detail;
  }

  function assignRoutedTarget(agent, x, y) {
    agent.route = [
      { x: clamp(agent.x, office.walkMinX, office.walkMaxX), y: office.corridorY },
      { x: clamp(x, office.walkMinX, office.walkMaxX), y: office.corridorY },
      { x, y },
    ];
    if (!agent.routeIndex || agent.routeIndex >= agent.route.length) {
      agent.routeIndex = 0;
    }
  }

  function syncOfficeState(snapshot) {
    const agentData = snapshot.agent || {};
    const activeRuns = Array.isArray(agentData.active_runs) ? agentData.active_runs : [];
    const cluster = snapshot.cluster || {};
    const clusterAgents = Array.isArray(snapshot.agents) ? snapshot.agents.filter((item) => item && item.role !== "primary") : [];
    const activeIds = new Set();

    if (cluster.enabled && clusterAgents.length) {
      const events = Array.isArray(cluster.events) ? cluster.events : [];
      clusterAgents.forEach((item, index) => {
        const workerId = item.agent_id || `cluster-${index}`;
        const worker = ensureAgent(workerId, index);
        const role = String(item.role || "agent").toLowerCase();
        const zone = clusterZones[role] || { seatIndex: index % seats.length, idle: { x: 520, y: 450 }, waiting: { x: 520, y: 470 } };
        const roleEvent = [...events].reverse().find((event) => String(event.agent_id || "") === workerId);
        worker.state = mapClusterStatus(item.status);
        worker.currentTool = "";
        worker.action = actionForClusterAgent(item.role, worker.state, item.summary || "");
        worker.palette = roleColors[index % roleColors.length];
        worker.task = item.summary || cluster.objective || "";
        worker.channel = item.role || "cluster";
        worker.label = item.name || workerId;
        worker.role = role;
        worker.seatIndex = zone.seatIndex;
        worker.skin = skinForRole(role, index);
        worker.helper = false;
        worker.bubbleText = bubbleForEvent(roleEvent, role);
        worker.bubbleCooldown = worker.bubbleText ? 2600 : Math.max(0, worker.bubbleCooldown || 0);
        worker.eventKind = String((roleEvent || {}).kind || "");
        worker.eventSummary = summarizeEvent(roleEvent);
        activeIds.add(workerId);
      });

      for (const [id, worker] of agents.entries()) {
        if (!activeIds.has(id)) {
          worker.state = "offline";
          worker.action = "offline";
          worker.currentTool = "";
          worker.task = "";
        }
      }

      for (const worker of agents.values()) {
        if (worker.state === "working") {
          const seat = seats[worker.seatIndex] || office.seat;
          assignRoutedTarget(worker, seat.x, seat.y);
        } else if (worker.state === "waiting") {
          const zone = clusterZones[worker.role] || clusterZones.executor;
          assignRoutedTarget(worker, zone.waiting.x, zone.waiting.y);
        } else if (worker.state === "idle") {
          const zone = clusterZones[worker.role] || clusterZones.executor;
          assignRoutedTarget(worker, zone.idle.x, zone.idle.y);
          worker.helper = false;
        }
      }
      return;
    }

    const main = ensureAgent("main", 0);
    main.state = agentData.state || "idle";
    main.currentTool = agentData.current_tool || "";
    main.action = classifyAgentAction(main.state, main.currentTool);
    main.palette = roleColors[0];
    main.task = agentData.current_task || "";
    main.channel = agentData.last_channel || "";
    main.label = String(agentData.name || "").trim() || "Weclaw";
    main.seatIndex = 0;
    main.skin = buildSkinForChannel(main.channel);
    activeIds.add("main");

    activeRuns.forEach((run, index) => {
      const workerId = run.conversation_key || `run-${index}`;
      const worker = ensureAgent(workerId, index + 1);
      worker.state = "working";
      worker.currentTool = run.current_tool || "";
      worker.action = classifyAgentAction(worker.state, worker.currentTool);
      worker.task = run.prompt || "";
      worker.channel = run.channel || "";
      worker.label = run.channel || workerId;
      worker.seatIndex = (index + 1) % seats.length;
      worker.palette = roleColors[(index + 1) % roleColors.length];
      worker.skin = buildSkinForChannel(worker.channel);
      activeIds.add(workerId);
    });

    for (const [id, worker] of agents.entries()) {
      if (!activeIds.has(id) && id !== "main") {
        worker.state = "offline";
        worker.action = "offline";
        worker.currentTool = "";
        worker.task = "";
      }
    }

    if (main.state === "idle") main.seatIndex = 2;
    if (main.state === "waiting") {
      main.seatIndex = 3;
      main.helper = false;
    }

    for (const worker of agents.values()) {
      if (worker.state === "working") {
        const seat = seats[worker.seatIndex] || office.seat;
        assignRoutedTarget(worker, seat.x, seat.y);
      } else if (worker.state === "waiting") {
        assignRoutedTarget(worker, 198, 450);
      } else if (worker.state === "idle") {
        worker.helper = false;
      }
    }

    if (activeRuns.length > 1) {
      activeRuns.forEach((run, index) => {
        const helperId = `${run.conversation_key || `run-${index}`}::helper`;
        const helper = ensureAgent(helperId, index + 2);
        helper.state = "working";
        helper.action = "running";
        helper.channel = run.channel || "";
        helper.label = `worker ${index + 1}`;
        helper.helper = true;
        helper.skin = buildSkinForChannel(helper.channel);
        helper.seatIndex = (index + 2) % seats.length;
        assignRoutedTarget(helper, 250 + index * 70, 492 - (index % 2) * 18);
        activeIds.add(helperId);
      });
    }
  }

  function updateAgent(agent, dt) {
    if (agent.state === "offline") return;
    agent.bob += dt * 0.003;
    agent.animationTime += dt;

    if (agent.state === "idle") {
      agent.wanderCooldown -= dt;
      const distance = Math.hypot(agent.targetX - agent.x, agent.targetY - agent.y);
      if (distance < 6 && agent.wanderCooldown <= 0) {
        const next = chooseIdleTarget();
        assignRoutedTarget(agent, next.x, next.y);
        agent.wanderCooldown = 1400 + Math.random() * 1800;
      }
    }

    if (agent.state === "waiting") {
      assignRoutedTarget(agent, office.walkMinX + 120, 454);
    }

    if (agent.state === "idle" && agent.seatIndex === 2) {
      const loungeX = 530;
      const loungeY = 470;
      if (Math.random() < 0.004) {
        assignRoutedTarget(agent, loungeX + Math.round((Math.random() - 0.5) * 60), loungeY + Math.round((Math.random() - 0.5) * 26));
      }
    }

    if (Array.isArray(agent.route) && agent.route.length) {
      const waypoint = agent.route[agent.routeIndex || 0] || agent.route[agent.route.length - 1];
      agent.targetX = waypoint.x;
      agent.targetY = waypoint.y;
    }

    const speed = agent.state === "working" ? 0.11 : 0.07;
    const dx = agent.targetX - agent.x;
    const dy = agent.targetY - agent.y;
    const distance = Math.hypot(dx, dy);
    if (distance > 1) {
      agent.x += (dx / distance) * speed * dt;
      agent.y += (dy / distance) * speed * dt;
      agent.direction = dx >= 0 ? 1 : -1;
    } else if (Array.isArray(agent.route) && agent.route.length) {
      agent.routeIndex = Math.min((agent.routeIndex || 0) + 1, agent.route.length - 1);
    }

    for (const other of agents.values()) {
      if (other === agent || other.state === "offline") continue;
      const pushDx = agent.x - other.x;
      const pushDy = agent.y - other.y;
      const pushDistance = Math.hypot(pushDx, pushDy);
      if (pushDistance > 0 && pushDistance < 34) {
        const force = (34 - pushDistance) * 0.02;
        agent.x += (pushDx / pushDistance) * force * dt;
        agent.y += (pushDy / pushDistance) * force * dt;
      }
    }

    agent.x = clamp(agent.x, office.walkMinX, office.walkMaxX);
    agent.y = clamp(agent.y, office.floorTop + 164, office.floorBottom - 32);
    agent.typingPhase += dt * 0.02;
    agent.bubbleCooldown = Math.max(0, (agent.bubbleCooldown || 0) - dt);
  }

  function drawBackground() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawPixelRect(0, 0, canvas.width, office.floorTop, "#b5c7ff");
    drawPixelRect(0, office.floorTop, canvas.width, canvas.height - office.floorTop, "#d9b991");
    drawPixelRect(0, office.floorTop, canvas.width, 18, "#6f84c4");
    drawPixelRect(0, office.corridorY - 18, canvas.width, 18, "rgba(102, 124, 190, 0.38)");
    drawPixelRect(0, office.corridorY, canvas.width, 4, "rgba(69, 82, 128, 0.34)");

    for (let x = 0; x < canvas.width; x += 32) {
      drawPixelRect(x, office.floorTop, 2, canvas.height - office.floorTop, "rgba(116, 81, 48, 0.16)");
    }
    for (let y = office.floorTop; y < canvas.height; y += 32) {
      drawPixelRect(0, y, canvas.width, 2, "rgba(116, 81, 48, 0.16)");
    }

    if (sprites.window) ctx.drawImage(sprites.window, 92, 104, 188, 112);
    if (sprites.desk) {
      ctx.drawImage(sprites.desk, 308, 230, 210, 140);
      ctx.drawImage(sprites.desk, 606, 230, 210, 140);
    }
    drawPixelRect(302, 372, 80, 22, "#384360");
    drawPixelRect(600, 372, 80, 22, "#384360");
    drawPixelRect(326, 394, 30, 58, "#303754");
    drawPixelRect(624, 394, 30, 58, "#303754");
    if (sprites.plant) ctx.drawImage(sprites.plant, 90, 406, 58, 92);
    if (sprites.lamp) ctx.drawImage(sprites.lamp, 832, 232, 46, 98);
    if (sprites.sofa) ctx.drawImage(sprites.sofa, 780, 468, 118, 64);
    drawPixelRect(700, 470, 54, 14, "#ad845e");
    drawPixelRect(708, 458, 38, 12, "#d9c6a1");
    drawPixelRect(716, 484, 8, 20, "#74533e");
    drawPixelRect(730, 484, 8, 20, "#74533e");
    drawPixelRect(180, 458, 48, 14, "#454f78");
    drawPixelRect(188, 472, 32, 24, "#2e3655");
    drawPixelRect(792, 126, 70, 20, "#2f3858");
    drawPixelRect(802, 136, 12, 58, "#2f3858");
    drawPixelRect(828, 136, 12, 58, "#2f3858");
    drawPixelRect(52, 504, 108, 20, "rgba(38, 22, 10, 0.16)");
    drawPixelRect(574, 506, 198, 24, "rgba(38, 22, 10, 0.18)");
    drawPixelRect(286, 506, 198, 24, "rgba(38, 22, 10, 0.16)");
  }

  function drawWorkingEffect(agent) {
    const seat = seats[agent.seatIndex] || office.seat;
    const glowX = seat.x - 8;
    const glowY = seat.y - 112;
    const pulse = 6 + Math.sin(agent.typingPhase) * 2;
    ctx.fillStyle = "rgba(100, 233, 196, 0.18)";
    ctx.fillRect(glowX - pulse, glowY - pulse, 46 + pulse * 2, 36 + pulse * 2);
    for (let index = 0; index < 4; index += 1) {
      const offset = (agent.typingPhase * 3 + index * 9) % 40;
      const tone = agent.action === "reading" ? "#8be0ff" : agent.action === "running" ? "#ffd166" : "#eef6ff";
      drawPixelRect(glowX + 8 + offset, glowY + 14 + (index % 2) * 4, 5, 3, tone);
    }
  }

  function drawWaitingBeacon(agent) {
    const beaconX = agent.x - 10;
    const beaconY = agent.y - 98;
    const pulse = 10 + Math.sin(agent.animationTime * 0.01) * 4;
    ctx.fillStyle = "rgba(255, 209, 102, 0.18)";
    ctx.fillRect(beaconX - pulse / 2, beaconY - pulse / 2, 20 + pulse, 20 + pulse);
    drawPixelRect(beaconX + 6, beaconY + 2, 6, 12, "#ffd166");
    drawPixelRect(beaconX + 6, beaconY + 16, 6, 4, "#fff4d7");
  }

  function drawSpeechBubble(agent, text) {
    const bubbleX = agent.x - 18;
    const bubbleY = agent.y - 72;
    drawPixelRect(bubbleX, bubbleY, 74, 28, "#ffffff");
    drawPixelRect(bubbleX + 4, bubbleY + 4, 66, 20, "#f7f9ff");
    drawPixelRect(bubbleX + 20, bubbleY + 28, 10, 10, "#ffffff");
    ctx.fillStyle = "#23304d";
    ctx.font = "12px monospace";
    ctx.fillText(text, bubbleX + 11, bubbleY + 18);
  }

  function drawEventCaption(agent) {
    if (!agent.eventSummary) return;
    const text = String(agent.eventSummary).slice(0, 18);
    ctx.fillStyle = "rgba(245,247,255,0.92)";
    ctx.font = "10px monospace";
    ctx.fillText(text, Math.round(agent.x) - 10, Math.round(agent.y) - 84);
  }

  function getCharacterFrame(agent) {
    const action = agent.action || (agent.state === "waiting" ? "waiting" : agent.state === "working" ? "working" : "idle");
    const frames = (sprites.characters && sprites.characters[action]) || (sprites.characters && sprites.characters.idle) || [];
    if (!frames.length) return null;
    const frameIndex = Math.floor(agent.animationTime / 220) % frames.length;
    return frames[frameIndex];
  }

  function drawSkinOverlay(agent, x, baseY) {
    const skin = agent.skin || buildSkinForChannel(agent.channel);
    ctx.fillStyle = skin.glow;
    ctx.fillRect(x + 4, baseY + 4, 34, 46);
    drawPixelRect(x + 10, baseY + 5, 22, 3, skin.band);
    drawPixelRect(x + 24, baseY + 27, 6, 6, skin.badge);
    if (agent.role === "planner") {
      drawPixelRect(x + 7, baseY + 8, 4, 10, "#eef3ff");
    } else if (agent.role === "executor") {
      drawPixelRect(x + 32, baseY + 30, 4, 10, "#e6fff6");
    } else if (agent.role === "reviewer") {
      drawPixelRect(x + 7, baseY + 30, 4, 4, "#ffe8f1");
      drawPixelRect(x + 31, baseY + 30, 4, 4, "#ffe8f1");
    }
    if (agent.helper) {
      drawPixelRect(x + 8, baseY + 30, 4, 4, "#fef3a0");
      drawPixelRect(x + 30, baseY + 30, 4, 4, "#fef3a0");
    }
  }

  function drawAgent(agent) {
    if (agent.state === "offline") return;
    const x = Math.round(agent.x);
    const baseY = Math.round(agent.y + Math.sin(agent.bob) * (agent.state === "working" ? 1 : 2));
    drawPixelRect(x + 8, baseY + 42, 28, 8, "rgba(37, 23, 9, 0.25)");
    const sprite = getCharacterFrame(agent);
    if (sprite) {
      if (agent.direction < 0) {
        ctx.save();
        ctx.translate(x + 48, 0);
        ctx.scale(-1, 1);
        ctx.drawImage(sprite, 0, baseY, 42, 64);
        ctx.scale(-1, 1);
        ctx.translate(-(x + 48), 0);
        drawSkinOverlay(agent, x, baseY);
        ctx.restore();
      } else {
        ctx.drawImage(sprite, x, baseY, 42, 64);
        drawSkinOverlay(agent, x, baseY);
      }
    } else {
      drawPixelRect(x + 12, baseY + 10, 20, 18, "#f3c8a5");
      drawPixelRect(x + 10, baseY + 28, 24, 18, agent.state === "working" ? "#66e6c2" : agent.palette.body);
    }
    if (agent.bubbleText && agent.bubbleCooldown > 0) {
      drawSpeechBubble(agent, agent.bubbleText);
      drawEventCaption(agent);
    } else if (agent.state === "working") {
      const bubble = agent.action === "reading" ? "READ" : agent.action === "writing" ? "WRITE" : agent.action === "running" ? "RUN" : agent.action === "researching" ? "SEARCH" : "WORK";
      drawSpeechBubble(agent, bubble);
    } else if (agent.state === "waiting") {
      drawSpeechBubble(agent, "?");
      drawWaitingBeacon(agent);
    }
    ctx.fillStyle = "#f5f7ff";
    ctx.font = "12px sans-serif";
    const labelText = agent.role && agent.role !== "primary" ? `${agent.role}:${agent.label}` : (agent.helper ? `assist ${agent.label}` : agent.label);
    ctx.fillText(labelText.slice(0, 14), x - 6, baseY + 76);
  }

  function render() {
    drawBackground();
    for (const worker of agents.values()) {
      if (worker.state === "working") {
        drawWorkingEffect(worker);
      }
    }
    const visibleAgents = Array.from(agents.values()).filter((item) => item.state !== "offline").sort((a, b) => a.y - b.y);
    visibleAgents.forEach((worker) => drawAgent(worker));
  }

  function tickOfficeFrame(dt) {
    for (const worker of agents.values()) {
      updateAgent(worker, dt);
    }
    render();
  }

  async function initializeOffice() {
    ensureAgent("main", 0);
    await loadSprites();
  }

  window.PixelOfficeEngine = {
    initializeOffice,
    syncOfficeState,
    tickOfficeFrame,
  };
})();
