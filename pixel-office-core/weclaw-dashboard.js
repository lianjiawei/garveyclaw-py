import { PixelOfficeController, loadAssetBundleFromBaseUrl } from '/core/dist/index.js';



const isMockMode = new URLSearchParams(window.location.search).get('mock') === '1';



const ui = {

  dot: document.getElementById('clusterDot'),

  state: document.getElementById('clusterState'),

  updatedAt: document.getElementById('updatedAt'),

  agentList: document.getElementById('agentList'),

};



const officeRoot = document.getElementById('office');

const office = new PixelOfficeController(officeRoot, { zoom: 3.8 });

const TILE_SIZE = 16;

const VOID_TILE = 255;



const applied = {

  agents: '',

  modes: new Map(),

  statuses: new Map(),

  focus: '',

};

let lastPayload = null;

let hasLoadedOfficeLayout = false;



office.on('agentClick', ({ id }) => {

  centerOfficeView();

});



window.addEventListener('resize', () => scheduleFitOfficeToStage());



const officeResizeObserver = new ResizeObserver(() => scheduleFitOfficeToStage());

officeResizeObserver.observe(officeRoot);



function setConnectionState(text, tone = '') {

  ui.state.textContent = text;

  ui.dot.className = `dot ${tone}`;

}



function modeText(mode) {

  return {

    working: '\u6267\u884c\u4e2d',

    thinking: '\u601d\u8003\u4e2d',

    waiting: '\u7b49\u5f85\u4e2d',

    blocked: '\u53d7\u963b',

    idle: '\u7a7a\u95f2',

  }[mode] || mode || '\u672a\u77e5';

}



function clusterText(state) {

  return {

    working: '\u534f\u4f5c\u4e2d',

    waiting: '\u7b49\u5f85\u4e2d',

    idle: '\u7a7a\u95f2',

    done: '\u5df2\u5b8c\u6210',

    error: '\u5f02\u5e38',

  }[state] || state || '\u7a7a\u95f2';

}



function applyCommands(commands) {

  for (const command of commands || []) {

    if (command.type === 'setAgents') {

      const signature = JSON.stringify(command.agents || []);

      if (signature === applied.agents) continue;

      applied.agents = signature;

      office.dispatch(command);

      continue;

    }



    if (command.type === 'setAgentMode') {

      const signature = `${command.mode}:${command.tool || ''}`;

      if (applied.modes.get(command.id) === signature) continue;

      applied.modes.set(command.id, signature);

      office.dispatch(command);

      continue;

    }



    if (command.type === 'setAgentStatus') {

      const signature = `${command.text || ''}:${command.detail || ''}:${command.ttlSeconds ?? ''}`;

      if (applied.statuses.get(command.id) === signature) continue;

      applied.statuses.set(command.id, signature);

      office.dispatch(command);

      continue;

    }



    if (command.type === 'focusAgent') {

      const signature = String(command.id ?? '');

      if (applied.focus === signature) continue;

      applied.focus = signature;

      centerOfficeView();

      continue;

    }



    office.dispatch(command);

  }

}



function resetAppliedCommands() {

  applied.agents = '';

  applied.modes.clear();

  applied.statuses.clear();

  applied.focus = '';

}



function renderSidebar(payload) {

  const cluster = payload.cluster || {};

  const agents = Array.isArray(payload.agents) ? payload.agents : [];

  const state = String(cluster.state || 'idle');

  ui.state.textContent = `${clusterText(state)}${isMockMode ? ' / MOCK' : ''}`;

  ui.updatedAt.textContent = payload.generated_at || '-';

  ui.dot.className = `dot ${state === 'working' ? 'working' : state === 'error' || state === 'waiting' ? 'blocked' : ''}`;

  ui.agentList.innerHTML = agents.map((agent) => {

    const mode = modeText(agent.mode);

    const summary = escapeHtml(agent.summary || '\u5f53\u524d\u6ca1\u6709\u4efb\u52a1\u3002');

    return `

      <article class="agent-card">

        <div class="agent-head">

          <span class="agent-name">${escapeHtml(agent.label)}</span>

          <span class="badge"><i class="dot ${escapeHtml(agent.mode)}"></i>${escapeHtml(mode)}</span>

        </div>

        <p class="agent-summary">${summary}</p>

      </article>

    `;

  }).join('');

}



async function fetchCommands() {

  if (isMockMode) {

    const payload = buildMockPayload();

    lastPayload = payload;

    applyCommands(payload.commands);

    renderSidebar(payload);

    return;

  }



  try {

    const response = await fetch('/api/pixel-office-core/commands', { cache: 'no-store' });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const payload = await response.json();

    lastPayload = payload;

    applyCommands(payload.commands);

    renderSidebar(payload);

  } catch (error) {

    setConnectionState('\u8fde\u63a5\u4e2d\u65ad', 'blocked');

    console.error('Failed to fetch WeClaw office commands', error);

  }

}



async function loadOfficeAssets() {

  setConnectionState('\u52a0\u8f7d\u7d20\u6750\u4e2d');

  try {

    const bundle = await withTimeout(loadAssetBundleFromBaseUrl('/core/public/assets'), 8000);

    office.loadAssets(bundle);

    hasLoadedOfficeLayout = true;

    scheduleFitOfficeToStage();

    window.setTimeout(() => scheduleFitOfficeToStage(), 80);

    if (lastPayload) {

      resetAppliedCommands();

      applyCommands(lastPayload.commands);

    }

  } catch (error) {

    setConnectionState('\u7d20\u6750\u52a0\u8f7d\u5931\u8d25', 'blocked');

    console.error('Failed to load pixel-office-core assets', error);

  }

}



let fitFrame = 0;



function scheduleFitOfficeToStage() {

  if (fitFrame) window.cancelAnimationFrame(fitFrame);

  fitFrame = window.requestAnimationFrame(() => {

    fitFrame = 0;

    fitOfficeToStage();

  });

}



function fitOfficeToStage() {

  if (!hasLoadedOfficeLayout) return;

  const layout = office.officeState.getLayout();

  const rect = officeRoot.getBoundingClientRect();

  if (!layout.cols || !layout.rows || rect.width <= 0 || rect.height <= 0) return;



  const dpr = Math.max(window.devicePixelRatio || 1, 1);

  const visibleBounds = getVisibleLayoutBounds(layout);

  const worldWidth = visibleBounds.width;

  const worldHeight = visibleBounds.height;

  const targetWidth = rect.width * dpr * 0.9;

  const targetHeight = rect.height * dpr * 0.9;

  const fittedZoom = Math.min(targetWidth / worldWidth, targetHeight / worldHeight);

  office.zoom = Math.max(3.8, Math.min(6.4, fittedZoom));

  centerOfficeView(visibleBounds);

}



function centerOfficeView(bounds = null) {

  if (!hasLoadedOfficeLayout) {

    office.dispatch({ type: 'focusAgent', id: null });

    office.dispatch({ type: 'panTo', x: 0, y: 0 });

    return;

  }



  const layout = office.officeState.getLayout();

  const visibleBounds = bounds || getVisibleLayoutBounds(layout);

  const fullWorldWidth = layout.cols * TILE_SIZE;

  const fullWorldHeight = layout.rows * TILE_SIZE;

  const visibleCenterX = visibleBounds.x + visibleBounds.width / 2;

  const visibleCenterY = visibleBounds.y + visibleBounds.height / 2;

  const panX = fullWorldWidth * office.zoom / 2 - visibleCenterX * office.zoom;

  const panY = fullWorldHeight * office.zoom / 2 - visibleCenterY * office.zoom;

  office.dispatch({ type: 'focusAgent', id: null });

  office.dispatch({ type: 'panTo', x: panX, y: panY });

}



function getVisibleLayoutBounds(layout) {

  let minCol = layout.cols;

  let minRow = layout.rows;

  let maxCol = -1;

  let maxRow = -1;



  for (let row = 0; row < layout.rows; row += 1) {

    for (let col = 0; col < layout.cols; col += 1) {

      const tile = layout.tiles[row * layout.cols + col];

      if (tile === VOID_TILE) continue;

      minCol = Math.min(minCol, col);

      minRow = Math.min(minRow, row);

      maxCol = Math.max(maxCol, col + 1);

      maxRow = Math.max(maxRow, row + 1);

    }

  }



  for (const item of layout.furniture || []) {

    minCol = Math.min(minCol, item.col);

    minRow = Math.min(minRow, item.row);

    maxCol = Math.max(maxCol, item.col + 2);

    maxRow = Math.max(maxRow, item.row + 2);

  }



  if (maxCol < minCol || maxRow < minRow) {

    return {

      x: 0,

      y: 0,

      width: layout.cols * TILE_SIZE,

      height: layout.rows * TILE_SIZE,

    };

  }



  const paddingX = 0.5;

  const paddingY = 0.75;

  minCol = Math.max(0, minCol - paddingX);

  minRow = Math.max(0, minRow - paddingY);

  maxCol = Math.min(layout.cols, maxCol + paddingX);

  maxRow = Math.min(layout.rows, maxRow + paddingY);



  return {

    x: minCol * TILE_SIZE,

    y: minRow * TILE_SIZE,

    width: (maxCol - minCol) * TILE_SIZE,

    height: (maxRow - minRow) * TILE_SIZE,

  };

}



function withTimeout(promise, timeoutMs) {

  return new Promise((resolve, reject) => {

    const timer = window.setTimeout(() => reject(new Error(`Timed out after ${timeoutMs}ms`)), timeoutMs);

    promise.then(

      (value) => {

        window.clearTimeout(timer);

        resolve(value);

      },

      (error) => {

        window.clearTimeout(timer);

        reject(error);

      },

    );

  });

}



function buildMockPayload() {

  const step = Math.floor(Date.now() / 4500) % 4;

  const states = [

    ['thinking', 'idle', 'idle'],

    ['idle', 'working', 'idle'],

    ['idle', 'idle', 'thinking'],

    ['idle', 'idle', 'idle'],

  ][step];

  const statusText = [

    ['\u89c4\u5212\u4e2d', '', ''],

    ['\u5df2\u5b8c\u6210', '\u6267\u884c\u4e2d', ''],

    ['\u5df2\u5b8c\u6210', '\u4efb\u52a1\u5b8c\u6210', '\u590d\u6838\u4e2d'],

    ['\u5df2\u5b8c\u6210', '\u5df2\u5b8c\u6210', '\u590d\u6838\u5b8c\u6210'],

  ][step];

  const details = [

    ['\u62c6\u89e3\u4efb\u52a1\u5e76\u751f\u6210\u6267\u884c\u8ba1\u5212', '\u7b49\u5f85\u4efb\u52a1\u5206\u914d', '\u7b49\u5f85\u6267\u884c\u7ed3\u679c'],

    ['\u8ba1\u5212\u5df2\u751f\u6210', '\u8bfb\u53d6\u9879\u76ee\u5e76\u6574\u7406\u7ed3\u8bba', '\u7b49\u5f85\u6267\u884c\u7ed3\u679c'],

    ['\u8ba1\u5212\u5df2\u751f\u6210', '\u8f93\u51fa\u5206\u6790\u7ed3\u679c', '\u68c0\u67e5\u7ed3\u679c\u5b8c\u6574\u6027'],

    ['\u8ba1\u5212\u5df2\u751f\u6210', '\u4efb\u52a1\u8f93\u51fa\u5b8c\u6210', '\u590d\u6838\u901a\u8fc7\uff0c\u7b49\u5f85\u4e0b\u4e00\u6b21\u4efb\u52a1'],

  ][step];

  const ids = [101, 102, 103];

  const labels = ['\u89c4\u5212\u5458', '\u6267\u884c\u5458', '\u590d\u6838\u5458'];

  const roles = ['planner', 'executor', 'reviewer'];



  return {

    generated_at: new Date().toLocaleTimeString(),

    cluster: { state: step === 3 ? 'done' : 'working' },

    agents: ids.map((id, index) => ({

      id,

      agent_id: roles[index],

      label: labels[index],

      role: roles[index],

      mode: states[index],

      status: states[index],

      summary: details[index],

    })),

    commands: [

      {

        type: 'setAgents',

        agents: ids.map((id, index) => ({

          id,

          label: labels[index],

          palette: index,

          isActive: states[index] !== 'idle',

          currentTool: states[index] === 'working' ? 'Write' : states[index] === 'thinking' ? 'Read' : null,

        })),

      },

      ...ids.flatMap((id, index) => [

        {

          type: 'setAgentMode',

          id,

          mode: states[index],

          tool: states[index] === 'working' ? 'Write' : states[index] === 'thinking' ? 'Read' : null,

        },

        {

          type: 'setAgentStatus',

          id,

          text: statusText[index],

          detail: states[index] === 'idle' ? '' : details[index],

          ttlSeconds: step === 3 ? 2 : null,

        },

      ]),

      { type: 'focusAgent', id: step === 3 ? null : ids[step] },

    ],

  };

}



function escapeHtml(value) {

  return String(value || '')

    .replace(/&/g, '&amp;')

    .replace(/</g, '&lt;')

    .replace(/>/g, '&gt;')

    .replace(/"/g, '&quot;')

    .replace(/'/g, '&#039;');

}



await fetchCommands();

void loadOfficeAssets();

window.setInterval(fetchCommands, isMockMode ? 900 : 1500);
