let latestSnapshot = null;
let lastFetchAt = 0;

async function fetchActivity() {
  try {
    const response = await fetch("/api/activity", { cache: "no-store" });
    if (!response.ok) return;
    const snapshot = await response.json();
    latestSnapshot = snapshot;
    window.PixelOfficeUI.updateDashboardUi(snapshot);
    window.PixelOfficeEngine.syncOfficeState(snapshot);
    lastFetchAt = performance.now();
  } catch (_error) {
  }
}

function frame(now) {
  const dt = Math.min(32, frame.lastNow ? now - frame.lastNow : 16);
  frame.lastNow = now;

  if (!latestSnapshot || now - lastFetchAt > 2500) {
    fetchActivity();
  }

  window.PixelOfficeEngine.tickOfficeFrame(dt);
  requestAnimationFrame(frame);
}

window.PixelOfficeEngine.initializeOffice().finally(() => {
  fetchActivity();
  requestAnimationFrame(frame);
});
