/* TradingView interaction study: local-only prototype behavior. */
const tvStateKey = 'aplus-tv-prototype-002259-v2';
const toolNames = {
  fib: '斐波那契回撤', long: '多头仓位', ray: '水平射线', trend: '趋势线',
  short: '空头仓位', rectangle: '矩形', up: '向上箭头', down: '向下箭头',
  arrow: '箭头', path: '多段折线', channel: '平行通道', text: '文字', ruler: '价格范围'
};
const onePointTools = new Set(['ray', 'up', 'down', 'text']);
const defaultObjects = [
  { id: 'trend-1', type: 'trend', a: { index: 20, value: 3.43 }, b: { index: 70, value: 5.34 }, color: '#f23645', width: 1, dash: 'solid', visible: true },
  { id: 'fib-1', type: 'fib', a: { index: 21, value: 3.14 }, b: { index: 60, value: 4.98 }, color: '#4caf50', width: 1, dash: 'solid', visible: true },
  { id: 'channel-1', type: 'channel', a: { index: 64, value: 4.45 }, b: { index: 78, value: 5.45 }, color: '#21b9d6', width: 1, dash: 'solid', visible: true },
  { id: 'long-1', type: 'long', a: { index: 66, value: 4.76 }, b: { index: 78, value: 5.98 }, stop: 4.08, color: '#8d939c', width: 1, dash: 'solid', visible: true }
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
const initialPrototypeState = loadPrototypeState();
let userObjects = initialPrototypeState.objects || structuredClone(defaultObjects);
let undoStack = [];
let redoStack = [];
if (initialPrototypeState.view) {
  viewCount = clamp(Number(initialPrototypeState.view.count) || bars.length, 18, bars.length);
  viewOffset = clamp(Number(initialPrototypeState.view.offset) || 0, 0, bars.length - viewCount);
  priceZoom = clamp(Number(initialPrototypeState.view.priceZoom) || 1, .45, 7);
  priceCenterOffset = Number(initialPrototypeState.view.priceCenterOffset) || 0;
}

function loadPrototypeState() {
  try { return JSON.parse(localStorage.getItem(tvStateKey) || '{}'); } catch (_) { return {}; }
}

function savePrototypeState() {
  try {
    const toolbar = document.querySelector('#favoriteTools');
    localStorage.setItem(tvStateKey, JSON.stringify({
      objects: userObjects,
      toolbar: { left: toolbar.style.left, top: toolbar.style.top },
      view: { count: viewCount, offset: viewOffset, priceZoom, priceCenterOffset }
    }));
  } catch (_) { /* local files can disable storage; the prototype still works. */ }
}

function rememberObjects() {
  undoStack.push(structuredClone(userObjects));
  if (undoStack.length > 30) undoStack.shift();
  redoStack = [];
  updateUndoButtons();
}

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
  const candleWidth = plotWidth / rows.length;
  const xGlobal = globalIndex => xFor(globalIndex - visible.begin, left, candleWidth);
  canvas._view = { rows, begin: visible.begin, end: visible.end, left, right, top, bottom, width, height, plotWidth, plotHeight, y, low, high, candleWidth, xGlobal };
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
    drawStrategyEvidence(ctx, canvas._view);
    userObjects.filter(item => item.visible !== false).forEach(item => drawUserObject(ctx, canvas._view, item));
    if (draftAnchor && draftCursor) drawUserObject(ctx, canvas._view, {
      id: 'draft', type: activeDrawingTool, a: draftAnchor, b: draftCursor,
      points: activeDrawingTool === 'path' ? [...draftPoints, draftCursor] : undefined,
      stop: draftAnchor.value - Math.abs(draftCursor.value - draftAnchor.value) * .55,
      color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true
    }, true);
  }
  drawLastPrice(ctx, rows.at(-1).close, y, width, right);
  document.querySelector('.ema-value').textContent = ema[Math.min(replayIndex, bars.length - 1)].toFixed(2);
}

function drawStrategyEvidence(ctx, view) {
  const { left, width, right, y, candleWidth, xGlobal } = view;
  const plotRight = width - right;
  const gapLeft = Math.max(left, xGlobal(57) - candleWidth * .55);
  const gapRight = Math.min(plotRight, xGlobal(64) + candleWidth * .55);
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
  callout(ctx, 'BO', xGlobal(57), y(4.56), xGlobal(54), y(4.83));
  callout(ctx, 'H1', xGlobal(58), y(4.45), xGlobal(55), y(4.72));
  callout(ctx, 'H2', xGlobal(70), y(5.19), xGlobal(67), y(5.48), true);
  callout(ctx, 'Gap Floor', gapLeft, y(4.38), gapLeft - 32, y(4.18));
  registerRect('strategy-evidence', gapLeft, y(4.56), gapRight, y(4.38));
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
    const levels = [0, .236, .382, .5, .618, .786, 1];
    levels.forEach(level => {
      const value = object.a.value + (b.value - object.a.value) * level;
      const py = y(value);
      ctx.beginPath(); ctx.moveTo(ax, py); ctx.lineTo(bx, py); ctx.stroke();
      ctx.font = '11px Segoe UI'; ctx.textAlign = 'right'; ctx.fillText(`${level}`, ax - 5, py + 4);
      registerSegment(object.id, ax, py, bx, py);
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
    color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true
  };
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
  const object = { id: `path-${Date.now()}`, type: 'path', a: points[0], b: points.at(-1), points, color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true };
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
    if (handle === 'a') object.a = snapped;
    else if (handle === 'b') object.b = snapped;
    else if (handle === 'stop') object.stop = snapped.value;
    else if (handle.startsWith('point:')) {
      const index = Number(handle.split(':')[1]); object.points[index] = snapped;
      object.a = object.points[0]; object.b = object.points.at(-1);
    }
    return;
  }
  const deltaIndex = point.index - start.index;
  const deltaValue = point.value - start.value;
  const movePoint = value => ({ index: value.index + deltaIndex, value: value.value + deltaValue });
  object.a = movePoint(original.a);
  object.b = movePoint(original.b || original.a);
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
    viewOffset = clamp(gesture.startOffset + Math.round(-dx / candle), 0, Math.max(0, bars.length - viewCount));
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

function renderToolSpecificSettings(object) {
  const host = document.querySelector('#toolSpecificSettings');
  let extra = '';
  if (object.type === 'fib') extra = `<div class="setting-section-title">斐波那契级别</div><div class="levels-table"><label><input type="checkbox" checked><i style="--level:#26a69a"></i><span>0</span></label><label><input type="checkbox" checked><i style="--level:#787b86"></i><span>0.5</span></label><label><input type="checkbox" checked><i style="--level:#ab47bc"></i><span>1</span></label></div><div class="setting-row"><span>标签</span><label class="switch"><input type="checkbox" checked><i></i></label><span>价格与比例</span></div>`;
  else if (object.type === 'long' || object.type === 'short') extra = `<div class="setting-section-title">仓位工具</div><div class="setting-row"><span>利润区域</span><button class="color-input"><i style="background:#089981"></i>#089981</button><label class="switch"><input type="checkbox" checked><i></i></label></div><div class="setting-row"><span>止损区域</span><button class="color-input"><i style="background:#f23645"></i>#F23645</button><label class="switch"><input type="checkbox" checked><i></i></label></div><div class="setting-row"><span>显示</span><label><input type="checkbox" checked> 风险/回报比</label><label><input type="checkbox" checked> 数量</label></div>`;
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

function openObjectSettings() {
  const object = objectAtSelection();
  if (!object) { showToast('先在图上单击一个人工标绘'); return; }
  document.querySelector('#objectModalTitle').textContent = toolNames[object.type] || '图形设置';
  renderToolSpecificSettings(object);
  const pointDate = point => bars[clamp(Math.round(point.index), 0, bars.length - 1)].date;
  document.querySelector('#coordDate1').value = pointDate(object.a);
  document.querySelector('#coordPrice1').value = object.a.value.toFixed(2);
  document.querySelector('#coordDate2').value = pointDate(object.b || object.a);
  document.querySelector('#coordPrice2').value = (object.b || object.a).value.toFixed(2);
  openWorkspaceModal('objectModal');
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
  tree.innerHTML = `
    <label><input type="checkbox" checked disabled><span class="tree-eye">◉</span><b>策略证据</b><small>只读</small></label>
    <label><input type="checkbox" checked disabled><span class="tree-eye">◉</span><b>EMA 20</b><small>指标</small></label>
    ${userObjects.map(item => `<label data-object-id="${item.id}" class="${item.id === selectedId ? 'selected' : ''}"><input type="checkbox" ${item.visible === false ? '' : 'checked'}><span class="tree-eye">${item.visible === false ? '○' : '◉'}</span><b>${toolNames[item.type] || item.type}</b><small>已保存</small></label>`).join('')}
    <label><input type="checkbox" checked disabled><span class="tree-eye">◉</span><b>价格提醒 5.20</b><small>本机</small></label>`;
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
document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.timeframes button').forEach(item => item.classList.remove('active')); button.classList.add('active'); showToast(`${button.textContent}线原型状态`);
}));
document.querySelector('#openAlert').addEventListener('click', () => openWorkspaceModal('alertModal'));
document.querySelector('#openObject').addEventListener('click', openObjectSettings);
document.querySelector('#removeObjects').addEventListener('click', () => openWorkspaceModal('removeModal'));
document.querySelectorAll('.close-modal').forEach(button => button.addEventListener('click', closeWorkspaceModal));
document.querySelector('#modalBackdrop').addEventListener('click', event => { if (event.target.id === 'modalBackdrop') closeWorkspaceModal(); });
document.querySelector('#createAlert').addEventListener('click', () => { closeWorkspaceModal(); showToast('提醒已保存：收盘价向上穿越 5.20'); });

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

document.querySelector('#startReplay').addEventListener('click', () => { replayBar.classList.add('visible'); replayIndex = Number(document.querySelector('#replaySlider').value); viewOffset = 0; renderWorkspace(); });
document.querySelector('#exitReplay').addEventListener('click', () => { replayBar.classList.remove('visible'); replayIndex = bars.length - 1; renderWorkspace(); });
document.querySelector('#replaySlider').addEventListener('input', event => {
  replayIndex = Number(event.target.value); document.querySelector('#replayDate').textContent = bars[replayIndex].date; updateReadout(bars[replayIndex]); renderWorkspace();
});
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
  const link = document.createElement('a'); link.download = 'Aplus-002259-2026-09-30.png'; link.href = canvas.toDataURL('image/png'); link.click(); closeWorkspaceModal();
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

window.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    draftAnchor = null; draftCursor = null; draftPoints = []; activeDrawingTool = null; hideZoomSelection(); closeWorkspaceModal(); startTool('cross', document.querySelector('[data-tool="cross"]'));
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId && !document.querySelector('#modalBackdrop').classList.contains('show')) document.querySelector('#quickDelete').click();
});

const workspaceResize = new ResizeObserver(renderWorkspace);
workspaceResize.observe(wrap);
updateObjectTree(); renderWorkspace();
updateUndoButtons();

const initialState = new URLSearchParams(location.search).get('state');
if (initialState === 'alert') openWorkspaceModal('alertModal');
if (initialState === 'object') { selectedId = userObjects[1]?.id || userObjects[0]?.id; selectObject(selectedId, wrap.clientWidth * .55, 220); openObjectSettings(); }
if (initialState === 'replay') document.querySelector('#startReplay').click();
if (initialState === 'snapshot') document.querySelector('#snapshot').click();
