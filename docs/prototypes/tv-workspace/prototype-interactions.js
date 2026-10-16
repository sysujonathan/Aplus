/* TradingView interaction study and production A++ WebView behavior. */
const chartBoot = window.APLUS_CHART_BOOT || {};
const chartCoordinates = window.AplusChartCoordinates;
const tvStateKey = chartBoot.production
  ? `aplus-tv-${chartBoot.identity}`
  : 'aplus-tv-prototype-002259-v3';
const DEFAULT_VISIBLE_BARS = 204;
const RIGHT_MARGIN_BARS = 12;
const focusDate = chartBoot.evidence?.bo_date || '2026-06-22';
const focusStartIndex = Math.max(0, bars.findIndex(row => row.date === focusDate));
const focusIndex = index => focusStartIndex + index;
const toolNames = {
  fib: '斐波那契回撤', long: '多头仓位', ray: '水平射线', trend: '趋势线',
  short: '空头仓位', rectangle: '矩形', up: '向上箭头', down: '向下箭头',
  arrow: '箭头', path: '多段折线', channel: '平行通道', text: '文字', ruler: '价格范围'
};
const onePointTools = new Set(['ray', 'up', 'down', 'text']);
const fibLevelDefaults = [
  [0, true, '#4caf50'], [.236, false, '#4caf50'], [.382, false, '#4caf50'], [.5, true, '#4caf50'],
  [.618, false, '#4caf50'], [.786, false, '#4caf50'], [1, true, '#4caf50'], [1.618, false, '#4caf50'],
  [2.618, false, '#4caf50'], [3.618, false, '#4caf50'], [4.236, false, '#4caf50'], [1.272, false, '#4caf50'],
  [1.414, false, '#4caf50'], [2.272, false, '#4caf50'], [2.414, false, '#4caf50'], [2, false, '#4caf50'],
  [3, false, '#4caf50'], [3.272, false, '#4caf50'], [3.414, false, '#4caf50'], [4, false, '#4caf50'],
  [4.272, false, '#4caf50'], [4.414, false, '#4caf50'], [4.618, false, '#4caf50'], [4.764, false, '#4caf50']
];
const fibVisibilityDefaults = {
  ticks: { enabled: true }, seconds: { enabled: true, min: 1, max: 59 },
  minutes: { enabled: true, min: 1, max: 59 }, hours: { enabled: true, min: 1, max: 24 },
  days: { enabled: true, min: 1, max: 366 }, weeks: { enabled: true, min: 1, max: 52 },
  months: { enabled: true, min: 1, max: 12 }, ranges: { enabled: true }
};
const defaultObjects = [
  { id: 'trend-1', type: 'trend', a: { index: focusIndex(20), value: 3.43 }, b: { index: focusIndex(70), value: 5.34 }, color: '#f23645', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) },
  { id: 'fib-1', type: 'fib', a: { index: focusIndex(21), value: 3.14 }, b: { index: focusIndex(60), value: 4.98 }, color: '#4caf50', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) },
  { id: 'channel-1', type: 'channel', a: { index: focusIndex(64), value: 4.45 }, b: { index: focusIndex(78), value: 5.45 }, color: '#21b9d6', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) },
  { id: 'long-1', type: 'long', a: { index: focusIndex(66), value: 4.76 }, b: { index: focusIndex(78), value: 5.98 }, stop: 4.08, color: '#8d939c', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) }
];

let activeDrawingTool = null;
let draftAnchor = null;
let draftCursor = null;
let draftPoints = [];
let selectedId = null;
let gesture = null;
let priceZoom = 1;
let priceCenterOffset = 0;
let magnetEnabled = true;
let hitRegions = [];
let replayActive = false;
let replayPlaying = false;
let replaySpeedValue = 1;
let replayTimer = null;
let preReplayView = null;
let activeTimeframeUnit = 'daily';
let objectSettingsSnapshot = null;
let replayCalendarCursor = new Date(`${bars[Math.max(0, bars.length - 35)].date}T00:00:00`);
const initialPrototypeState = loadPrototypeState();
let userObjects = chartBoot.production
  ? structuredClone(chartBoot.drawings || initialPrototypeState.objects || [])
  : initialPrototypeState.objects || structuredClone(defaultObjects);
let chartAlerts = structuredClone(chartBoot.alerts || []);
let undoStack = [];
let redoStack = [];
let bridgeRevision = Number(chartBoot.revision || 0);
let bridgeSaveChain = Promise.resolve();
let bridgeSaveFailed = false;
viewCount = Math.min(DEFAULT_VISIBLE_BARS, bars.length);
viewOffset = 0;
if (initialPrototypeState.view) {
  viewCount = clamp(Number(initialPrototypeState.view.count) || DEFAULT_VISIBLE_BARS, 18, bars.length);
  viewOffset = clamp(Number(initialPrototypeState.view.offset) || 0, 0, bars.length - viewCount);
  priceZoom = clamp(Number(initialPrototypeState.view.priceZoom) || 1, .45, 7);
  priceCenterOffset = Number(initialPrototypeState.view.priceCenterOffset) || 0;
}

function loadPrototypeState() {
  if (chartBoot.production && chartBoot.state) return structuredClone(chartBoot.state);
  try { return JSON.parse(localStorage.getItem(tvStateKey) || '{}'); } catch (_) { return {}; }
}

function savePrototypeState() {
  try {
    userObjects.forEach(object => chartCoordinates.capture(object, bars));
    const toolbar = document.querySelector('#favoriteTools');
    const state = {
      objects: structuredClone(userObjects),
      toolbar: { left: toolbar.style.left, top: toolbar.style.top },
      view: { count: viewCount, offset: viewOffset, priceZoom, priceCenterOffset },
      timeframe: activeTimeframeUnit
    };
    try { localStorage.setItem(tvStateKey, JSON.stringify(state)); } catch (_) { /* Store remains authoritative. */ }
    if (chartBoot.production && window.pywebview?.api?.save_state) {
      document.querySelector('.save-state').innerHTML = '<span class="saved-dot"></span>正在保存到本机…';
      bridgeSaveChain = bridgeSaveChain.then(() => window.pywebview.api.save_state(state, bridgeRevision))
        .then(result => {
          bridgeRevision = Number(result.revision);
          if (Array.isArray(result.alerts)) { chartAlerts = result.alerts; updateObjectTree(); }
          bridgeSaveFailed = false;
          document.querySelector('.save-state').innerHTML = '<span class="saved-dot"></span>所有更改已保存到本机';
        }).catch(error => {
          bridgeSaveFailed = true;
          document.querySelector('.save-state').textContent = `尚未保存：${error.message || error}`;
        });
    }
    return bridgeSaveChain;
  } catch (error) {
    bridgeSaveFailed = true;
    document.querySelector('.save-state').textContent = `尚未保存：${error.message || error}`;
    return Promise.resolve();
  }
}

function rememberObjects() {
  userObjects.forEach(object => chartCoordinates.capture(object, bars));
  undoStack.push(structuredClone(userObjects));
  if (undoStack.length > 30) undoStack.shift();
  redoStack = [];
  updateUndoButtons();
}

let closingWorkspace = false;
['pointerdown', 'keydown', 'input', 'change', 'click', 'wheel'].forEach(type =>
  document.addEventListener(type, event => {
    if (closingWorkspace) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, { capture: true, passive: false }));
window.aplusFlushAndClose = async () => {
  if (closingWorkspace) return;
  closingWorkspace = true;
  try {
    await savePrototypeState();
    if (bridgeSaveFailed) throw new Error('最后的修改尚未保存');
    await window.pywebview.api.finish_close();
  } catch (error) {
    closingWorkspace = false;
    try { await window.pywebview.api.cancel_close(); } catch (_) { /* Native guard also permits retry on bridge errors. */ }
    showToast(`${error.message || error}，窗口已保留，请重试关闭`);
  }
};

function updateUndoButtons() {
  document.querySelector('#undoAction').classList.toggle('muted', undoStack.length === 0);
  document.querySelector('#redoAction').classList.toggle('muted', redoStack.length === 0);
}

function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
function visibleWindow() {
  const maxEnd = Math.min(replayIndex + 1, bars.length);
  const end = clamp(Math.round(bars.length - viewOffset), Math.min(viewCount, maxEnd), maxEnd);
  const begin = Math.max(0, end - viewCount);
  return { begin, end, data: bars.slice(begin, end) };
}

function dashPattern(style) {
  if (style === 'dashed') return [7, 5];
  if (style === 'dotted') return [2, 4];
  return [];
}

function ensureFibSettings(object) {
  if (!object || object.type !== 'fib') return object;
  object.fibLevels = Array.isArray(object.fibLevels) && object.fibLevels.length
    ? object.fibLevels
    : fibLevelDefaults.map(([value, enabled, color]) => ({ value, enabled, color }));
  object.fibVisibility = object.fibVisibility || structuredClone(fibVisibilityDefaults);
  for (const [key, defaults] of Object.entries(fibVisibilityDefaults)) {
    object.fibVisibility[key] = { ...defaults, ...(object.fibVisibility[key] || {}) };
  }
  object.fibExtend = object.fibExtend || 'none';
  object.fibLevelFormat = object.fibLevelFormat || 'value';
  object.fibLabelHorizontal = object.fibLabelHorizontal || 'left';
  object.fibLabelVertical = object.fibLabelVertical || 'middle';
  object.fibTextHorizontal = object.fibTextHorizontal || 'center';
  object.fibTextVertical = object.fibTextVertical || 'middle';
  object.fibFontSize = object.fibFontSize || 12;
  return object;
}

function loadFibTemplate() {
  try { return JSON.parse(localStorage.getItem(`${tvStateKey}-fib-template`) || 'null'); } catch (_) { return null; }
}

function saveFibTemplate(object) {
  const template = Object.fromEntries(Object.entries(object).filter(([key]) => key === 'color' || key === 'width' || key === 'dash' || key.startsWith('fib')));
  try { localStorage.setItem(`${tvStateKey}-fib-template`, JSON.stringify(template)); } catch (_) { /* prototype remains usable without storage. */ }
}

function objectVisibleAtTimeframe(object) {
  if (object.type !== 'fib') return true;
  const visibility = ensureFibSettings(object).fibVisibility;
  const key = activeTimeframeUnit === 'weekly' ? 'weeks' : activeTimeframeUnit === 'monthly' ? 'months' : 'days';
  return visibility[key]?.enabled !== false;
}

function segmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const length = dx * dx + dy * dy;
  if (!length) return Math.hypot(px - ax, py - ay);
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / length, 0, 1);
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function registerSegment(id, ax, ay, bx, by, tolerance = 7, handle = null) {
  hitRegions.push({ id, handle, hit: (x, y) => segmentDistance(x, y, ax, ay, bx, by) <= tolerance });
}

function registerRect(id, x1, y1, x2, y2) {
  const left = Math.min(x1, x2) - 6;
  const right = Math.max(x1, x2) + 6;
  const top = Math.min(y1, y2) - 6;
  const bottom = Math.max(y1, y2) + 6;
  hitRegions.push({ id, handle: null, hit: (x, y) => x >= left && x <= right && y >= top && y <= bottom });
}

function registerHandle(id, handle, x, y) {
  hitRegions.push({ id, handle, hit: (px, py) => Math.hypot(px - x, py - y) <= 9 });
}

function hitRegion(x, y) {
  for (let index = hitRegions.length - 1; index >= 0; index -= 1) {
    if (hitRegions[index].hit(x, y)) return hitRegions[index];
  }
  return null;
}
function hitObject(x, y) { return hitRegion(x, y)?.id || null; }

function renderWorkspace() {
  const rect = wrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = rect.width;
  const height = rect.height;
  const right = 61;
  const bottom = 76;
  const top = 42;
  const left = 8;
  const plotWidth = width - right - left;
  const plotHeight = height - bottom - top;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = css('--bg');
  ctx.fillRect(0, 0, width, height);

  const visible = visibleWindow();
  const rows = visible.data;
  if (!rows.length) return;
  const rawLow = Math.min(...rows.map(row => row.low), 3.14) - .12;
  const rawHigh = Math.max(...rows.map(row => row.high), 5.98) + .12;
  const rawCenter = (rawHigh + rawLow) / 2 + priceCenterOffset;
  const span = (rawHigh - rawLow) / priceZoom;
  const low = rawCenter - span / 2;
  const high = rawCenter + span / 2;
  const y = value => top + (high - value) / (high - low) * plotHeight;
  const futureSpace = replayActive ? RIGHT_MARGIN_BARS : Math.max(0, RIGHT_MARGIN_BARS - viewOffset);
  const candleWidth = plotWidth / Math.max(1, viewCount + futureSpace);
  const xGlobal = globalIndex => xFor(globalIndex - visible.begin, left, candleWidth);
  canvas._view = { rows, begin: visible.begin, end: visible.end, left, right, top, bottom, width, height, plotWidth, plotHeight, y, low, high, candleWidth, xGlobal, futureSpace };
  hitRegions = [];

  ctx.strokeStyle = css('--grid');
  ctx.lineWidth = 1;
  ctx.font = '12px Segoe UI';
  ctx.fillStyle = '#8c8f96';
  ctx.textAlign = 'left';
  const priceStep = span < 1.5 ? .2 : span < 3.5 ? .4 : 1;
  for (let value = Math.ceil(low / priceStep) * priceStep; value <= high; value += priceStep) {
    const py = Math.round(y(value)) + .5;
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(width - right, py); ctx.stroke();
    ctx.fillText(value.toFixed(2), width - right + 8, py + 4);
  }
  const monthMarks = [];
  rows.forEach((row, index) => {
    const month = row.date.slice(5, 7);
    if ((index === 0 || rows[index - 1].date.slice(5, 7) !== month)) monthMarks.push({ index, label: `${Number(month)}月` });
  });
  monthMarks.forEach(mark => {
    const px = Math.round(xFor(mark.index, left, candleWidth)) + .5;
    ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, height - bottom); ctx.stroke();
    ctx.fillText(mark.label, px + 5, height - 52);
  });

  ctx.strokeStyle = css('--orange');
  ctx.lineWidth = 1.45;
  ctx.beginPath();
  rows.forEach((row, index) => {
    const px = xFor(index, left, candleWidth);
    const py = y(ema[visible.begin + index]);
    index ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
  });
  ctx.stroke();

  rows.forEach((row, index) => {
    const px = xFor(index, left, candleWidth);
    const color = row.close >= row.open ? '#f5f5f5' : '#2962ff';
    const bodyWidth = Math.max(3, Math.min(13, candleWidth * .62));
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(px, y(row.high)); ctx.lineTo(px, y(row.low)); ctx.stroke();
    const bodyTop = y(Math.max(row.open, row.close));
    const bodyBottom = y(Math.min(row.open, row.close));
    ctx.fillRect(px - bodyWidth / 2, bodyTop, bodyWidth, Math.max(2, bodyBottom - bodyTop));
  });

  if (drawingsVisible) {
    if (chartBoot.production || !replayActive || replayIndex >= focusIndex(57)) drawStrategyEvidence(ctx, canvas._view);
    userObjects.filter(item => item.visible !== false && objectVisibleAtTimeframe(item) && (!replayActive || (item.createdAtIndex ?? bars.length - 1) <= replayIndex)).forEach(item => drawUserObject(ctx, canvas._view, item));
    if (draftAnchor && draftCursor) drawUserObject(ctx, canvas._view, {
      id: 'draft', type: activeDrawingTool, a: draftAnchor, b: draftCursor,
      points: activeDrawingTool === 'path' ? [...draftPoints, draftCursor] : undefined,
      stop: draftAnchor.value - Math.abs(draftCursor.value - draftAnchor.value) * .55,
      color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true
    }, true);
  }
  if (replayActive) drawReplayBoundary(ctx, canvas._view);
  drawLastPrice(ctx, rows.at(-1).close, y, width, right);
  document.querySelector('.ema-value').textContent = ema[Math.min(replayIndex, bars.length - 1)].toFixed(2);
}

function drawStrategyEvidence(ctx, view) {
  const { left, width, right, y, candleWidth, xGlobal } = view;
  const plotRight = width - right;
  if (chartBoot.production) {
    const evidence = chartBoot.evidence || {};
    const at = day => chartCoordinates.indexForDate(bars, day, activeTimeframeUnit);
    const floor = Number(evidence.gap_floor);
    const top = Number(evidence.gap_top);
    const boIndex = at(evidence.bo_date);
    const setupIndex = at(evidence.h2_setup_date || evidence.setup_date || evidence.asof);
    if (boIndex >= 0 && setupIndex >= 0 && Number.isFinite(floor) && Number.isFinite(top) && top > floor) {
      const gapLeft = Math.max(left, xGlobal(boIndex) - candleWidth * .55);
      const gapRight = Math.min(plotRight, xGlobal(Math.max(boIndex, setupIndex)) + candleWidth * .55);
      ctx.save();
      ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 1.2; ctx.globalAlpha = .72;
      ctx.setLineDash([6, 4]); ctx.strokeRect(gapLeft, y(top), gapRight - gapLeft, y(floor) - y(top));
      ctx.setLineDash([]); ctx.globalAlpha = .6;
      ctx.beginPath(); ctx.moveTo(gapLeft, y(floor)); ctx.lineTo(plotRight, y(floor)); ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = '#fff'; ctx.strokeStyle = '#2962ff';
      ctx.fillRect((gapLeft + gapRight) / 2 - 19, y((floor + top) / 2) - 10, 38, 20);
      ctx.strokeRect((gapLeft + gapRight) / 2 - 19, y((floor + top) / 2) - 10, 38, 20);
      ctx.fillStyle = '#2962ff'; ctx.font = '12px Segoe UI'; ctx.textAlign = 'center';
      ctx.fillText('Gap', (gapLeft + gapRight) / 2, y((floor + top) / 2) + 4);
      ctx.restore();
      if (evidence.bo_high) callout(ctx, 'BO', xGlobal(boIndex), y(Number(evidence.bo_high)), xGlobal(boIndex - 3), y(Number(evidence.bo_high)) - 28);
      if (at(evidence.h1_date) >= 0 && evidence.h1_high) callout(ctx, 'H1', xGlobal(at(evidence.h1_date)), y(Number(evidence.h1_high)), xGlobal(at(evidence.h1_date) - 3), y(Number(evidence.h1_high)) - 28);
      if (evidence.h2_high && (!replayActive || replayIndex >= setupIndex)) callout(ctx, 'H2', xGlobal(setupIndex), y(Number(evidence.h2_high)), xGlobal(setupIndex - 3), y(Number(evidence.h2_high)) - 32, true);
      callout(ctx, 'Gap Floor', gapLeft, y(floor), Math.max(left + 5, gapLeft - 32), y(floor) + 28);
      registerRect('strategy-evidence', gapLeft, y(top), gapRight, y(floor));
    }
    (chartBoot.holdingLevels || []).forEach(level => {
      const price = Number(level.price ?? level.value);
      if (!Number.isFinite(price) || price <= 0) return;
      ctx.save(); ctx.strokeStyle = level.color || '#b8bdc8'; ctx.setLineDash(level.style === '--' ? [7, 5] : level.style === '-.' ? [7, 4, 2, 4] : []);
      ctx.beginPath(); ctx.moveTo(left, y(price)); ctx.lineTo(plotRight, y(price)); ctx.stroke();
      ctx.fillStyle = level.color || '#b8bdc8'; ctx.font = '12px Segoe UI'; ctx.textAlign = 'left';
      ctx.fillText(`${level.label} ${price.toFixed(2)}`, left + 8, y(price) - 5); ctx.restore();
    });
    return;
  }
  const gapLeft = Math.max(left, xGlobal(focusIndex(57)) - candleWidth * .55);
  const gapRight = Math.min(plotRight, xGlobal(focusIndex(64)) + candleWidth * .55);
  ctx.save();
  ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 1.2; ctx.globalAlpha = .72;
  ctx.setLineDash([6, 4]); ctx.strokeRect(gapLeft, y(4.56), gapRight - gapLeft, y(4.38) - y(4.56));
  ctx.setLineDash([]); ctx.globalAlpha = .6;
  ctx.beginPath(); ctx.moveTo(gapLeft, y(4.38)); ctx.lineTo(plotRight, y(4.38)); ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#fff'; ctx.strokeStyle = '#2962ff';
  ctx.fillRect((gapLeft + gapRight) / 2 - 19, y(4.47) - 10, 38, 20);
  ctx.strokeRect((gapLeft + gapRight) / 2 - 19, y(4.47) - 10, 38, 20);
  ctx.fillStyle = '#2962ff'; ctx.font = '12px Segoe UI'; ctx.textAlign = 'center';
  ctx.fillText('Gap', (gapLeft + gapRight) / 2, y(4.47) + 4);
  ctx.restore();
  callout(ctx, 'BO', xGlobal(focusIndex(57)), y(4.56), xGlobal(focusIndex(54)), y(4.83));
  callout(ctx, 'H1', xGlobal(focusIndex(58)), y(4.45), xGlobal(focusIndex(55)), y(4.72));
  if (!replayActive || replayIndex >= focusIndex(70)) callout(ctx, 'H2', xGlobal(focusIndex(70)), y(5.19), xGlobal(focusIndex(67)), y(5.48), true);
  callout(ctx, 'Gap Floor', gapLeft, y(4.38), gapLeft - 32, y(4.18));
  registerRect('strategy-evidence', gapLeft, y(4.56), gapRight, y(4.38));
}

function drawReplayBoundary(ctx, view) {
  const boundaryX = Math.min(view.width - view.right, view.xGlobal(replayIndex) + view.candleWidth / 2);
  ctx.save();
  ctx.fillStyle = 'rgba(4,4,4,.24)';
  ctx.fillRect(boundaryX, view.top, view.width - view.right - boundaryX, view.plotHeight);
  ctx.strokeStyle = '#777a81'; ctx.lineWidth = 1; ctx.setLineDash([6, 6]);
  ctx.beginPath(); ctx.moveTo(boundaryX, view.top); ctx.lineTo(boundaryX, view.height - view.bottom); ctx.stroke();
  ctx.restore();
}

function drawUserObject(ctx, view, object, draft = false) {
  if (!object || !object.a) return;
  const { left, width, right, top, height, bottom, y, xGlobal } = view;
  const plotRight = width - right;
  const ax = xGlobal(object.a.index);
  const ay = y(object.a.value);
  const b = object.b || object.a;
  const bx = xGlobal(b.index);
  const by = y(b.value);
  const selectedObjectNow = object.id === selectedId;
  ctx.save();
  ctx.strokeStyle = object.color || '#2962ff';
  ctx.fillStyle = object.color || '#2962ff';
  ctx.lineWidth = object.width || 2;
  ctx.setLineDash(dashPattern(object.dash));
  ctx.globalAlpha = (draft ? .65 : 1) * (object.alpha ?? 1);

  if (['trend', 'arrow', 'ruler'].includes(object.type)) {
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    if (object.type === 'arrow') drawArrowHead(ctx, ax, ay, bx, by);
    if (object.type === 'ruler') {
      const change = object.a.value ? ((b.value / object.a.value - 1) * 100).toFixed(2) : '0.00';
      ctx.fillStyle = '#202020'; ctx.fillRect((ax + bx) / 2 - 46, (ay + by) / 2 - 24, 92, 20);
      ctx.fillStyle = '#e3e3e3'; ctx.textAlign = 'center'; ctx.font = '12px Segoe UI';
      ctx.fillText(`${change}% · ${Math.abs(Math.round(b.index - object.a.index))}根`, (ax + bx) / 2, (ay + by) / 2 - 10);
    }
    registerSegment(object.id, ax, ay, bx, by);
  } else if (object.type === 'path') {
    const points = object.points?.length ? object.points : [object.a, object.b];
    const screens = points.map(point => [xGlobal(point.index), y(point.value)]);
    ctx.beginPath();
    screens.forEach(([x, yy], index) => index ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy));
    ctx.stroke();
    screens.slice(1).forEach(([x, yy], index) => registerSegment(object.id, screens[index][0], screens[index][1], x, yy));
  } else if (object.type === 'ray') {
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(plotRight, ay); ctx.stroke();
    registerSegment(object.id, ax, ay, plotRight, ay);
  } else if (object.type === 'rectangle') {
    if (object.fill !== false) { ctx.globalAlpha = object.backgroundAlpha ?? .13; ctx.fillRect(ax, ay, bx - ax, by - ay); }
    ctx.globalAlpha = (draft ? .65 : 1) * (object.alpha ?? 1); ctx.strokeRect(ax, ay, bx - ax, by - ay);
    registerRect(object.id, ax, ay, bx, by);
  } else if (object.type === 'fib') {
    ensureFibSettings(object);
    const startX = object.fibExtend === 'left' || object.fibExtend === 'both' ? left : Math.min(ax, bx);
    const endX = object.fibExtend === 'right' || object.fibExtend === 'both' ? plotRight : Math.max(ax, bx);
    const enabledLevels = object.fibLevels.filter(level => level.enabled);
    const renderedLevels = enabledLevels.map(level => {
      const ratio = object.fibReverse ? 1 - Number(level.value) : Number(level.value);
      const value = object.a.value + (b.value - object.a.value) * ratio;
      return { ...level, valueAtLevel: value, py: y(value) };
    });
    if (object.fibBackground && renderedLevels.length > 1) {
      const sorted = [...renderedLevels].sort((aLevel, bLevel) => aLevel.py - bLevel.py);
      ctx.save(); ctx.globalAlpha = object.fibBackgroundAlpha ?? .12;
      sorted.slice(0, -1).forEach((level, index) => {
        ctx.fillStyle = object.fibUseOneColor ? (object.fibSingleColor || object.color) : level.color;
        ctx.fillRect(startX, level.py, endX - startX, sorted[index + 1].py - level.py);
      });
      ctx.restore();
    }
    if (object.fibTrendLine) {
      ctx.save(); ctx.strokeStyle = object.color || '#4caf50'; ctx.setLineDash(dashPattern(object.fibTrendDash || 'dashed'));
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke(); ctx.restore();
    }
    renderedLevels.forEach(level => {
      const lineColor = object.fibUseOneColor ? (object.fibSingleColor || object.color || '#4caf50') : level.color;
      ctx.strokeStyle = lineColor; ctx.fillStyle = lineColor;
      ctx.lineWidth = object.fibHorizontalWidth || object.width || 1;
      ctx.setLineDash(dashPattern(object.fibHorizontalDash || object.dash));
      ctx.beginPath(); ctx.moveTo(startX, level.py); ctx.lineTo(endX, level.py); ctx.stroke();
      const labelX = object.fibLabelHorizontal === 'right' ? endX - 5 : object.fibLabelHorizontal === 'center' ? (startX + endX) / 2 : startX + 5;
      const labelAlign = object.fibLabelHorizontal === 'right' ? 'right' : object.fibLabelHorizontal === 'center' ? 'center' : 'left';
      const labelOffset = object.fibLabelVertical === 'top' ? -5 : object.fibLabelVertical === 'bottom' ? (object.fibFontSize || 12) + 4 : 4;
      const levelLabel = object.fibLevelFormat === 'percent' ? `${(Number(level.value) * 100).toFixed(1).replace('.0', '')}%` : `${level.value}`;
      const text = [object.fibShowLevels !== false ? levelLabel : '', object.fibShowPrices !== false ? `(${level.valueAtLevel.toFixed(2)})` : ''].filter(Boolean).join(' ');
      if (object.fibText !== false && text) {
        ctx.font = `${object.fibFontSize || 12}px Segoe UI`; ctx.textAlign = labelAlign; ctx.fillText(text, labelX, level.py + labelOffset);
      }
      registerSegment(object.id, startX, level.py, endX, level.py);
    });
  } else if (object.type === 'channel') {
    const delta = Math.max(.2, Math.abs(b.value - object.a.value) * .34);
    [-delta, 0, delta].forEach((offset, index) => {
      ctx.setLineDash(index === 1 ? [6, 6] : dashPattern(object.dash));
      const y1 = y(object.a.value + offset), y2 = y(b.value + offset);
      ctx.beginPath(); ctx.moveTo(ax, y1); ctx.lineTo(bx, y2); ctx.stroke();
      registerSegment(object.id, ax, y1, bx, y2);
    });
  } else if (object.type === 'long' || object.type === 'short') {
    const entry = object.a.value;
    const target = b.value;
    const stop = object.stop ?? (entry + (entry - target) * .55);
    const x1 = Math.min(ax, bx), x2 = Math.max(ax, bx);
    const targetColor = 'rgba(8,153,129,.24)';
    const stopColor = 'rgba(242,54,69,.25)';
    ctx.fillStyle = targetColor; ctx.fillRect(x1, y(target), x2 - x1, y(entry) - y(target));
    ctx.fillStyle = stopColor; ctx.fillRect(x1, y(entry), x2 - x1, y(stop) - y(entry));
    ctx.globalAlpha = 1; ctx.strokeStyle = object.color; ctx.setLineDash([]);
    [target, entry, stop].forEach(value => { ctx.beginPath(); ctx.moveTo(x1, y(value)); ctx.lineTo(x2, y(value)); ctx.stroke(); });
    registerRect(object.id, x1, Math.min(y(target), y(stop)), x2, Math.max(y(target), y(stop)));
  } else if (object.type === 'up' || object.type === 'down') {
    ctx.font = `${object.size || 30}px Segoe UI`; ctx.textAlign = 'center';
    ctx.fillText(object.type === 'up' ? '⇧' : '⇩', ax, ay + 10);
    registerRect(object.id, ax - 14, ay - 18, ax + 14, ay + 14);
  } else if (object.type === 'text') {
    ctx.font = '13px Segoe UI'; ctx.textAlign = 'left'; ctx.fillText(object.text || '笔记', ax, ay);
    registerRect(object.id, ax, ay - 16, ax + 38, ay + 5);
  }

  if (selectedObjectNow && !draft) drawSelectionHandles(ctx, object, ax, ay, bx, by);
  ctx.restore();
}

function drawArrowHead(ctx, ax, ay, bx, by) {
  const angle = Math.atan2(by - ay, bx - ax);
  ctx.beginPath();
  ctx.moveTo(bx, by);
  ctx.lineTo(bx - 12 * Math.cos(angle - .45), by - 12 * Math.sin(angle - .45));
  ctx.moveTo(bx, by);
  ctx.lineTo(bx - 12 * Math.cos(angle + .45), by - 12 * Math.sin(angle + .45));
  ctx.stroke();
}

function drawSelectionHandles(ctx, object, ax, ay, bx, by) {
  ctx.save(); ctx.setLineDash([]); ctx.fillStyle = '#0b0b0b'; ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 2;
  let points = onePointTools.has(object.type) ? [{ x: ax, y: ay, handle: 'a' }] : [{ x: ax, y: ay, handle: 'a' }, { x: bx, y: by, handle: 'b' }];
  if (object.type === 'path' && object.points?.length) {
    points = object.points.map((point, index) => ({ x: canvas._view.xGlobal(point.index), y: canvas._view.y(point.value), handle: `point:${index}` }));
  }
  if (['long', 'short'].includes(object.type)) points.push({ x: bx, y: canvas._view.y(object.stop), handle: 'stop' });
  points.forEach(({ x, y: yy, handle }) => {
    ctx.beginPath(); ctx.arc(x, yy, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    registerHandle(object.id, handle, x, yy);
  });
  ctx.restore();
}

function eventPoint(event) {
  const view = canvas._view;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const yPx = event.clientY - rect.top;
  const index = clamp(view.begin + (x - view.left) / view.candleWidth - .5, 0, bars.length - 1);
  const value = view.high - (yPx - view.top) / view.plotHeight * (view.high - view.low);
  return { x, y: yPx, index, value };
}

function selectObject(id, x, y) {
  if (id === 'strategy-evidence') {
    showToast('策略证据为只读；不会被人工标绘覆盖');
    return;
  }
  selectedId = id;
  const object = userObjects.find(item => item.id === id);
  selected = Boolean(object);
  if (!object) {
    document.querySelector('#selectedObject').classList.remove('visible');
    renderWorkspace();
    return;
  }
  const toolbar = document.querySelector('#selectedObject');
  toolbar.classList.add('visible');
  toolbar.style.left = `${clamp(x + 12, 8, wrap.clientWidth - 190)}px`;
  toolbar.style.top = `${clamp(y - 52, 54, wrap.clientHeight - 90)}px`;
  document.querySelector('#quickWidth').textContent = `${object.width || 2}px`;
  document.querySelector('.color-swatch').style.background = object.color || '#2962ff';
  updateObjectTree();
  renderWorkspace();
}

function objectAtSelection() { return userObjects.find(item => item.id === selectedId); }

function startTool(type, button) {
  if (type === 'magnet') {
    magnetEnabled = !magnetEnabled;
    button.classList.toggle('on', magnetEnabled);
    button.setAttribute('aria-pressed', String(magnetEnabled));
    showToast(magnetEnabled ? '磁铁模式已开启：端点吸附最近K线的OHLC' : '磁铁模式已关闭');
    return;
  }
  if (type === 'lock') {
    button.classList.toggle('active');
    showToast(button.classList.contains('active') ? '人工标绘已锁定' : '人工标绘已解锁');
    return;
  }
  if (['cross'].includes(type)) {
    activeDrawingTool = null; draftAnchor = null; draftCursor = null; draftPoints = [];
  } else if (toolNames[type]) {
    activeDrawingTool = type; draftAnchor = null; draftCursor = null; draftPoints = [];
  } else if (type === 'zoom' || type === 'zoomOut') {
    activeDrawingTool = type; draftAnchor = null; draftCursor = null; draftPoints = [];
  }
  document.querySelectorAll('.tool[data-tool]:not(.magnet):not([data-tool="lock"]), [data-favorite]').forEach(item => item.classList.remove('active'));
  if (button) button.classList.add('active');
  canvas.classList.toggle('drawing', Boolean(activeDrawingTool));
  const instruction = type === 'path' ? '依次单击转折点，双击完成' : (type === 'zoom' || type === 'zoomOut') ? '在图上拖框选择区域' : onePointTools.has(type) ? '在图上单击放置' : '依次单击起点和终点';
  showToast(activeDrawingTool ? `${toolNames[type] || button?.title}：${instruction}` : `${button?.title || '十字光标'}已选择`);
  renderWorkspace();
}

function snapDrawingPoint(point) {
  const snapped = { index: Math.round(point.index), value: point.value };
  if (!magnetEnabled || !canvas._view) return snapped;
  const index = clamp(Math.round(point.index), 0, bars.length - 1);
  const row = bars[index];
  const candidates = [row.open, row.high, row.low, row.close];
  const nearest = candidates.reduce((best, value) => Math.abs(canvas._view.y(value) - point.y) < Math.abs(canvas._view.y(best) - point.y) ? value : best, candidates[0]);
  if (Math.abs(canvas._view.y(nearest) - point.y) <= 16) return { index, value: nearest };
  return snapped;
}

function exitDrawingMode() {
  activeDrawingTool = null; draftAnchor = null; draftCursor = null; draftPoints = [];
  document.querySelectorAll('[data-favorite], .tool[data-tool]:not(.magnet):not([data-tool="lock"])').forEach(item => item.classList.remove('active'));
  document.querySelector('[data-tool="cross"]').classList.add('active');
  canvas.classList.remove('drawing');
}

function placeDrawing(point) {
  const snapped = snapDrawingPoint(point);
  if (!draftAnchor) {
    draftAnchor = snapped;
    draftCursor = { ...draftAnchor };
    if (activeDrawingTool === 'path') { draftPoints = [draftAnchor]; renderWorkspace(); return; }
    if (!onePointTools.has(activeDrawingTool)) { renderWorkspace(); return; }
  }
  if (activeDrawingTool === 'path') {
    const last = draftPoints.at(-1);
    if (!last || Math.abs(last.index - snapped.index) > .2 || Math.abs(last.value - snapped.value) > .01) draftPoints.push(snapped);
    draftCursor = snapped; renderWorkspace(); return;
  }
  const finish = snapped;
  const object = {
    id: `${activeDrawingTool}-${Date.now()}`, type: activeDrawingTool,
    a: draftAnchor, b: onePointTools.has(activeDrawingTool) ? draftAnchor : finish,
    color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true, createdAtIndex: replayActive ? replayIndex : bars.length - 1
  };
  if (object.type === 'fib') {
    Object.assign(object, structuredClone(loadFibTemplate() || {}));
    ensureFibSettings(object);
  }
  if (object.type === 'long') object.stop = object.a.value - Math.abs(object.b.value - object.a.value) * .55;
  if (object.type === 'short') object.stop = object.a.value + Math.abs(object.b.value - object.a.value) * .55;
  rememberObjects();
  userObjects.push(object);
  draftAnchor = null; draftCursor = null;
  savePrototypeState(); updateObjectTree();
  selectObject(object.id, point.x, point.y);
  showToast(`${toolNames[object.type]}已保存到当前标的`);
  exitDrawingMode();
}

function finishPath(point) {
  if (activeDrawingTool !== 'path' || draftPoints.length < 2) return false;
  const snapped = snapDrawingPoint(point);
  const points = [...draftPoints];
  if (Math.abs(points.at(-1).index - snapped.index) > .2 || Math.abs(points.at(-1).value - snapped.value) > .01) points.push(snapped);
  rememberObjects();
  const object = { id: `path-${Date.now()}`, type: 'path', a: points[0], b: points.at(-1), points, color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true, createdAtIndex: replayActive ? replayIndex : bars.length - 1 };
  userObjects.push(object); savePrototypeState(); updateObjectTree(); selectObject(object.id, point.x, point.y);
  exitDrawingMode(); showToast('多段折线已保存到当前标的'); renderWorkspace(); return true;
}

function zoomAround(factor, ratio) {
  const view = canvas._view;
  if (!view) return;
  const oldCount = view.end - view.begin;
  const anchorIndex = view.begin + ratio * oldCount;
  const maxCount = Math.min(bars.length, replayIndex + 1);
  const nextCount = clamp(Math.round(viewCount * factor), 18, maxCount);
  let nextEnd = Math.round(anchorIndex + (1 - ratio) * nextCount);
  nextEnd = clamp(nextEnd, nextCount, replayIndex + 1);
  viewCount = nextCount;
  viewOffset = bars.length - nextEnd;
  renderWorkspace(); savePrototypeState();
}

function panChart(barDelta) {
  const maxOffset = Math.max(0, Math.min(replayIndex + 1, bars.length) - viewCount);
  viewOffset = clamp(viewOffset + Math.round(barDelta), 0, maxOffset);
  renderWorkspace(); savePrototypeState();
}

function resetChartView() {
  viewCount = Math.min(DEFAULT_VISIBLE_BARS, replayIndex + 1, bars.length);
  viewOffset = 0;
  priceZoom = 1;
  priceCenterOffset = 0;
  renderWorkspace(); savePrototypeState();
  showToast(`已重置：${viewCount}根K线，右侧保留${RIGHT_MARGIN_BARS}根K线宽度`);
}

function updateCrosshair(point) {
  const view = canvas._view;
  const inPlot = point.x >= view.left && point.x <= view.width - view.right && point.y >= view.top && point.y <= view.height - view.bottom;
  canvas.classList.toggle('price-scale', point.x > view.width - view.right && point.y < view.height - 39);
  canvas.classList.toggle('time-scale', point.y > view.height - view.bottom && point.y < view.height - 39 && point.x < view.width - view.right);
  if (!inPlot) { crosshair.classList.remove('show'); return; }
  const localIndex = clamp(Math.round(point.index) - view.begin, 0, view.rows.length - 1);
  const row = view.rows[localIndex];
  crosshair.style.setProperty('--cx', `${point.x}px`);
  crosshair.style.setProperty('--cy', `${point.y}px`);
  document.querySelector('#crossPrice').textContent = point.value.toFixed(2);
  document.querySelector('#crossDate').textContent = row.date;
  crosshair.classList.add('show'); updateReadout(row);
}

function showZoomSelection(start, end) {
  const box = document.querySelector('#zoomSelection');
  box.classList.add('visible');
  box.style.left = `${Math.min(start.x, end.x)}px`;
  box.style.top = `${Math.min(start.y, end.y)}px`;
  box.style.width = `${Math.abs(end.x - start.x)}px`;
  box.style.height = `${Math.abs(end.y - start.y)}px`;
}

function hideZoomSelection() { document.querySelector('#zoomSelection').classList.remove('visible'); }

function applyZoomSelection(start, end, zoomOut) {
  const view = canvas._view;
  const widthFraction = clamp(Math.abs(end.x - start.x) / view.plotWidth, .08, 1);
  const centerIndex = (start.index + end.index) / 2;
  const nextCount = zoomOut
    ? clamp(Math.round(viewCount / widthFraction), 18, replayIndex + 1)
    : clamp(Math.round(Math.abs(end.index - start.index)), 18, replayIndex + 1);
  let nextEnd = Math.round(centerIndex + nextCount / 2);
  nextEnd = clamp(nextEnd, nextCount, replayIndex + 1);
  viewCount = nextCount;
  viewOffset = bars.length - nextEnd;
  const heightFraction = clamp(Math.abs(end.y - start.y) / view.plotHeight, .08, 1);
  const desiredCenter = (start.value + end.value) / 2;
  const currentCenter = (view.high + view.low) / 2;
  priceCenterOffset += desiredCenter - currentCenter;
  priceZoom = clamp(priceZoom * (zoomOut ? heightFraction : 1 / heightFraction), .45, 7);
  exitDrawingMode(); hideZoomSelection(); renderWorkspace(); savePrototypeState();
  showToast(zoomOut ? '已按框选区域缩小' : '已放大到框选区域');
}

function moveObjectFromOriginal(object, original, start, point, handle) {
  if (!object || !original) return;
  if (handle) {
    const snapped = snapDrawingPoint(point);
    const edited = old => ({ ...snapped,
      date: Math.round(snapped.index) === Math.round(old.index) && old.date
        ? old.date : chartCoordinates.dateAt(bars, snapped.index) });
    if (handle === 'a') object.a = edited(original.a);
    else if (handle === 'b') object.b = edited(original.b || original.a);
    else if (handle === 'stop') object.stop = snapped.value;
    else if (handle.startsWith('point:')) {
      const index = Number(handle.split(':')[1]); object.points[index] = edited(original.points[index]);
      object.a = object.points[0]; object.b = object.points.at(-1);
    }
    return;
  }
  const deltaIndex = point.index - start.index;
  const deltaValue = point.value - start.value;
  const movePoint = value => chartCoordinates.move(value, deltaIndex, deltaValue, bars);
  object.a = movePoint(original.a);
  object.b = movePoint(original.b || original.a);
  if (original.c) object.c = movePoint(original.c);
  if (original.points) object.points = original.points.map(movePoint);
  if (typeof original.stop === 'number') object.stop = original.stop + deltaValue;
}

canvas.addEventListener('pointerdown', event => {
  const view = canvas._view;
  const point = eventPoint(event);
  canvas.setPointerCapture(event.pointerId);
  if (point.x > view.width - view.right && point.y < view.height - 39) {
    gesture = { mode: 'price-scale', startX: event.clientX, startY: event.clientY, startZoom: priceZoom, moved: false };
  } else if (point.y > view.height - view.bottom && point.y < view.height - 39 && point.x < view.width - view.right) {
    gesture = { mode: 'time-scale', startX: event.clientX, startCount: viewCount, moved: false };
  } else if (activeDrawingTool === 'zoom' || activeDrawingTool === 'zoomOut') {
    gesture = { mode: 'zoom-box', zoomOut: activeDrawingTool === 'zoomOut', start: point, startX: event.clientX, startY: event.clientY, moved: false };
    showZoomSelection(point, point);
  } else if (!activeDrawingTool) {
    const region = hitRegion(point.x, point.y);
    if (region && region.id !== 'strategy-evidence' && !document.querySelector('[data-tool="lock"]').classList.contains('active')) {
      if (selectedId !== region.id) selectObject(region.id, point.x, point.y);
      const object = userObjects.find(item => item.id === region.id);
      gesture = { mode: region.handle ? 'object-handle' : 'object-move', objectId: region.id, handle: region.handle, original: structuredClone(object), before: structuredClone(userObjects), start: point, startX: event.clientX, startY: event.clientY, moved: false };
    } else {
      gesture = { mode: region ? 'select' : 'pan', objectId: region?.id || null, startX: event.clientX, startY: event.clientY, startOffset: viewOffset, moved: false };
    }
  } else {
    gesture = { mode: 'draw', startX: event.clientX, startY: event.clientY, moved: false };
  }
});

canvas.addEventListener('pointermove', event => {
  const point = eventPoint(event);
  updateCrosshair(point);
  if (draftAnchor && activeDrawingTool) { draftCursor = snapDrawingPoint(point); renderWorkspace(); }
  if (!gesture) return;
  const dx = event.clientX - gesture.startX;
  const dy = event.clientY - gesture.startY;
  gesture.moved ||= Math.abs(dx) + Math.abs(dy) > 4;
  if (gesture.mode === 'price-scale') {
    priceZoom = clamp(gesture.startZoom * Math.exp(-dy / 105), .45, 7);
    renderWorkspace();
  } else if (gesture.mode === 'time-scale') {
    const next = clamp(Math.round(gesture.startCount * Math.exp(dx / 230)), 18, replayIndex + 1);
    viewCount = next; viewOffset = clamp(viewOffset, 0, Math.max(0, bars.length - next)); renderWorkspace();
  } else if (gesture.mode === 'pan' && gesture.moved) {
    const candle = Math.max(3, canvas._view.candleWidth);
    viewOffset = clamp(gesture.startOffset + Math.round(dx / candle), 0, Math.max(0, Math.min(replayIndex + 1, bars.length) - viewCount));
    renderWorkspace();
  } else if (gesture.mode === 'zoom-box') {
    showZoomSelection(gesture.start, point);
  } else if ((gesture.mode === 'object-move' || gesture.mode === 'object-handle') && gesture.moved) {
    const object = userObjects.find(item => item.id === gesture.objectId);
    moveObjectFromOriginal(object, gesture.original, gesture.start, point, gesture.mode === 'object-handle' ? gesture.handle : null);
    renderWorkspace();
  }
});

canvas.addEventListener('pointerup', event => {
  if (!gesture) return;
  const point = eventPoint(event);
  if (gesture.mode === 'draw' && !gesture.moved) placeDrawing(point);
  else if (gesture.mode === 'select' && !gesture.moved) selectObject(gesture.objectId, point.x, point.y);
  else if (gesture.mode === 'pan' && !gesture.moved) selectObject(hitObject(point.x, point.y), point.x, point.y);
  else if (gesture.mode === 'zoom-box') {
    if (gesture.moved) applyZoomSelection(gesture.start, point, gesture.zoomOut);
    else { hideZoomSelection(); showToast('拖框选择需要放大或缩小的区域'); }
  } else if ((gesture.mode === 'object-move' || gesture.mode === 'object-handle') && gesture.moved) {
    undoStack.push(gesture.before); redoStack = []; updateUndoButtons(); savePrototypeState(); updateObjectTree(); renderWorkspace();
    showToast(gesture.mode === 'object-handle' ? '结构端点已调整并保存' : '图形位置已移动并保存');
  } else if ((gesture.mode === 'object-move' || gesture.mode === 'object-handle') && !gesture.moved) selectObject(gesture.objectId, point.x, point.y);
  if (gesture.moved && ['price-scale', 'time-scale', 'pan'].includes(gesture.mode)) savePrototypeState();
  gesture = null;
});

canvas.addEventListener('pointercancel', () => { gesture = null; hideZoomSelection(); });
canvas.addEventListener('mouseleave', () => { crosshair.classList.remove('show'); updateReadout(bars[Math.min(replayIndex, bars.length - 1)]); });
canvas.addEventListener('wheel', event => {
  event.preventDefault();
  const view = canvas._view;
  const rect = canvas.getBoundingClientRect();
  const ratio = clamp((event.clientX - rect.left - view.left) / view.plotWidth, 0, 1);
  zoomAround(Math.exp(event.deltaY * .0016), ratio);
}, { passive: false });

const chartNavControls = document.querySelector('#chartNavControls');
wrap.addEventListener('pointermove', event => {
  if (event.target.closest?.('#chartNavControls')) return;
  const rect = wrap.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const inHoverZone = x >= rect.width / 2 - 250 && x <= rect.width / 2 + 250 && y >= rect.height - 150 && y <= rect.height - 38;
  chartNavControls.classList.toggle('visible', inHoverZone && !replayActive);
});
wrap.addEventListener('pointerleave', () => chartNavControls.classList.remove('visible'));
chartNavControls.addEventListener('pointerleave', event => {
  if (!wrap.contains(event.relatedTarget)) chartNavControls.classList.remove('visible');
});
document.querySelector('#navZoomOut').addEventListener('click', () => zoomAround(1.25, .5));
document.querySelector('#navZoomIn').addEventListener('click', () => zoomAround(.8, .5));
document.querySelector('#navMoveLeft').addEventListener('click', () => panChart(Math.max(1, Math.round(viewCount * .12))));
document.querySelector('#navMoveRight').addEventListener('click', () => panChart(-Math.max(1, Math.round(viewCount * .12))));
document.querySelector('#navReset').addEventListener('click', resetChartView);

canvas.addEventListener('dblclick', event => {
  const point = eventPoint(event);
  const view = canvas._view;
  if (finishPath(point)) return;
  if (point.x > view.width - view.right) {
    priceZoom = 1; priceCenterOffset = 0; renderWorkspace(); showToast('价格轴已恢复自动刻度'); return;
  }
  const id = hitObject(point.x, point.y);
  if (id === 'strategy-evidence') { showToast('GAP H2 策略证据为只读'); return; }
  if (id) { selectObject(id, point.x, point.y); openObjectSettings(); }
});

document.querySelectorAll('.tool[data-tool], [data-favorite]').forEach(button => {
  button.addEventListener('click', () => startTool(button.dataset.favorite || button.dataset.tool, button));
});

const favoriteToolbar = document.querySelector('#favoriteTools');
const savedToolbar = loadPrototypeState().toolbar;
if (savedToolbar?.left) { favoriteToolbar.style.left = savedToolbar.left; favoriteToolbar.style.top = savedToolbar.top; }
document.querySelector('#favoriteDragHandle').addEventListener('pointerdown', event => {
  event.preventDefault(); event.stopPropagation();
  const toolbarRect = favoriteToolbar.getBoundingClientRect();
  const wrapRect = wrap.getBoundingClientRect();
  const start = { x: event.clientX, y: event.clientY, left: toolbarRect.left - wrapRect.left, top: toolbarRect.top - wrapRect.top };
  const move = moveEvent => {
    favoriteToolbar.style.left = `${clamp(start.left + moveEvent.clientX - start.x, 4, wrap.clientWidth - favoriteToolbar.offsetWidth - 4)}px`;
    favoriteToolbar.style.top = `${clamp(start.top + moveEvent.clientY - start.y, 4, wrap.clientHeight - favoriteToolbar.offsetHeight - 43)}px`;
  };
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); savePrototypeState(); showToast('收藏工具栏位置已保存'); };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up, { once: true });
});

function openWorkspaceModal(id) {
  document.querySelectorAll('.modal').forEach(modal => modal.classList.remove('show'));
  document.querySelector(`#${id}`).classList.add('show');
  document.querySelector('#modalBackdrop').classList.add('show');
}
function closeWorkspaceModal() { document.querySelector('#modalBackdrop').classList.remove('show'); }

const drawingPalette = ['#2962ff', '#f23645', '#ff9800', '#26a69a', '#ab47bc', '#f5f5f5', '#787b86', '#21b9d6'];

function commonLineSettings(object, label = '线条') {
  return `<div class="setting-row"><span>${label}</span><select data-setting="width" aria-label="线宽"><option value="1">1 px</option><option value="2">2 px</option><option value="3">3 px</option><option value="4">4 px</option></select><select data-setting="dash" aria-label="线型"><option value="solid">实线</option><option value="dashed">虚线</option><option value="dotted">点线</option></select></div>
    <div class="color-palette">${drawingPalette.map(color => `<button class="color-choice ${color === object.color ? 'active' : ''}" data-color="${color}" style="--choice:${color}" title="${color}"></button>`).join('')}</div>`;
}

function paletteSettings(object) {
  return `<div class="color-palette">${drawingPalette.map(color => `<button class="color-choice ${color === object.color ? 'active' : ''}" data-color="${color}" style="--choice:${color}" title="${color}"></button>`).join('')}</div>`;
}

function commitObjectSetting(object, key, value) {
  rememberObjects(); object[key] = value; savePrototypeState(); renderWorkspace();
}

function renderFibSettings(object) {
  ensureFibSettings(object);
  const host = document.querySelector('#toolSpecificSettings');
  const levels = object.fibLevels.map((level, index) => `
    <label class="fib-level-cell ${level.enabled ? '' : 'disabled'}">
      <input type="checkbox" data-fib-level-enabled="${index}" ${level.enabled ? 'checked' : ''} aria-label="启用 ${level.value} 水平位">
      <input type="number" step="any" data-fib-level-value="${index}" value="${level.value}" ${level.enabled ? '' : 'disabled'} aria-label="水平位 ${level.value}">
      <input type="color" data-fib-level-color="${index}" value="${level.color}" ${level.enabled ? '' : 'disabled'} aria-label="水平位 ${level.value} 颜色">
    </label>`).join('');
  host.innerHTML = `<div class="fib-setting-stack">
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibTrendLine" ${object.fibTrendLine ? 'checked' : ''}>趋势线</label><input type="color" data-fib-setting="color" value="${object.color || '#4caf50'}" aria-label="趋势线颜色"><select data-fib-setting="fibTrendDash" aria-label="趋势线线型"><option value="solid">实线</option><option value="dashed">虚线</option><option value="dotted">点线</option></select></div>
    <div class="fib-setting-row"><b>水平线</b><select data-fib-setting="fibHorizontalWidth" aria-label="水平线粗细"><option value="1">1 px</option><option value="2">2 px</option><option value="3">3 px</option><option value="4">4 px</option></select><select data-fib-setting="fibHorizontalDash" aria-label="水平线线型"><option value="solid">实线</option><option value="dashed">虚线</option><option value="dotted">点线</option></select></div>
    <div class="fib-setting-row"><b>延伸</b><select class="fib-wide-select" data-fib-setting="fibExtend" aria-label="延伸方式"><option value="none">不要扩大</option><option value="left">向左延伸</option><option value="right">向右延伸</option><option value="both">双向延伸</option></select></div>
    <div class="fib-level-grid">${levels}</div>
    <div class="fib-setting-row"><b>使用一种颜色</b><input type="color" data-fib-setting="fibSingleColor" value="${object.fibSingleColor || object.color || '#4caf50'}" aria-label="斐波那契单一颜色"><label class="switch"><input type="checkbox" data-fib-setting="fibUseOneColor" ${object.fibUseOneColor ? 'checked' : ''}><i></i></label></div>
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibBackground" ${object.fibBackground ? 'checked' : ''}>背景</label><input type="range" min="0" max="100" data-fib-setting="fibBackgroundAlpha" value="${Math.round((object.fibBackgroundAlpha ?? .12) * 100)}" aria-label="背景透明度"><span>${Math.round((object.fibBackgroundAlpha ?? .12) * 100)}%</span></div>
    <div class="fib-setting-row compact"><label><input type="checkbox" data-fib-setting="fibReverse" ${object.fibReverse ? 'checked' : ''}>反手</label></div>
    <div class="fib-setting-row compact"><label><input type="checkbox" data-fib-setting="fibShowPrices" ${object.fibShowPrices === false ? '' : 'checked'}>价格</label></div>
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibShowLevels" ${object.fibShowLevels === false ? '' : 'checked'}>水平位</label><select data-fib-setting="fibLevelFormat" aria-label="水平位格式"><option value="value">数值</option><option value="percent">百分比</option></select></div>
    <div class="fib-setting-row"><b>标签</b><select data-fib-setting="fibLabelHorizontal" aria-label="标签水平位置"><option value="left">左</option><option value="center">中心</option><option value="right">右</option></select><select data-fib-setting="fibLabelVertical" aria-label="标签垂直位置"><option value="top">顶部</option><option value="middle">中间</option><option value="bottom">底部</option></select></div>
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibText" ${object.fibText === false ? '' : 'checked'}>文本</label><select data-fib-setting="fibTextHorizontal" aria-label="文本水平位置"><option value="left">左</option><option value="center">中心</option><option value="right">右</option></select><select data-fib-setting="fibTextVertical" aria-label="文本垂直位置"><option value="top">顶部</option><option value="middle">中间</option><option value="bottom">底部</option></select></div>
    <div class="fib-setting-row"><b>字体大小</b><select data-fib-setting="fibFontSize" aria-label="字体大小"><option value="10">10</option><option value="11">11</option><option value="12">12</option><option value="14">14</option><option value="16">16</option></select></div>
    <div class="fib-setting-row compact muted-option"><label><input type="checkbox" disabled>基于对数坐标的Fib水平</label></div>
  </div>`;
  ['fibTrendDash', 'fibHorizontalWidth', 'fibHorizontalDash', 'fibExtend', 'fibLevelFormat', 'fibLabelHorizontal', 'fibLabelVertical', 'fibTextHorizontal', 'fibTextVertical', 'fibFontSize'].forEach(key => {
    const control = host.querySelector(`[data-fib-setting="${key}"]`);
    if (control) control.value = String(object[key] ?? ({ fibTrendDash: 'dashed', fibHorizontalWidth: object.width || 1, fibHorizontalDash: object.dash || 'solid' }[key] || ''));
  });
  host.querySelectorAll('[data-fib-level-enabled]').forEach(control => control.addEventListener('change', () => {
    const index = Number(control.dataset.fibLevelEnabled); rememberObjects(); object.fibLevels[index].enabled = control.checked;
    savePrototypeState(); renderFibSettings(object); renderWorkspace();
  }));
  host.querySelectorAll('[data-fib-level-value]').forEach(control => control.addEventListener('input', () => {
    const index = Number(control.dataset.fibLevelValue); commitObjectSetting(object.fibLevels[index], 'value', Number(control.value));
  }));
  host.querySelectorAll('[data-fib-level-color]').forEach(control => control.addEventListener('input', () => {
    const index = Number(control.dataset.fibLevelColor); object.fibLevels[index].color = control.value; savePrototypeState(); renderWorkspace();
  }));
  host.querySelectorAll('[data-fib-setting]').forEach(control => control.addEventListener('change', () => {
    const key = control.dataset.fibSetting;
    let value = control.type === 'checkbox' ? control.checked : control.value;
    if (key === 'fibHorizontalWidth' || key === 'fibFontSize') value = Number(value);
    if (key === 'fibBackgroundAlpha') value = Number(value) / 100;
    commitObjectSetting(object, key, value);
    if (key === 'color') document.querySelector('.color-swatch').style.background = value;
  }));
}

function renderToolSpecificSettings(object) {
  const host = document.querySelector('#toolSpecificSettings');
  if (object.type === 'fib') { renderFibSettings(object); return; }
  let extra = '';
  if (object.type === 'long' || object.type === 'short') extra = `<div class="setting-section-title">仓位工具</div><div class="setting-row"><span>利润区域</span><button class="color-input"><i style="background:#089981"></i>#089981</button><label class="switch"><input type="checkbox" checked><i></i></label></div><div class="setting-row"><span>止损区域</span><button class="color-input"><i style="background:#f23645"></i>#F23645</button><label class="switch"><input type="checkbox" checked><i></i></label></div><div class="setting-row"><span>显示</span><label><input type="checkbox" checked> 风险/回报比</label><label><input type="checkbox" checked> 数量</label></div>`;
  else if (object.type === 'rectangle') extra = `<div class="setting-section-title">矩形</div><div class="setting-row"><span>背景</span><label class="switch"><input data-setting="fill" type="checkbox" ${object.fill === false ? '' : 'checked'}><i></i></label><input data-setting="backgroundAlpha" type="range" min="0" max="60" value="${Math.round((object.backgroundAlpha ?? .13) * 100)}"><b class="opacity-value">${Math.round((object.backgroundAlpha ?? .13) * 100)}%</b></div>`;
  else if (object.type === 'up' || object.type === 'down') extra = `<div class="setting-section-title">箭头标记</div><div class="setting-row"><span>大小</span><select data-setting="size"><option value="24">小</option><option value="30">中</option><option value="38">大</option></select></div>`;
  else if (object.type === 'path') extra = `<div class="setting-section-title">多段折线</div><div class="setting-row"><span>节点</span><span>${object.points?.length || 2} 个转折点；可在图上逐点拖动</span></div>`;
  else if (object.type === 'channel') extra = `<div class="setting-section-title">平行通道</div><div class="setting-row"><span>中线</span><label class="switch"><input type="checkbox" checked><i></i></label><span>虚线</span></div>`;
  else if (object.type === 'text') extra = `<div class="setting-section-title">文字</div><div class="setting-row"><span>内容</span><input data-setting="text" type="text" value="${object.text || '笔记'}"></div>`;
  else if (object.type === 'ray') extra = `<div class="setting-section-title">水平射线</div><div class="setting-row"><span>价格标签</span><label class="switch"><input type="checkbox" checked><i></i></label><span>在价格轴显示</span></div>`;
  else if (object.type === 'arrow') extra = `<div class="setting-section-title">箭头</div><div class="setting-row"><span>箭头端</span><select><option>终点</option><option>起点和终点</option></select></div>`;
  else if (object.type === 'ruler') extra = `<div class="setting-section-title">价格范围</div><div class="setting-row"><span>标签</span><label class="switch"><input type="checkbox" checked><i></i></label><span>价格、涨跌幅、K线数</span></div>`;
  const markerOrText = ['up', 'down', 'text'].includes(object.type);
  const positionTool = ['long', 'short'].includes(object.type);
  const base = markerOrText ? paletteSettings(object) : positionTool
    ? `<div class="setting-row"><span>边界</span><select data-setting="width" aria-label="线宽"><option value="1">1 px</option><option value="2">2 px</option><option value="3">3 px</option><option value="4">4 px</option></select></div>${paletteSettings(object)}`
    : commonLineSettings(object);
  host.innerHTML = `<div class="tool-setting-stack">${base}<div class="setting-row"><span>透明度</span><input data-setting="alpha" type="range" min="10" max="100" value="${Math.round((object.alpha ?? 1) * 100)}"><b class="opacity-value">${Math.round((object.alpha ?? 1) * 100)}%</b></div>${extra}</div>`;
  if (host.querySelector('[data-setting="width"]')) host.querySelector('[data-setting="width"]').value = String(object.width || 2);
  if (host.querySelector('[data-setting="dash"]')) host.querySelector('[data-setting="dash"]').value = object.dash || 'solid';
  if (host.querySelector('[data-setting="size"]')) host.querySelector('[data-setting="size"]').value = String(object.size || 30);
  host.querySelectorAll('[data-color]').forEach(button => button.addEventListener('click', () => {
    rememberObjects(); object.color = button.dataset.color; document.querySelector('.color-swatch').style.background = object.color; renderToolSpecificSettings(object); savePrototypeState(); renderWorkspace();
  }));
  host.querySelectorAll('[data-setting]').forEach(control => control.addEventListener('change', () => {
    rememberObjects();
    const key = control.dataset.setting;
    if (key === 'width' || key === 'size') object[key] = Number(control.value);
    else if (key === 'alpha' || key === 'backgroundAlpha') object[key] = Number(control.value) / 100;
    else if (key === 'fill') object.fill = control.checked;
    else object[key] = control.value;
    document.querySelector('#quickWidth').textContent = `${object.width || 2}px`;
    savePrototypeState(); renderToolSpecificSettings(object); renderWorkspace();
  }));
}

function renderCoordinateSettings(object) {
  const host = document.querySelector('#coordinateSettings');
  const b = object.b || object.a;
  const fibLabel = object.type === 'fib';
  host.innerHTML = `<div class="coordinate-rows">
    <div class="coordinate-row"><b>#1${fibLabel ? '（价格，K线）' : ''}</b><input type="number" step="0.01" data-coordinate-point="a" data-coordinate-field="value" value="${object.a.value.toFixed(2)}" aria-label="#1 价格"><input type="number" step="1" min="1" max="${bars.length}" data-coordinate-point="a" data-coordinate-field="index" value="${Math.round(object.a.index) + 1}" aria-label="#1 K线"></div>
    <div class="coordinate-row"><b>#2${fibLabel ? '（价格，K线）' : ''}</b><input type="number" step="0.01" data-coordinate-point="b" data-coordinate-field="value" value="${b.value.toFixed(2)}" aria-label="#2 价格"><input type="number" step="1" min="1" max="${bars.length}" data-coordinate-point="b" data-coordinate-field="index" value="${Math.round(b.index) + 1}" aria-label="#2 K线"></div>
  </div>`;
  host.querySelectorAll('[data-coordinate-field]').forEach(control => control.addEventListener('input', () => {
    rememberObjects();
    const pointKey = control.dataset.coordinatePoint;
    if (!object[pointKey]) object[pointKey] = { ...object.a };
    object[pointKey][control.dataset.coordinateField] = control.dataset.coordinateField === 'index'
      ? clamp(Number(control.value) - 1, 0, bars.length - 1)
      : Number(control.value);
    if (control.dataset.coordinateField === 'index') delete object[pointKey].date;
    savePrototypeState(); renderWorkspace();
  }));
}

function renderVisibilitySettings(object) {
  const host = document.querySelector('#visibilitySettings');
  if (object.type !== 'fib') {
    host.innerHTML = `<p class="visibility-note">在所选周期中显示</p><div class="visibility-grid"><label><input type="checkbox">分钟</label><label><input type="checkbox">小时</label><label><input type="checkbox" checked>天</label><label><input type="checkbox" checked>周</label><label><input type="checkbox" checked>月</label></div>`;
    return;
  }
  ensureFibSettings(object);
  const labels = { ticks: 'ticks', seconds: '秒', minutes: '分钟', hours: '小时', days: '日', weeks: '周', months: '个月', ranges: 'Ranges' };
  host.innerHTML = `<div class="fib-visibility-list">${Object.entries(labels).map(([key, label]) => {
    const setting = object.fibVisibility[key];
    const bounds = setting.min === undefined ? '' : `<input type="number" data-fib-visibility-bound="min" value="${setting.min}" ${setting.enabled ? '' : 'disabled'} aria-label="${label}最小值"><span class="visibility-range-track"></span><input type="number" data-fib-visibility-bound="max" value="${setting.max}" ${setting.enabled ? '' : 'disabled'} aria-label="${label}最大值">`;
    return `<div class="fib-visibility-row" data-fib-visibility-key="${key}"><label><input type="checkbox" data-fib-visibility-enabled ${setting.enabled ? 'checked' : ''}>${label}</label>${bounds}</div>`;
  }).join('')}</div>`;
  host.querySelectorAll('[data-fib-visibility-key]').forEach(row => {
    const setting = object.fibVisibility[row.dataset.fibVisibilityKey];
    row.querySelector('[data-fib-visibility-enabled]').addEventListener('change', event => {
      rememberObjects(); setting.enabled = event.target.checked; savePrototypeState(); renderVisibilitySettings(object); renderWorkspace();
    });
    row.querySelectorAll('[data-fib-visibility-bound]').forEach(control => control.addEventListener('input', () => {
      rememberObjects(); setting[control.dataset.fibVisibilityBound] = Number(control.value); savePrototypeState(); renderWorkspace();
    }));
  });
}

function openObjectSettings() {
  const object = objectAtSelection();
  if (!object) { showToast('先在图上单击一个人工标绘'); return; }
  objectSettingsSnapshot = structuredClone(object);
  document.querySelector('#objectModal').classList.toggle('fib-object-modal', object.type === 'fib');
  document.querySelector('#objectModalTitle').textContent = toolNames[object.type] || '图形设置';
  document.querySelectorAll('#objectTabs button').forEach(button => button.classList.toggle('active', button.dataset.tab === 'style'));
  document.querySelectorAll('.object-tab-panel').forEach(panel => panel.classList.toggle('active', panel.dataset.panel === 'style'));
  renderToolSpecificSettings(object); renderCoordinateSettings(object); renderVisibilitySettings(object);
  openWorkspaceModal('objectModal');
}

function closeObjectSettings(revert = false) {
  if (revert && objectSettingsSnapshot) {
    const index = userObjects.findIndex(item => item.id === objectSettingsSnapshot.id);
    if (index >= 0) userObjects[index] = structuredClone(objectSettingsSnapshot);
  }
  objectSettingsSnapshot = null; closeWorkspaceModal(); savePrototypeState(); updateObjectTree(); renderWorkspace();
}

document.querySelector('#quickSettings').addEventListener('click', openObjectSettings);
document.querySelector('#quickDelete').addEventListener('click', () => {
  if (!selectedId) return;
  rememberObjects();
  userObjects = userObjects.filter(item => item.id !== selectedId);
  selectedId = null; selected = false;
  document.querySelector('#selectedObject').classList.remove('visible');
  savePrototypeState(); updateObjectTree(); renderWorkspace(); showToast('图形已删除');
});
document.querySelector('#quickColor').addEventListener('click', openObjectSettings);
document.querySelector('#quickWidth').addEventListener('click', openObjectSettings);
document.querySelectorAll('#objectTabs button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('#objectTabs button').forEach(item => item.classList.toggle('active', item === button));
  document.querySelectorAll('.object-tab-panel').forEach(panel => panel.classList.toggle('active', panel.dataset.panel === button.dataset.tab));
}));

function updateObjectTree() {
  const tree = document.querySelector('.object-tree');
  const escaped = value => String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  tree.innerHTML = `
    <label><input type="checkbox" checked disabled><span class="tree-eye">◉</span><b>策略证据</b><small>只读</small></label>
    <label><input type="checkbox" checked disabled><span class="tree-eye">◉</span><b>EMA 20</b><small>指标</small></label>
    ${userObjects.map(item => `<label data-object-id="${item.id}" class="${item.id === selectedId ? 'selected' : ''}"><input type="checkbox" ${item.visible === false ? '' : 'checked'}><span class="tree-eye">${item.visible === false ? '○' : '◉'}</span><b>${toolNames[item.type] || item.type}</b><small>已保存</small></label>`).join('')}
    ${chartAlerts.map(alert => `<label><input type="checkbox" ${alert.enabled === false ? '' : 'checked'} disabled><span class="tree-eye">${alert.enabled === false ? '○' : '◉'}</span><b>${escaped(alert.name)}</b><small>本机</small></label>`).join('')}`;
  tree.querySelectorAll('[data-object-id]').forEach(label => {
    label.addEventListener('click', event => {
      const object = userObjects.find(item => item.id === label.dataset.objectId); if (!object) return;
      if (event.target.matches('input')) object.visible = event.target.checked;
      selectedId = object.id; savePrototypeState(); updateObjectTree(); renderWorkspace();
    });
    label.addEventListener('dblclick', () => { selectedId = label.dataset.objectId; openObjectSettings(); });
  });
}

document.querySelectorAll('.timeframes .disabled').forEach(button => button.addEventListener('click', () => showToast(button.dataset.unavailable)));
function switchProductionTimeframe(timeframe, persist = true) {
  const target = chartBoot.series?.[timeframe];
  if (!chartBoot.production || !target?.length || timeframe === activeTimeframeUnit) return false;
  userObjects.forEach(object => chartCoordinates.capture(object, bars));
  bars.splice(0, bars.length, ...structuredClone(target));
  userObjects.forEach(object => chartCoordinates.projectObject(object, bars, timeframe));
  // Undo/redo snapshots carry canonical dates too; project them before restoring an edit.
  [undoStack, redoStack].forEach(stack => stack.forEach(objects =>
    objects.forEach(object => chartCoordinates.projectObject(object, bars, timeframe))));
  ema.splice(0, ema.length);
  bars.forEach((row, index) => ema.push(index ? row.close * (2 / 21) + ema[index - 1] * (19 / 21) : row.close));
  activeTimeframeUnit = timeframe; replayIndex = bars.length - 1;
  viewCount = Math.min(DEFAULT_VISIBLE_BARS, bars.length); viewOffset = 0; priceZoom = 1; priceCenterOffset = 0;
  document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.classList.toggle('active', button.dataset.timeframe === timeframe));
  const period = timeframe === 'weekly' ? '1周' : timeframe === 'monthly' ? '1月' : '1天';
  document.querySelector('.instrument-title b').textContent = `${chartBoot.name} · ${period} · ${String(chartBoot.symbol || '').startsWith('sh.') ? 'SSE' : 'SZSE'}`;
  renderWorkspace(); if (persist) savePrototypeState(); return true;
}

document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.addEventListener('click', () => {
  if (chartBoot.production) {
    if (button.dataset.timeframe === activeTimeframeUnit) return;
    if (switchProductionTimeframe(button.dataset.timeframe)) showToast(`已切换到${button.textContent}线真实聚合行情`);
    else showToast('当前入口没有该周期的真实底层行情');
    return;
  }
  document.querySelectorAll('.timeframes button').forEach(item => item.classList.remove('active')); button.classList.add('active');
  activeTimeframeUnit = button.dataset.timeframe; renderWorkspace(); showToast(`${button.textContent}线原型状态`);
}));
document.querySelector('#openAlert').addEventListener('click', () => openWorkspaceModal('alertModal'));
document.querySelector('#openObject').addEventListener('click', openObjectSettings);
document.querySelector('#removeObjects').addEventListener('click', () => openWorkspaceModal('removeModal'));
document.querySelectorAll('.close-modal').forEach(button => button.addEventListener('click', closeWorkspaceModal));
document.querySelector('#closeObjectSettings').addEventListener('click', () => closeObjectSettings(false));
document.querySelector('#cancelObjectSettings').addEventListener('click', () => closeObjectSettings(true));
document.querySelector('#confirmObjectSettings').addEventListener('click', () => closeObjectSettings(false));
document.querySelector('#objectTemplate').addEventListener('click', () => {
  const object = objectAtSelection();
  if (!object || object.type !== 'fib') { showToast('当前工具暂无可保存模板'); return; }
  saveFibTemplate(ensureFibSettings(object));
  showToast('当前斐波那契设置已保存为绘图模板');
});
document.querySelector('#modalBackdrop').addEventListener('click', event => {
  if (event.target.id !== 'modalBackdrop') return;
  if (document.querySelector('#objectModal').classList.contains('show')) closeObjectSettings(true);
  else closeWorkspaceModal();
});
document.querySelector('#createAlert').addEventListener('click', async () => {
  const value = Number(document.querySelector('#alertPrice').value);
  const name = document.querySelector('#alertName').value.trim();
  if (!Number.isFinite(value) || value <= 0) { showToast('请输入有效提醒价格'); return; }
  try {
    if (chartBoot.production && window.pywebview?.api?.create_price_alert) {
      const timeframe = activeTimeframeUnit;
      const pending = bridgeSaveChain.then(() =>
        window.pywebview.api.create_price_alert(value, 'cross_up', name, 'price', timeframe));
      bridgeSaveChain = pending.then(result => {
        bridgeRevision = Number(result.revision);
        if (Array.isArray(result.alerts)) chartAlerts = result.alerts;
        else chartAlerts.push(result.alert);
        updateObjectTree();
      }).catch(error => { showToast(`提醒未保存：${error.message || error}`); });
      await pending;
    }
    closeWorkspaceModal(); showToast(`提醒已保存：收盘价向上穿越 ${value.toFixed(2)}`);
  } catch (error) { showToast(`提醒未保存：${error.message || error}`); }
});

document.querySelector('#toggleDrawings').addEventListener('click', () => {
  drawingsVisible = !drawingsVisible; renderWorkspace(); showToast(drawingsVisible ? '已显示所有图形' : '已隐藏所有图形');
});
document.querySelector('#togglePanel').addEventListener('click', () => { document.querySelector('#rightPanel').classList.toggle('collapsed'); setTimeout(renderWorkspace, 190); });
document.querySelectorAll('.remove-option').forEach((button, index) => button.addEventListener('click', () => {
  rememberObjects();
  if (index === 0 || index === 2) userObjects = [];
  if (index === 1 || index === 2) showToast(index === 1 ? 'EMA 20 已从原型图层隐藏' : '人工图形与指标已移除');
  selectedId = null; document.querySelector('#selectedObject').classList.remove('visible'); closeWorkspaceModal(); savePrototypeState(); updateObjectTree(); renderWorkspace();
}));

const replaySpeeds = [10, 7, 5, 3, 1, .5, .3, .2, .1];

function dateAtOrBefore(value) {
  let index = bars.findIndex(row => row.date === value);
  if (index >= 0) return index;
  index = bars.findLastIndex(row => row.date <= value);
  return clamp(index < 0 ? 0 : index, 0, bars.length - 1);
}

function renderReplayCalendar() {
  const host = document.querySelector('#replayCalendar');
  const input = document.querySelector('#replayStartDate');
  const year = replayCalendarCursor.getFullYear();
  const month = replayCalendarCursor.getMonth();
  document.querySelector('#calendarMonth').textContent = `${year}年${month + 1}月`;
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  const mondayOffset = (first.getDay() + 6) % 7;
  const availableDates = new Set(bars.map(row => row.date));
  const cells = ['一', '二', '三', '四', '五', '六', '日'].map(day => `<span>${day}</span>`);
  for (let blank = 0; blank < mondayOffset; blank += 1) cells.push('<span></span>');
  for (let day = 1; day <= days; day += 1) {
    const date = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const disabled = !availableDates.has(date);
    cells.push(`<button data-replay-date="${date}" class="${input.value === date ? 'active' : ''}" ${disabled ? 'disabled' : ''}>${day}</button>`);
  }
  host.innerHTML = cells.join('');
  host.querySelectorAll('[data-replay-date]').forEach(button => button.addEventListener('click', () => {
    input.value = button.dataset.replayDate; renderReplayCalendar();
  }));
  document.querySelector('#calendarPrevious').disabled = `${year}-${String(month + 1).padStart(2, '0')}` <= bars[0].date.slice(0, 7);
  document.querySelector('#calendarNext').disabled = `${year}-${String(month + 1).padStart(2, '0')}` >= bars.at(-1).date.slice(0, 7);
}

function openReplayDatePicker() {
  const savedValue = localStorage.getItem(`${tvStateKey}-replay-index`);
  const savedIndex = savedValue === null ? Number.NaN : Number(savedValue);
  const preferredIndex = replayActive ? replayIndex : Number.isFinite(savedIndex) && savedIndex >= 0 ? savedIndex : Math.max(0, bars.length - 35);
  const date = bars[clamp(preferredIndex, 0, bars.length - 1)].date;
  document.querySelector('#replayStartDate').min = bars[0].date;
  document.querySelector('#replayStartDate').max = bars.at(-1).date;
  document.querySelector('#replayStartDate').value = date;
  replayCalendarCursor = new Date(`${date}T00:00:00`);
  renderReplayCalendar(); openWorkspaceModal('replayStartModal');
}

function updateReplayUi() {
  document.querySelector('#replayDate').textContent = bars[replayIndex].date;
  document.querySelector('#replayPlay').textContent = replayPlaying ? 'Ⅱ' : '▶';
  document.querySelector('#replayPlay').classList.toggle('playing', replayPlaying);
  document.querySelector('#replayPlay').title = replayPlaying ? '暂停 Shift +' : '播放 Shift +';
  document.querySelector('#replaySpeed').textContent = `${replaySpeedValue}x`;
  updateReadout(bars[replayIndex]);
}

function stopReplayTimer() {
  clearInterval(replayTimer); replayTimer = null; replayPlaying = false; updateReplayUi();
}

function stepReplay() {
  if (replayIndex >= bars.length - 1) { stopReplayTimer(); showToast('已到最后一根K线'); return; }
  replayIndex += 1; viewOffset = 0; updateReplayUi(); renderWorkspace();
}

function startReplayTimer() {
  clearInterval(replayTimer); replayPlaying = true; updateReplayUi();
  replayTimer = setInterval(stepReplay, Math.max(90, Math.round(1000 / replaySpeedValue)));
}

function enterReplay(index) {
  if (!replayActive) preReplayView = { count: viewCount, offset: viewOffset, priceZoom, priceCenterOffset };
  replayActive = true; replayIndex = clamp(index, 0, bars.length - 1); replayPlaying = false; viewOffset = 0;
  viewCount = Math.min(DEFAULT_VISIBLE_BARS, replayIndex + 1);
  replayBar.classList.add('visible');
  document.querySelector('#replayWatermark').classList.add('visible');
  document.querySelector('#replayModeBadge').classList.add('visible');
  document.querySelector('#startReplay').classList.add('active-box');
  document.querySelector('#rightPanel').classList.add('replay-context');
  chartNavControls.classList.remove('visible');
  closeWorkspaceModal(); updateReplayUi(); renderWorkspace();
  showToast(`回放从 ${bars[replayIndex].date} 开始；未来K线已隐藏`);
}

function leaveReplay() {
  stopReplayTimer();
  if (document.querySelector('#saveReplayChoice').checked) localStorage.setItem(`${tvStateKey}-replay-index`, String(replayIndex));
  else localStorage.removeItem(`${tvStateKey}-replay-index`);
  replayActive = false; replayIndex = bars.length - 1;
  replayBar.classList.remove('visible');
  document.querySelector('#replayWatermark').classList.remove('visible');
  document.querySelector('#replayModeBadge').classList.remove('visible');
  document.querySelector('#startReplay').classList.remove('active-box');
  document.querySelector('#rightPanel').classList.remove('replay-context');
  if (preReplayView) {
    viewCount = preReplayView.count; viewOffset = preReplayView.offset;
    priceZoom = preReplayView.priceZoom; priceCenterOffset = preReplayView.priceCenterOffset;
  }
  updateReadout(bars.at(-1)); closeWorkspaceModal(); renderWorkspace(); savePrototypeState(); showToast('已退出K线回放');
}

document.querySelector('#startReplay').addEventListener('click', openReplayDatePicker);
document.querySelector('#replayDatePicker').addEventListener('click', openReplayDatePicker);
document.querySelector('#replayModeBadge').addEventListener('click', openReplayDatePicker);
document.querySelector('#replayFirstDate').addEventListener('click', () => {
  document.querySelector('#replayStartDate').value = bars[0].date;
  replayCalendarCursor = new Date(`${bars[0].date}T00:00:00`); renderReplayCalendar();
});
document.querySelector('#replayStartDate').addEventListener('change', event => {
  replayCalendarCursor = new Date(`${event.target.value}T00:00:00`); renderReplayCalendar();
});
document.querySelector('#calendarPrevious').addEventListener('click', () => { replayCalendarCursor.setMonth(replayCalendarCursor.getMonth() - 1); renderReplayCalendar(); });
document.querySelector('#calendarNext').addEventListener('click', () => { replayCalendarCursor.setMonth(replayCalendarCursor.getMonth() + 1); renderReplayCalendar(); });
document.querySelector('#confirmReplayStart').addEventListener('click', () => enterReplay(dateAtOrBefore(document.querySelector('#replayStartDate').value)));
document.querySelector('#replayPlay').addEventListener('click', () => replayPlaying ? stopReplayTimer() : startReplayTimer());
document.querySelector('#replayStep').addEventListener('click', () => { stopReplayTimer(); stepReplay(); });
document.querySelector('#replayToLatest').addEventListener('click', () => { stopReplayTimer(); replayIndex = bars.length - 1; viewOffset = 0; updateReplayUi(); renderWorkspace(); showToast('已更新到最后一根K线'); });
document.querySelector('#replayInterval').addEventListener('click', () => showToast('当前按日K逐根更新'));
document.querySelector('#exitReplay').addEventListener('click', () => openWorkspaceModal('replayExitModal'));
document.querySelector('#leaveReplay').addEventListener('click', leaveReplay);

const replaySpeedMenu = document.querySelector('#replaySpeedMenu');
replaySpeedMenu.innerHTML = replaySpeeds.map(speed => `<button data-replay-speed="${speed}" class="${speed === 1 ? 'active' : ''}"><b>${speed}x</b><small>${speed >= 1 ? `每1秒更新${speed}次` : `每${Math.round(1 / speed)}秒更新1次`}</small></button>`).join('');
document.querySelector('#replaySpeed').addEventListener('click', event => { event.stopPropagation(); replaySpeedMenu.classList.toggle('visible'); });
replaySpeedMenu.querySelectorAll('[data-replay-speed]').forEach(button => button.addEventListener('click', () => {
  replaySpeedValue = Number(button.dataset.replaySpeed);
  replaySpeedMenu.querySelectorAll('button').forEach(item => item.classList.toggle('active', item === button));
  replaySpeedMenu.classList.remove('visible');
  if (replayPlaying) startReplayTimer(); else updateReplayUi();
}));
document.addEventListener('click', event => { if (!event.target.closest('.replay-speed-wrap')) replaySpeedMenu.classList.remove('visible'); });
async function copyCurrentChartSnapshot() {
  const preview = canvas.toDataURL('image/png');
  document.querySelector('#snapshotImage').src = preview;
  try {
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob || !navigator.clipboard || typeof ClipboardItem === 'undefined') throw new Error('clipboard unavailable');
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    showToast('当前缩放范围的完整图表已复制到剪贴板');
  } catch (_) {
    openWorkspaceModal('snapshotModal');
    showToast('浏览器未开放图片剪贴板；可在预览中保存 PNG');
  }
}
document.querySelector('#snapshot').addEventListener('click', copyCurrentChartSnapshot);
document.querySelector('#downloadSnapshot').addEventListener('click', () => {
  const link = document.createElement('a'); link.download = `Aplus-${chartBoot.displaySymbol || '002259'}-${String(bars.at(-1).date).slice(0, 10)}.png`; link.href = canvas.toDataURL('image/png'); link.click(); closeWorkspaceModal();
});
document.querySelectorAll('.candidate').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.candidate').forEach(item => item.classList.remove('active')); button.classList.add('active'); if (!button.textContent.includes('升达林业')) showToast('静态原型仅嵌入 002259 真实日线');
}));
document.querySelectorAll('.panel-tabs button[data-panel]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.panel-tabs button[data-panel]').forEach(item => item.classList.toggle('active', item === button));
  document.querySelector('.panel-context span').textContent = button.dataset.panel === 'watch' ? '我的自选 · 3' : 'GAP H2 · 2026-09-30';
  showToast(button.dataset.panel === 'watch' ? '已切换到自选标的，主图保持不变' : '已切换到策略匹配，主图保持可操作');
}));

document.querySelector('#undoAction').addEventListener('click', () => {
  if (!undoStack.length) { showToast('没有可撤销的图形操作'); return; }
  redoStack.push(structuredClone(userObjects)); userObjects = undoStack.pop(); selectedId = null;
  document.querySelector('#selectedObject').classList.remove('visible'); savePrototypeState(); updateUndoButtons(); updateObjectTree(); renderWorkspace(); showToast('已撤销图形操作');
});
document.querySelector('#redoAction').addEventListener('click', () => {
  if (!redoStack.length) { showToast('没有可重做的图形操作'); return; }
  undoStack.push(structuredClone(userObjects)); userObjects = redoStack.pop(); selectedId = null;
  document.querySelector('#selectedObject').classList.remove('visible'); savePrototypeState(); updateUndoButtons(); updateObjectTree(); renderWorkspace(); showToast('已重做图形操作');
});

function applyProductionContext() {
  if (!chartBoot.production) return;
  const latest = chartBoot.latest || bars.at(-1);
  const display = chartBoot.displaySymbol || chartBoot.symbol;
  const market = String(chartBoot.symbol || '').startsWith('sh.') ? '沪' : '深';
  document.title = `A++ 图表工作区 · ${chartBoot.name} ${display}`;
  document.querySelector('.symbol-pill b').textContent = display;
  document.querySelector('.instrument-title b').textContent = `${chartBoot.name} · ${chartBoot.timeframe === 'weekly' ? '1周' : chartBoot.timeframe === 'monthly' ? '1月' : '1天'} · ${market === '沪' ? 'SSE' : 'SZSE'}`;
  document.querySelector('.exchange-dot').textContent = market;
  ['open', 'high', 'low', 'close'].forEach(key => { document.querySelector(`#${key}Value`).textContent = Number(latest[key]).toFixed(2); });
  const change = bars.length > 1 ? Number(latest.close) - Number(bars.at(-2).close) : 0;
  const pct = bars.length > 1 && Number(bars.at(-2).close) ? change / Number(bars.at(-2).close) * 100 : 0;
  const changeNode = document.querySelector('.ohlc .positive');
  changeNode.textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)} (${change >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
  changeNode.className = change >= 0 ? 'positive' : 'negative';
  document.querySelector('.quote.sell b').textContent = (Number(latest.close) - .01).toFixed(2);
  document.querySelector('.quote.buy b').textContent = Number(latest.close).toFixed(2);
  document.querySelector('.panel-context span').textContent = `${chartBoot.contextTitle} · ${String(latest.date).slice(0, 10)}`;
  document.querySelector('.panel-tabs [data-panel="strategy"]').textContent = chartBoot.contextTitle;
  document.querySelector('.candidate-list').innerHTML = `<button class="candidate active"><span class="badge ${market === '沪' ? 'sh' : 'sz'}">${market}</span><span class="candidate-name"><b></b><small></small></span><span class="candidate-price"><b></b><small></small></span></button>`;
  const candidate = document.querySelector('.candidate-list .candidate');
  candidate.querySelector('.candidate-name b').textContent = chartBoot.name;
  candidate.querySelector('.candidate-name small').textContent = `${display} · ${chartBoot.strategyType ? chartBoot.strategyType.replace('STRATEGY_', '') : chartBoot.contextTitle}`;
  candidate.querySelector('.candidate-price b').textContent = Number(latest.close).toFixed(2);
  candidate.querySelector('.candidate-price small').textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
  candidate.querySelector('.candidate-price small').className = pct >= 0 ? 'positive' : 'negative';
  const evidence = chartBoot.evidence || {};
  const entry = Number(evidence.entry_plan_price ?? evidence.entry);
  const stop = Number(evidence.sl1_plan_price ?? evidence.stop);
  const target = Number(evidence.mm_target ?? evidence.target);
  const card = document.querySelector('.decision-card');
  if ([entry, stop, target].every(Number.isFinite)) {
    card.querySelector('.decision-title span').textContent = chartBoot.contextKind === 'watch' ? '关注计划证据' : '次日挂单机会';
    card.querySelector('.decision-title b').textContent = evidence.pending_state === 'PENDING' ? '待挂' : (evidence.pending_state || '只读');
    const values = card.querySelectorAll('dd');
    [entry, stop, target].forEach((value, index) => { values[index].textContent = value.toFixed(2); });
    const risk = entry > stop ? (entry - stop) / entry * 100 : Number(evidence.initial_risk_pct || 0);
    const reward = entry > stop ? (target - entry) / (entry - stop) : Number(evidence.mm_r_multiple || 0);
    const spans = card.querySelectorAll('.risk-line span');
    spans[0].textContent = `风险 ${risk.toFixed(2)}%`; spans[1].textContent = `收益 ${reward.toFixed(2)}R`;
  } else if ((chartBoot.holdingLevels || []).length) {
    card.querySelector('.decision-title span').textContent = chartBoot.contextTitle;
    card.querySelector('.decision-title b').textContent = '账本只读';
    card.querySelector('dl').innerHTML = chartBoot.holdingLevels.slice(0, 3).map(level => `<div><dt>${level.label}</dt><dd>${Number(level.value).toFixed(2)}</dd></div>`).join('');
    card.querySelector('.risk-line').textContent = '实际成交与参考水平来自本机账本';
  } else {
    card.querySelector('.decision-title span').textContent = chartBoot.contextTitle;
    card.querySelector('.decision-title b').textContent = '只读';
    card.querySelector('dl').innerHTML = '<div><dt>行情</dt><dd>真实日线</dd></div>';
    card.querySelector('.risk-line').textContent = '当前没有可显示的计划价位';
  }
  const alertSymbol = document.querySelector('#alertModal .select-row b');
  if (alertSymbol) alertSymbol.textContent = `${chartBoot.name} · ${display}`;
  document.querySelector('#alertPrice').value = Number(latest.close).toFixed(2);
  document.querySelector('#alertName').value = `${display} 向上穿越 ${Number(latest.close).toFixed(2)}`;
  document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.classList.toggle('active', button.dataset.timeframe === chartBoot.timeframe));
  activeTimeframeUnit = chartBoot.timeframe;
  const restoredTimeframe = initialPrototypeState.timeframe || chartBoot.timeframe;
  if (restoredTimeframe !== chartBoot.timeframe && switchProductionTimeframe(restoredTimeframe, false)) {
    const savedView = initialPrototypeState.view || {};
    viewCount = clamp(Number(savedView.count) || DEFAULT_VISIBLE_BARS, 18, bars.length);
    viewOffset = clamp(Number(savedView.offset) || 0, 0, Math.max(0, bars.length - viewCount));
    priceZoom = clamp(Number(savedView.priceZoom) || 1, .45, 7);
    priceCenterOffset = Number(savedView.priceCenterOffset) || 0;
  }
}

window.addEventListener('keydown', event => {
  if (replayActive && event.shiftKey && (event.key === '+' || event.key === '=')) {
    event.preventDefault(); replayPlaying ? stopReplayTimer() : startReplayTimer(); return;
  }
  if (event.key === 'Escape') {
    draftAnchor = null; draftCursor = null; draftPoints = []; activeDrawingTool = null; hideZoomSelection(); closeWorkspaceModal(); startTool('cross', document.querySelector('[data-tool="cross"]'));
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId && !document.querySelector('#modalBackdrop').classList.contains('show')) document.querySelector('#quickDelete').click();
});

const workspaceResize = new ResizeObserver(renderWorkspace);
workspaceResize.observe(wrap);
applyProductionContext();
updateObjectTree(); renderWorkspace();
updateUndoButtons();

const initialState = new URLSearchParams(location.search).get('state');
if (initialState === 'alert') openWorkspaceModal('alertModal');
if (initialState === 'object') { selectedId = userObjects[1]?.id || userObjects[0]?.id; selectObject(selectedId, wrap.clientWidth * .55, 220); openObjectSettings(); }
if (initialState === 'replay') document.querySelector('#startReplay').click();
if (initialState === 'snapshot') document.querySelector('#snapshot').click();
