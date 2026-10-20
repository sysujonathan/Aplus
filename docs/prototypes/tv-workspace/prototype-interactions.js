/* TradingView interaction study and production A++ WebView behavior. */
const chartBoot = window.APLUS_CHART_BOOT || {};
const chartCoordinates = window.AplusChartCoordinates;
if(!chartBoot.production){
  Object.assign(chartBoot,{name:'升达林业',symbol:'sz.002259',
    series:{daily:structuredClone(bars),weekly:AplusChartIndicators.aggregate(bars,'weekly'),monthly:AplusChartIndicators.aggregate(bars,'monthly')},
    seriesCloseDates:window.AplusPrototypeCalendar?.closeDates||{}});
}
chartCoordinates.configure(chartBoot.production?chartBoot.axisDates:window.AplusPrototypeCalendar?.axisDates);
const tvStateKey = chartBoot.production
  ? `aplus-tv-${chartBoot.identity}`
  : 'aplus-tv-prototype-002259-v3';
const DEFAULT_VISIBLE_BARS = 204;
const RIGHT_MARGIN_BARS = 12;
let activeTimeframeUnit = 'daily';
const focusDate = chartBoot.evidence?.bo_date || '2026-06-22';
const focusStartIndex = Math.max(0, bars.findIndex(row => row.date === focusDate));
const focusIndex = index => {
  const daily=chartBoot.series?.daily||bars;
  const date=chartCoordinates.dateAt(daily,focusStartIndex+index,'daily');
  return chartCoordinates.project({date,value:1},bars,activeTimeframeUnit).index;
};
const toolNames = {
  fib: '斐波那契回撤', long: '多头仓位', ray: '水平射线', trend: '趋势线',
  short: '空头仓位', rectangle: '矩形', up: '向上箭头', down: '向下箭头',
  arrow: '箭头标记', path: '多段折线', channel: '平行通道', text: '文字', ruler: '价格范围'
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
const defaultObjects = chartBoot.production ? [] : [
  { id: 'trend-1', type: 'trend', a: { index: focusIndex(20), value: 3.43 }, b: { index: focusIndex(70), value: 5.34 }, color: '#f23645', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) },
  { id: 'fib-1', type: 'fib', a: { index: focusIndex(21), value: 3.14 }, b: { index: focusIndex(60), value: 4.98 }, color: '#4caf50', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) },
  { id: 'channel-1', type: 'channel', a: { index: focusIndex(64), value: 4.45 }, b: { index: focusIndex(78), value: 5.45 }, color: '#21b9d6', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) },
  { id: 'long-1', type: 'long', a: { index: focusIndex(66), value: 4.76 }, b: { index: focusIndex(78), value: 5.98 }, stop: 4.08, color: '#8d939c', width: 1, dash: 'solid', visible: true, createdAtIndex: focusIndex(70) }
];

let activeDrawingTool = null;
let draftAnchor = null;
let draftCursor = null;
let draftPoints = [];
let pendingDrawingText = null;
let selectedId = null;
let gesture = null;
let priceZoom = 1;
let priceCenterOffset = 0;
let magnetEnabled = true;
let hitRegions = [];
let staticHitRegions = [];
let hoverPoint = null;
let strategyLabelBoxes = [];
let replayActive = false;
let replayPlaying = false;
let replaySpeedValue = 1;
let replayIntervalUnit = 'auto';
let replayTimer = null;
let preReplayView = null;
let replayCutoffDate = null;
let objectSettingsSnapshot = null;
let objectSettingsHistory = null;
let replayCalendarCursor = new Date(`${bars[Math.max(0, bars.length - 35)].date}T00:00:00`);
const initialPrototypeState = loadPrototypeState();
let replayBookmarkDate = initialPrototypeState.replayDate || null;
let drawingTemplates = structuredClone(initialPrototypeState.templates || {});
let userObjects = chartBoot.production
  ? structuredClone(chartBoot.drawings || initialPrototypeState.objects || [])
  : initialPrototypeState.objects || structuredClone(defaultObjects);
let chartAlerts = structuredClone(chartBoot.alerts || []);
let editingAlertId = null;
let undoStack = [];
let redoStack = [];
let bridgeRevision = Number(chartBoot.revision || 0);
let bridgeSaveChain = Promise.resolve();
let bridgeSaveFailed = false;
let workspaceRenderFrame = null;
let workspaceRenderMode = null;
let viewSaveTimer = null;

function scheduleWorkspaceRender(mode = 'all') {
  workspaceRenderMode = workspaceRenderMode === 'all' || mode === 'all' ? 'all' : 'overlay';
  if (workspaceRenderFrame !== null) return;
  workspaceRenderFrame = requestAnimationFrame(() => {
    const next = workspaceRenderMode || 'all';
    workspaceRenderFrame = null; workspaceRenderMode = null;
    renderWorkspace(next);
  });
}

function flushWorkspaceRender(mode = null) {
  if (workspaceRenderFrame !== null) cancelAnimationFrame(workspaceRenderFrame);
  const next = mode || workspaceRenderMode;
  workspaceRenderFrame = null; workspaceRenderMode = null;
  if (next) renderWorkspace(next);
}

function scheduleViewStateSave() {
  clearTimeout(viewSaveTimer);
  viewSaveTimer = setTimeout(() => { viewSaveTimer = null; savePrototypeState(); }, 180);
}

function sizeCanvas(target, width, height, ratio) {
  const pixelWidth = Math.max(1, Math.round(width * ratio));
  const pixelHeight = Math.max(1, Math.round(height * ratio));
  if (target.width === pixelWidth && target.height === pixelHeight) return false;
  target.width = pixelWidth; target.height = pixelHeight;
  return true;
}
const indicatorDefaults = {ema:true, ema_length:20, ema_source:'close', ema_offset:0, ema_width:1.5, ema_alpha:1,
  ema_color:'#ff9800', chart_type:'candles', magnet:true, strategy_visible:true, drawings_visible:true,
  ema_daily:true, ema_weekly:true, ema_monthly:true, indicators_visible:true,
  lock_drawings:false, hide_scope:'drawings', favorites_visible:true, sidebar_open:false,
  ema_smoothing:'none', ema_smooth_length:14, ema_bb_deviation:2, ema_smooth_color:'#fdd835',
  ema_price_labels:true,ema_values:true,ema_inputs:true,ema_precision:'default',
  ema_timeframe:'chart',ema_wait_close:true,ema_smooth_visible:true,ema_bb_visible:true,
  ema_smooth_alpha:1,ema_bb_upper_alpha:1,ema_bb_lower_alpha:1,
  ema_bb_upper_color:'#26a69a',ema_bb_lower_color:'#26a69a',ema_bb_fill_color:'#26a69a',ema_bb_fill_alpha:.1};
let indicatorSettings = {...indicatorDefaults,
  ...Object.fromEntries(Object.entries(chartBoot.settings || {}).filter(([key]) => key in indicatorDefaults)),
  ...Object.fromEntries(Object.entries(initialPrototypeState.indicators || {}).filter(([key]) => key in indicatorDefaults))};
let indicatorUndoStack = [];
let indicatorRedoStack = [];
let emaSmoothing = {values:[],upper:[],lower:[]};
magnetEnabled = indicatorSettings.magnet !== false;
drawingsVisible = indicatorSettings.drawings_visible !== false;
viewCount = Math.min(DEFAULT_VISIBLE_BARS, bars.length);
viewOffset = 0;
if (initialPrototypeState.view) {
  viewCount = clamp(Number(initialPrototypeState.view.count) || DEFAULT_VISIBLE_BARS, 18, bars.length);
  const limits=panLimits();
  viewOffset = clamp(Number(initialPrototypeState.view.offset) || 0, limits.min, limits.max);
  priceZoom = clamp(Number(initialPrototypeState.view.priceZoom) || 1, .45, 7);
  priceCenterOffset = Number(initialPrototypeState.view.priceCenterOffset) || 0;
}

function loadPrototypeState() {
  if (chartBoot.production && chartBoot.state) return structuredClone(chartBoot.state);
  try { return JSON.parse(localStorage.getItem(tvStateKey) || '{}'); } catch (_) { return {}; }
}

function savePrototypeState(flushOnly = false) {
  try {
    if (flushOnly && typeof viewSaveTimer !== 'undefined' && viewSaveTimer !== null) {
      clearTimeout(viewSaveTimer); viewSaveTimer = null; flushOnly = false;
    }
    if(flushOnly){
      window.flushChartInlineEditor?.(true,true);
      // An idle second view must not write its stale document merely on close.
      // Actual edits already queued immutable snapshots through this function.
      if(!bridgeSaveFailed)return bridgeSaveChain;
    }
    window.flushChartInlineEditor?.(true,false);
    userObjects.forEach(object => chartCoordinates.capture(object, bars, activeTimeframeUnit));
    const toolbar = document.querySelector('#favoriteTools');
    const persistentView = replayActive && typeof preReplayView !== 'undefined' && preReplayView ? preReplayView :
      {count:viewCount,offset:viewOffset,priceZoom,priceCenterOffset,timeframe:activeTimeframeUnit};
    const state = {
      objects: structuredClone(userObjects),
      templates: structuredClone(drawingTemplates),
      toolbar: { left: toolbar.style.left, top: toolbar.style.top, userMoved: typeof favoriteToolbarMoved==='undefined'?false:favoriteToolbarMoved },
      view: {count:persistentView.count,offset:persistentView.offset,priceZoom:persistentView.priceZoom,priceCenterOffset:persistentView.priceCenterOffset},
      timeframe: persistentView.timeframe,
      replayDate: typeof replayBookmarkDate !== 'undefined' ? replayBookmarkDate : null
    };
    state.indicators = typeof indicatorSettings === 'undefined' ? {} :
      {...structuredClone(indicatorSettings), magnet:magnetEnabled, drawings_visible:drawingsVisible};
    try { localStorage.setItem(tvStateKey, JSON.stringify(state)); } catch (_) { /* Store remains authoritative. */ }
    if (chartBoot.production && window.pywebview?.api?.save_state) {
      document.querySelector('.save-state').innerHTML = '<span class="saved-dot"></span>正在保存到本机…';
      const saveArgs = [state, bridgeRevision];
      if (chartBoot.sessionToken != null) saveArgs.push(chartBoot.sessionToken);
      bridgeSaveChain = bridgeSaveChain.then(() => window.pywebview.api.save_state(...saveArgs))
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
  if (objectSettingsSnapshot && objectSettingsHistory) { objectSettingsHistory.dirty=true; return; }
  userObjects.forEach(object => chartCoordinates.capture(object, bars, activeTimeframeUnit));
  undoStack.push(structuredClone(userObjects));
  indicatorUndoStack.push(structuredClone(indicatorSettings));
  if (undoStack.length > 30) { undoStack.shift(); indicatorUndoStack.shift(); }
  redoStack = [];
  indicatorRedoStack = [];
  updateUndoButtons();
}

let closingWorkspace = false;
['pointerdown', 'keydown', 'input', 'change', 'click', 'wheel'].forEach(type =>
  document.addEventListener(type, event => {
    if (closingWorkspace) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, { capture: true, passive: false }));
window.aplusFlushAndClose = async () => {
  if (closingWorkspace) { window.aplusCloseRequested = true; return; }
  document.activeElement?.blur?.();
  closingWorkspace = true;
  try {
    await savePrototypeState(true);
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

function clamp(value, min, max) { return Math.max(Math.min(min, max), Math.min(max, value)); }
function visibleWindow() {
  const maxEnd = Math.min(replayIndex + 1, bars.length);
  const begin = Math.max(0, Math.round(maxEnd - viewCount - viewOffset));
  const end = Math.min(maxEnd, begin + viewCount + RIGHT_MARGIN_BARS);
  return { begin, end, data: bars.slice(begin, end) };
}

function panLimits() {
  const maxEnd=Math.min(replayIndex+1,bars.length);
  const calendar=chartCoordinates.bounds(bars,activeTimeframeUnit);
  const future=Math.max(0,calendar.max-(maxEnd-1)-RIGHT_MARGIN_BARS);
  return {min:-Math.min(Math.floor(viewCount*.8),future),max:Math.max(0,maxEnd-viewCount)};
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
  if (drawingTemplates.fib) return structuredClone(drawingTemplates.fib);
  if (chartBoot.production) return null;
  try { return JSON.parse(localStorage.getItem(`${tvStateKey}-fib-template`) || 'null'); } catch (_) { return null; }
}

function saveFibTemplate(object) {
  const template = Object.fromEntries(Object.entries(object).filter(([key]) => key === 'color' || key === 'width' || key === 'dash' || key.startsWith('fib')));
  drawingTemplates.fib = structuredClone(template);savePrototypeState();
}

function objectVisibleAtTimeframe(object) {
  if (object.type !== 'fib') return object.visibility?.[activeTimeframeUnit] !== false;
  const visibility = ensureFibSettings(object).fibVisibility;
  const key = activeTimeframeUnit === 'weekly' ? 'weeks' : activeTimeframeUnit === 'monthly' ? 'months' : 'days';
  const setting=visibility[key];
  return setting?.enabled !== false && (setting?.min??1)<=1 && (setting?.max??1)>=1;
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

function registerPolygon(id, points) {
  hitRegions.push({id,handle:null,hit:(x,y)=>{
    let inside=false;
    for(let i=0,j=points.length-1;i<points.length;j=i++){
      const [ax,ay]=points[i],[bx,by]=points[j];
      if((ay>y)!==(by>y)&&x<(bx-ax)*(y-ay)/(by-ay)+ax)inside=!inside;
    }
    return inside;
  }});
}

function hitRegion(x, y) {
  for (let index = hitRegions.length - 1; index >= 0; index -= 1) {
    if (hitRegions[index].hit(x, y)) return hitRegions[index];
  }
  return null;
}
function hitObject(x, y) { return hitRegion(x, y)?.id || null; }

function drawingVisibleInWorkspace(object) {
  return Boolean(object && drawingsVisible && object.visible !== false && objectVisibleAtTimeframe(object) &&
    (!replayActive || (object.createdAtDate || bars[object.createdAtIndex ?? bars.length - 1]?.date) <=
      (replayCutoffDate || bars[replayIndex]?.date)));
}

function timeAxisTicks(view) {
  const candidates=[];
  const stride=[1,2,5,10,20,50,100].find(step=>step>=72/view.candleWidth)||100;
  let previous=null;
  try{previous=chartCoordinates.dateAt(bars,view.begin-1,activeTimeframeUnit);}catch(_error){/* Start of verified dates. */}
  for(let offset=0;offset<Math.ceil(viewCount+RIGHT_MARGIN_BARS);offset++){
    const index=view.begin+offset;
    let day;
    try{day=chartCoordinates.dateAt(bars,index,activeTimeframeUnit);}catch(_error){break;}
    const x=view.xGlobal(index);
    if(x>view.width-view.right-28)break;
    const newYear=previous&&day.slice(0,4)!==previous.slice(0,4);
    const newMonth=!previous||day.slice(0,7)!==previous.slice(0,7);
    let label=null,priority=0;
    if(newYear || (offset===0&&activeTimeframeUnit==='monthly')){label=day.slice(0,4);priority=3;}
    else if((newMonth||offset===0)&&(activeTimeframeUnit!=='monthly'||view.candleWidth>=36)){
      label=`${Number(day.slice(5,7))}月`;priority=2;
    }else if(activeTimeframeUnit==='daily'&&view.candleWidth>=9&&index%stride===0){
      label=String(Number(day.slice(8,10)));priority=1;
    }
    if(label)candidates.push({index,x,label,priority,date:day});
    previous=day;
  }
  const selected=[];
  candidates.sort((a,b)=>b.priority-a.priority||a.index-b.index).forEach(tick=>{
    if(selected.every(other=>Math.abs(tick.x-other.x)>=60))selected.push(tick);
  });
  return selected.sort((a,b)=>a.index-b.index);
}

function renderWorkspace(mode = 'all') {
  const visibleObjects = userObjects.filter(drawingVisibleInWorkspace);
  document.querySelector('#selectedObject').classList.toggle('visible', visibleObjects.some(item => item.id === selectedId));
  const rect = wrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const background = typeof baseCanvas === 'undefined' || !baseCanvas ? canvas : baseCanvas;
  const backgroundResized = sizeCanvas(background, rect.width, rect.height, ratio);
  const overlayResized = sizeCanvas(canvas, rect.width, rect.height, ratio);
  const resized = backgroundResized || overlayResized;
  const renderBase = mode !== 'overlay' || Boolean(resized);
  const ctx = background.getContext('2d');
  const overlay = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  overlay.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = rect.width;
  const height = rect.height;
  const right = 61;
  const compact = height < 260;
  const bottom = compact ? 52 : 76;
  // Keep even the hidden-state 8px animation clear of the time-axis hit area.
  document.querySelector('#chartNavControls').style.bottom = `${bottom + 10}px`;
  const top = compact ? 20 : 42;
  const left = 8;
  const plotWidth = width - right - left;
  const plotHeight = Math.max(1, height - bottom - top);
  const visible = visibleWindow();
  const rows = visible.data;
  if (!rows.length) {
    if (renderBase) { ctx.clearRect(0, 0, width, height); ctx.fillStyle=css('--bg');ctx.fillRect(0,0,width,height); }
    overlay.clearRect(0, 0, width, height);
    canvas._view=null;background._view=null;staticHitRegions=[];hitRegions=[];
    return;
  }
  const priceLow = Math.min(...rows.map(row => row.low));
  const priceHigh = Math.max(...rows.map(row => row.high));
  const pricePadding = Math.max(priceHigh - priceLow, Math.abs(priceHigh) * .02, .01) * .08;
  const rawLow = priceLow - pricePadding;
  const rawHigh = priceHigh + pricePadding;
  const rawCenter = (rawHigh + rawLow) / 2 + priceCenterOffset;
  const span = (rawHigh - rawLow) / priceZoom;
  const low = rawCenter - span / 2;
  const high = rawCenter + span / 2;
  const y = value => top + (high - value) / (high - low) * plotHeight;
  const futureSpace = Math.max(0, RIGHT_MARGIN_BARS - viewOffset);
  const candleWidth = plotWidth / Math.max(1, viewCount + RIGHT_MARGIN_BARS);
  const xGlobal = globalIndex => xFor(globalIndex - visible.begin, left, candleWidth);
  canvas._view = { rows, begin: visible.begin, end: visible.end, left, right, top, bottom, width, height, plotWidth, plotHeight, y, low, high, candleWidth, xGlobal, futureSpace };
  background._view = canvas._view;

  if (renderBase) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = css('--bg');
  ctx.fillRect(0, 0, width, height);
  hitRegions = [];
  strategyLabelBoxes = [];

  ctx.strokeStyle = css('--grid');
  ctx.lineWidth = 1;
  ctx.font = `${compact ? 10 : 12}px Segoe UI`;
  ctx.fillStyle = '#8c8f96';
  ctx.textAlign = 'left';
  const magnitude = Math.pow(10, Math.floor(Math.log10(Math.max(span / 8, .000001))));
  const stepRatio = span / 8 / magnitude;
  const priceStep = (stepRatio <= 1 ? 1 : stepRatio <= 2 ? 2 : stepRatio <= 5 ? 5 : 10) * magnitude;
  for (let value = Math.ceil(low / priceStep) * priceStep; value <= high; value += priceStep) {
    const py = Math.round(y(value)) + .5;
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(width - right, py); ctx.stroke();
    ctx.fillText(chartPrice(value), width - right + 8, py + 4);
  }
  timeAxisTicks(canvas._view).forEach(mark => {
    const px = Math.round(mark.x) + .5;
    ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, height - bottom); ctx.stroke();
    ctx.fillText(mark.label, px + 5, height - (compact ? 33 : 52));
  });

  ctx.save(); ctx.beginPath(); ctx.rect(left, top, plotWidth, plotHeight); ctx.clip();
  ctx.strokeStyle = indicatorSettings.ema_color;
  ctx.globalAlpha = indicatorSettings.ema_alpha ?? 1;
  ctx.lineWidth = Number(indicatorSettings.ema_width || 1.5);
  ctx.beginPath();
  if (indicatorSettings.indicators_visible !== false && indicatorSettings.ema && indicatorSettings['ema_' + activeTimeframeUnit] !== false) rows.forEach((row, index) => {
    const px = xFor(index, left, candleWidth);
    const emaIndex = visible.begin + index - Number(indicatorSettings.ema_offset);
    const value = emaIndex <= replayIndex ? ema[emaIndex] : undefined;
    if (!Number.isFinite(value)) return;
    const py = y(value);
    ctx.lineTo(px, py);
  });
  ctx.stroke();

  ctx.globalAlpha = 1;
  if (indicatorSettings.indicators_visible !== false && indicatorSettings.ema && indicatorSettings['ema_' + activeTimeframeUnit] !== false) {
    ctx.save();ctx.strokeStyle=indicatorSettings.ema_smooth_color||'#fdd835';ctx.lineWidth=1;
    const seriesStyles=[[emaSmoothing.values,indicatorSettings.ema_smooth_color||'#fdd835',indicatorSettings.ema_smooth_visible!==false,indicatorSettings.ema_smooth_alpha??1],
      [emaSmoothing.upper,indicatorSettings.ema_bb_upper_color||'#26a69a',indicatorSettings.ema_bb_visible!==false,indicatorSettings.ema_bb_upper_alpha??1],
      [emaSmoothing.lower,indicatorSettings.ema_bb_lower_color||'#26a69a',indicatorSettings.ema_bb_visible!==false,indicatorSettings.ema_bb_lower_alpha??1]];
    if(indicatorSettings.ema_bb_visible!==false && emaSmoothing.upper.length){
      ctx.save();ctx.fillStyle=indicatorSettings.ema_bb_fill_color||'#26a69a';ctx.globalAlpha=indicatorSettings.ema_bb_fill_alpha??.1;
      let polygon=[];
      const fillBand=()=>{if(polygon.length>1){ctx.beginPath();polygon.forEach(([px,up],n)=>n?ctx.lineTo(px,up):ctx.moveTo(px,up));
        [...polygon].reverse().forEach(([px,_up,down])=>ctx.lineTo(px,down));ctx.closePath();ctx.fill();}polygon=[];};
      rows.forEach((row,index)=>{const at=visible.begin+index-Number(indicatorSettings.ema_offset);
        const up=at<=replayIndex?emaSmoothing.upper[at]:null,down=emaSmoothing.lower[at];
        if(!Number.isFinite(up)||!Number.isFinite(down)){fillBand();return;}
        polygon.push([xFor(index,left,candleWidth),y(up),y(down)]);});fillBand();ctx.restore();
    }
    for (const [series,color,enabled,alpha] of seriesStyles) {
      if(!enabled)continue;ctx.strokeStyle=color;ctx.globalAlpha=alpha;
      ctx.beginPath();let started=false;
      rows.forEach((row,index)=>{const sourceIndex=visible.begin+index-Number(indicatorSettings.ema_offset);
        const value=sourceIndex<=replayIndex?series[sourceIndex]:null;
        if(!Number.isFinite(value)){started=false;return;}
        const px=xFor(index,left,candleWidth);started?ctx.lineTo(px,y(value)):ctx.moveTo(px,y(value));started=true;});
      ctx.stroke();
    }ctx.restore();
  }
  if (indicatorSettings.chart_type === 'line') {
    ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 2; ctx.beginPath();
    rows.forEach((row, index) => index ? ctx.lineTo(xFor(index,left,candleWidth),y(row.close))
      : ctx.moveTo(xFor(index,left,candleWidth),y(row.close)));
    ctx.stroke();
  } else rows.forEach((row, index) => {
    const px = xFor(index, left, candleWidth);
    const color = row.close >= row.open ? '#f5f5f5' : '#2962ff';
    const bodyWidth = Math.max(1, candleWidth * .62);
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(px, y(row.high)); ctx.lineTo(px, y(row.low)); ctx.stroke();
    const bodyTop = y(Math.max(row.open, row.close));
    const bodyBottom = y(Math.min(row.open, row.close));
    ctx.fillRect(px - bodyWidth / 2, bodyTop, bodyWidth, Math.max(2, bodyBottom - bodyTop));
  });

  if (indicatorSettings.strategy_visible !== false && (chartBoot.production || !replayActive || replayIndex >= focusIndex(57))) drawStrategyEvidence(ctx, canvas._view);
  if (replayActive) drawReplayBoundary(ctx, canvas._view);
  if (!replayActive) chartAlerts.filter(alert => alert.enabled !== false && alert.target === 'price'
    && alert.timeframe === activeTimeframeUnit).forEach(alert => {
    const py = y(Number(alert.value));
    ctx.save(); ctx.strokeStyle = '#ff9800'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(width - right, py); ctx.stroke();
    ctx.fillStyle = '#ff9800'; ctx.font = '11px Segoe UI'; ctx.textAlign = 'left';
    ctx.fillText(`${alert.name} ${chartPrice(alert.value)}`, left + 10, py - 5); ctx.restore();
    registerRect(`alert:${alert.id}`, left, py - 5, width - right, py + 5);
  });
  ctx.restore();
  drawAxisPriceTags(ctx,canvas._view);
  drawLastPrice(ctx, rows.at(-1).close, y, width, right);
  const legendIndex = Math.min(replayIndex, bars.length - 1) - Number(indicatorSettings.ema_offset);
  const emaValue = legendIndex <= replayIndex ? ema[legendIndex] : undefined;
  document.querySelector('.ema-value').textContent = Number.isFinite(emaValue) ? chartPrice(emaValue,indicatorSettings.ema_precision) : '—';
  if (typeof syncIndicatorLegend === 'function') syncIndicatorLegend();
  staticHitRegions = hitRegions.slice();
  }

  hitRegions = staticHitRegions.slice();
  overlay.clearRect(0, 0, width, height);
  overlay.save(); overlay.beginPath(); overlay.rect(left, top, plotWidth, plotHeight); overlay.clip();
  if (drawingsVisible) {
    const selectedDrawing=visibleObjects.find(item=>item.id===selectedId);
    visibleObjects.filter(item=>item!==selectedDrawing).forEach(item=>drawUserObject(overlay,canvas._view,item));
    if(selectedDrawing)drawUserObject(overlay,canvas._view,selectedDrawing);
    if (draftAnchor && draftCursor) drawUserObject(overlay, canvas._view, {
      id: 'draft', type: activeDrawingTool, a: draftAnchor,
      b: activeDrawingTool === 'channel' && draftPoints.length > 1 ? draftPoints[1] : draftCursor,
      c: activeDrawingTool === 'channel' && draftPoints.length > 1 ? draftCursor : undefined,
      points: activeDrawingTool === 'path' ? [...draftPoints, draftCursor] : undefined,
      arrow_marker_version: activeDrawingTool === 'arrow' ? 1 : undefined,
      stop: draftAnchor.value - Math.abs(draftCursor.value - draftAnchor.value) * .55,
      color: activeDrawingTool === 'arrow' ? '#f23645' : '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true
    }, true);
  }
  overlay.restore();
}

function calculateEma() {
  const settings = typeof indicatorSettings === 'undefined' ? {ema_length:20, ema_source:'close'} : indicatorSettings;
  ema.splice(0, ema.length);
  const unit=!settings.ema_timeframe||settings.ema_timeframe==='chart'?activeTimeframeUnit:settings.ema_timeframe;
  const blind=typeof replayActive!=='undefined'&&replayActive;
  const displayRows=blind?bars.slice(0,replayIndex+1):bars;
  const daily=chartBoot.series?.daily;
  const cutoff=blind?(replayCutoffDate||displayRows.at(-1).date):(daily||bars).at(-1).date;
  const calculationRows=unit===activeTimeframeUnit?displayRows:daily?
    AplusChartIndicators.aggregate(daily.filter(row=>row.date<=cutoff),unit):
    (chartBoot.series?.[unit]||[]).filter(row=>(chartBoot.seriesCloseDates?.[unit]?.[row.date]||row.date)<=cutoff);
  const values=calculationRows.map(row=>AplusChartIndicators.source(row,settings.ema_source));
  const calculated=AplusChartIndicators.average(values,Number(settings.ema_length));
  const smoothed=AplusChartIndicators.smooth(calculated,calculationRows,settings.ema_smoothing||'none',
    settings.ema_smooth_length||14,settings.ema_bb_deviation??2);
  const merge=series=>AplusChartIndicators.mergeTimeframe(series,calculationRows,displayRows,unit,activeTimeframeUnit,
    chartBoot.seriesCloseDates||{},cutoff,settings.ema_wait_close!==false);
  ema.push(...merge(calculated));
  emaSmoothing=Object.fromEntries(Object.entries(smoothed).map(([key,series])=>[key,series.length?merge(series):[]]));
}

function drawStrategyEvidence(ctx, view) {
  const { left, width, right, y, candleWidth, xGlobal } = view;
  const plotRight = width - right;
  if (chartBoot.production) {
    if (typeof drawVectorEvidence === 'function') drawVectorEvidence(ctx, view);
    const evidence = chartBoot.evidence || {};
    const at = day => replayActive && day > (replayCutoffDate || bars[replayIndex].date)
      ? -1 : chartCoordinates.indexForDate(bars, day, activeTimeframeUnit);
    const floor = Number(evidence.gap_floor);
    const top = Number(evidence.gap_top);
    const boIndex = at(evidence.bo_date);
    const setupIndex = at(evidence.h2_setup_date || evidence.setup_date || evidence.asof);
    if (boIndex >= 0 && setupIndex >= 0 && (!replayActive || replayIndex >= setupIndex)
        && Number.isFinite(floor) && Number.isFinite(top) && top > floor) {
      const gapLeft = Math.max(left, xGlobal(boIndex) - candleWidth * .55);
      const gapRight = Math.min(plotRight, xGlobal(Math.max(boIndex, setupIndex)) + candleWidth * .55);
      ctx.save();
      ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 1.2; ctx.globalAlpha = .6;
      ctx.setLineDash([6, 4]); ctx.strokeRect(gapLeft, y(top), gapRight - gapLeft, y(floor) - y(top));
      ctx.setLineDash([]); ctx.globalAlpha = .6;
      ctx.beginPath(); ctx.moveTo(gapLeft, y(floor)); ctx.lineTo(plotRight, y(floor)); ctx.stroke();
      ctx.restore();
      if (evidence.bo_high) callout(ctx, 'BO', xGlobal(boIndex), y(Number(evidence.bo_high)), xGlobal(boIndex - 3), y(Number(evidence.bo_high)) - 28);
      if (at(evidence.h1_date) >= 0 && evidence.h1_high) callout(ctx, 'H1', xGlobal(at(evidence.h1_date)), y(Number(evidence.h1_high)), xGlobal(at(evidence.h1_date) - 3), y(Number(evidence.h1_high)) - 28);
      // A setup is not an upside trigger. Match the existing draw_h2 contract.
      const triggerDate = evidence.replay_trigger_date || (evidence.pending_state === 'TRIGGERED' ? evidence.pending_end_date : null);
      const triggerIndex = triggerDate ? at(triggerDate) : -1;
      if (triggerIndex >= 0 && (!replayActive || replayIndex >= triggerIndex)) {
        const triggerHigh = bars[triggerIndex].high;
        callout(ctx, 'H2', xGlobal(triggerIndex), y(triggerHigh), xGlobal(triggerIndex - 3), y(triggerHigh) - 32, true);
      }
      const lowIndex = evidence.mm_low_date ? at(evidence.mm_low_date) : -1;
      if (lowIndex >= 0 && Number.isFinite(Number(evidence.mm_low))) callout(ctx, 'MM low',
        xGlobal(lowIndex), y(Number(evidence.mm_low)), xGlobal(lowIndex + 3), y(Number(evidence.mm_low)) + 28);
      const floorIndex = evidence.gap_floor_date ? at(evidence.gap_floor_date) : boIndex;
      if (floorIndex >= 0) callout(ctx, 'Gap Floor', xGlobal(floorIndex), y(floor),
        Math.max(left + 5, xGlobal(floorIndex) - 32), y(floor) + 28);
      callout(ctx, 'Gap', (gapLeft + gapRight) / 2, y((floor + top) / 2),
        (gapLeft + gapRight) / 2 + 28, y(top) - 18);
      registerRect('strategy-evidence', gapLeft, y(top), gapRight, y(floor));
    }
    (evidence.prior_open_gaps || []).forEach(gap => {
      const index = at(gap.date);if(index < 0 || (replayActive && replayIndex < setupIndex))return;
      ctx.save();ctx.strokeStyle='#787b86';ctx.globalAlpha=.6;ctx.lineWidth=.7;ctx.setLineDash([2,4]);
      ctx.beginPath();ctx.moveTo(xGlobal(index),y(Number(gap.floor)));ctx.lineTo(plotRight,y(Number(gap.floor)));ctx.stroke();ctx.restore();
    });
    (chartBoot.holdingLevels || []).forEach(level => {
      if (replayActive) return; // current account levels are not historical replay evidence
      const price = Number(level.price ?? level.value);
      if (!Number.isFinite(price) || price <= 0) return;
      ctx.save(); ctx.strokeStyle = level.color || '#b8bdc8'; ctx.setLineDash(level.style === '--' ? [7, 5] : level.style === '-.' ? [7, 4, 2, 4] : []);
      ctx.beginPath(); ctx.moveTo(left, y(price)); ctx.lineTo(plotRight, y(price)); ctx.stroke();
      ctx.fillStyle = level.color || '#b8bdc8'; ctx.font = '12px Segoe UI'; ctx.textAlign = 'left';
      ctx.fillText(`${level.label} ${chartPrice(price)}`, left + 8, y(price) - 5); ctx.restore();
    });
    if (evidence.plan_kind === 'gap-h2-next-session' && setupIndex >= 0 && (!replayActive || replayIndex >= setupIndex)) {
      [['entry_plan_price','Entry','#089981'],['sl1_plan_price','SL1','#f23645'],['mm_target','MM','#089981']].forEach(([key,label,color]) => {
        if (key !== 'mm_target' && evidence.pending_state !== 'PENDING') return;
        if (key !== 'mm_target' && replayActive && String(evidence.plan_asof || '9999') >
            String(replayCutoffDate || bars[replayIndex].date)) return;
        const price = Number(evidence[key] ?? evidence[key === 'entry_plan_price' ? 'entry' : key === 'sl1_plan_price' ? 'stop' : 'target']);
        if (!Number.isFinite(price) || price <= 0) return;
        ctx.save(); ctx.strokeStyle = color; ctx.setLineDash([5,5]); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(left,y(price)); ctx.lineTo(plotRight,y(price)); ctx.stroke();
        ctx.fillStyle=color; ctx.font='11px Segoe UI'; ctx.fillText(`${label} ${chartPrice(price)}`,left+8,y(price)-5);ctx.restore();
      });
    }
    return;
  }
  const gapLeft = Math.max(left, xGlobal(focusIndex(57)) - candleWidth * .55);
  const gapRight = Math.min(plotRight, xGlobal(focusIndex(64)) + candleWidth * .55);
  ctx.save();
  ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 1.2; ctx.globalAlpha = .6;
  ctx.setLineDash([6, 4]); ctx.strokeRect(gapLeft, y(4.56), gapRight - gapLeft, y(4.38) - y(4.56));
  ctx.setLineDash([]); ctx.globalAlpha = .6;
  ctx.beginPath(); ctx.moveTo(gapLeft, y(4.38)); ctx.lineTo(plotRight, y(4.38)); ctx.stroke();
  ctx.restore();
  callout(ctx, 'BO', xGlobal(focusIndex(57)), y(4.56), xGlobal(focusIndex(54)), y(4.83));
  callout(ctx, 'H1', xGlobal(focusIndex(58)), y(4.45), xGlobal(focusIndex(55)), y(4.72));
  if (!replayActive || replayIndex >= focusIndex(70)) callout(ctx, 'H2', xGlobal(focusIndex(70)), y(5.19), xGlobal(focusIndex(67)), y(5.48), true);
  callout(ctx, 'Gap Floor', gapLeft, y(4.38), gapLeft - 32, y(4.18));
  callout(ctx, 'Gap', (gapLeft + gapRight) / 2, y(4.47), (gapLeft + gapRight) / 2 + 28, y(4.56) - 18);
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

  if (object.type === 'arrow' && object.arrow_marker_version) {
    const polygon = arrowMarkerPoints(ax, ay, bx, by);
    ctx.setLineDash([]);ctx.beginPath();
    polygon.forEach(([x,yy],index)=>index?ctx.lineTo(x,yy):ctx.moveTo(x,yy));
    ctx.closePath();ctx.fill();registerPolygon(object.id,polygon);
    registerSegment(object.id,ax,ay,bx,by);
  } else if (['trend', 'arrow', 'ruler'].includes(object.type)) {
    const slope = (by-ay)/((bx-ax)||1);
    const startX = object.type==='trend' && object.extendLeft ? left : ax;
    const endX = object.type==='trend' && object.extendRight ? plotRight : bx;
    const startY=ay+slope*(startX-ax), endY=ay+slope*(endX-ax);
    ctx.beginPath(); ctx.moveTo(startX,startY); ctx.lineTo(endX,endY); ctx.stroke();
    if(['trend','arrow'].includes(object.type))drawTrendDetails(ctx,object,ax,ay,bx,by,selectedObjectNow);
    if (object.type === 'ruler' && object.showLabel !== false) {
      const change = object.a.value ? ((b.value / object.a.value - 1) * 100).toFixed(2) : '0.00';
      ctx.fillStyle = '#202020'; ctx.fillRect((ax + bx) / 2 - 46, (ay + by) / 2 - 24, 92, 20);
      ctx.fillStyle = '#e3e3e3'; ctx.textAlign = 'center'; ctx.font = '12px Segoe UI';
      ctx.fillText(`${change}% · ${Math.abs(Math.round(b.index - object.a.index))}根`, (ax + bx) / 2, (ay + by) / 2 - 10);
    }
    registerSegment(object.id, startX, startY, endX, endY);
  } else if (object.type === 'path') {
    const points = object.points?.length ? object.points : [object.a, object.b];
    const screens = points.map(point => [xGlobal(point.index), y(point.value)]);
    ctx.beginPath();
    screens.forEach(([x, yy], index) => index ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy));
    if(object.closed)ctx.closePath();
    if(object.fill){ctx.save();ctx.fillStyle=object.backgroundColor||object.color;ctx.globalAlpha=(object.backgroundAlpha??.13)*(object.alpha??1);ctx.fill();ctx.restore();registerPolygon(object.id,screens);}
    ctx.stroke();
    screens.slice(1).forEach(([x, yy], index) => registerSegment(object.id, screens[index][0], screens[index][1], x, yy));
    const distinctFrom = (origin, candidates) => candidates.find(point => Math.hypot(point[0]-origin[0],point[1]-origin[1])>.01);
    const first=screens[0],last=screens.at(-1);
    ctx.save();ctx.setLineDash([]);
    const next=distinctFrom(first,screens.slice(1));
    if(object.startStyle==='arrow'&&next)drawArrowHead(ctx,...next,...first);
    const previous=distinctFrom(last,screens.slice(0,-1).reverse());
    if((object.endStyle||(object.path_version?'arrow':'none'))==='arrow'&&previous)drawArrowHead(ctx,...previous,...last);
    ctx.restore();
  } else if (object.type === 'ray') {
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(plotRight, ay); ctx.stroke();
    registerSegment(object.id, ax, ay, plotRight, ay);
    if (object.showPrice !== false) {ctx.font='11px Segoe UI';ctx.textAlign='right';ctx.fillText(chartPrice(object.a.value),plotRight-6,ay-6);}
  } else if (object.type === 'rectangle') {
    const startX=object.extendLeft?left:Math.min(ax,bx);
    const endX=object.extendRight?plotRight:Math.max(ax,bx);
    if (object.fill !== false) { ctx.globalAlpha = (object.backgroundAlpha ?? .13)*(object.alpha ?? 1);ctx.fillStyle=object.backgroundColor||object.color; ctx.fillRect(startX, ay, endX - startX, by - ay); }
    ctx.globalAlpha = (draft ? .65 : 1) * (object.alpha ?? 1); ctx.strokeRect(startX, ay, endX - startX, by - ay);
    if(object.showMiddle){ctx.save();ctx.strokeStyle=object.middleColor||object.color;ctx.lineWidth=object.middleWidth||1;
      ctx.globalAlpha=(object.middleAlpha??1)*(object.alpha??1);
      ctx.setLineDash(dashPattern(object.middleDash||'dashed'));ctx.beginPath();ctx.moveTo(startX,(ay+by)/2);ctx.lineTo(endX,(ay+by)/2);ctx.stroke();ctx.restore();}
    registerRect(object.id, startX, ay, endX, by);
  } else if (object.type === 'fib') {
    ensureFibSettings(object);
    const startX = object.fibExtend === 'left' || object.fibExtend === 'both' ? left : Math.min(ax, bx);
    const endX = object.fibExtend === 'right' || object.fibExtend === 'both' ? plotRight : Math.max(ax, bx);
    const enabledLevels = object.fibLevels.map((level,index)=>({...level,levelIndex:index})).filter(level => level.enabled);
    const renderedLevels = enabledLevels.map(level => {
      const ratio = object.fibReverse ? 1 - Number(level.value) : Number(level.value);
      const value = object.fibLog ? Math.exp(Math.log(object.a.value) + (Math.log(b.value)-Math.log(object.a.value))*ratio)
        : object.a.value + (b.value - object.a.value) * ratio;
      return { ...level, valueAtLevel: value, py: y(value) };
    });
    if (object.fibBackground && renderedLevels.length > 1) {
      registerRect(object.id,startX,Math.min(...renderedLevels.map(level=>level.py)),endX,Math.max(...renderedLevels.map(level=>level.py)));
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
      ctx.globalAlpha *= object.fibTrendAlpha ?? 1;
      ctx.lineWidth = object.fibTrendWidth || object.width || 1;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke(); ctx.restore();
    }
    renderedLevels.forEach(level => {
      ctx.globalAlpha = (draft ? .65 : 1) * (object.alpha ?? 1) *
        (object.fibUseOneColor ? (object.fibSingleAlpha ?? 1) : (level.alpha ?? 1));
      const lineColor = object.fibUseOneColor ? (object.fibSingleColor || object.color || '#4caf50') : level.color;
      ctx.strokeStyle = lineColor; ctx.fillStyle = lineColor;
      ctx.lineWidth = object.fibHorizontalWidth || object.width || 1;
      ctx.setLineDash(dashPattern(object.fibHorizontalDash || object.dash));
      ctx.beginPath(); ctx.moveTo(startX, level.py); ctx.lineTo(endX, level.py); ctx.stroke();
      const labelX = object.fibLabelHorizontal === 'right' ? endX - 5 : object.fibLabelHorizontal === 'center' ? (startX + endX) / 2 : startX + 5;
      const labelAlign = object.fibLabelHorizontal === 'right' ? 'right' : object.fibLabelHorizontal === 'center' ? 'center' : 'left';
      const labelOffset = object.fibLabelVertical === 'top' ? -5 : object.fibLabelVertical === 'bottom' ? (object.fibFontSize || 12) + 4 : 4;
      const levelLabel = object.fibLevelFormat === 'percent' ? `${(Number(level.value) * 100).toFixed(1).replace('.0', '')}%` : `${level.value}`;
      const text = [object.fibShowLevels !== false ? levelLabel : '', object.fibShowPrices !== false ? `(${chartPrice(level.valueAtLevel)})` : ''].filter(Boolean).join(' ');
      if (text) {
        ctx.font = `${object.fibFontSize || 12}px Segoe UI`; ctx.textAlign = labelAlign; ctx.fillText(text, labelX, level.py + labelOffset);
      }
      registerSegment(object.id, startX, level.py, endX, level.py);
      drawFibLevelText(ctx,object,level,level.levelIndex,startX,endX,selectedObjectNow,draft);
    });
  } else if (object.type === 'channel') {
    const slope = (b.value-object.a.value)/((b.index-object.a.index)||1);
    const c = object.c || {index:object.a.index,value:object.a.value+Math.max(.01, Math.abs(b.value-object.a.value)*.34)};
    const delta = c.value - (object.a.value+slope*(c.index-object.a.index));
    const startIndex=object.extendLeft?view.begin:object.a.index;
    const endIndex=object.extendRight?view.end+view.futureSpace:b.index;
    const cx1=xGlobal(startIndex),cx2=xGlobal(endIndex);
    const at=index=>object.a.value+slope*(index-object.a.index);
    if(object.fill !== false){ctx.save();ctx.globalAlpha=(object.backgroundAlpha??.1)*(object.alpha??1);ctx.fillStyle=object.backgroundColor||object.color;
      ctx.beginPath();ctx.moveTo(cx1,y(at(startIndex)));ctx.lineTo(cx2,y(at(endIndex)));ctx.lineTo(cx2,y(at(endIndex)+delta));ctx.lineTo(cx1,y(at(startIndex)+delta));ctx.closePath();ctx.fill();ctx.restore();
      registerPolygon(object.id,[[cx1,y(at(startIndex))],[cx2,y(at(endIndex))],[cx2,y(at(endIndex)+delta)],[cx1,y(at(startIndex)+delta)]]);}
    channelLevels(object).filter(level=>level.enabled).forEach(level => {
      ctx.strokeStyle=level.color||object.color;ctx.lineWidth=level.width||object.width||1;
      ctx.globalAlpha=(draft ? .65 : 1)*(level.alpha??1)*(object.alpha??1);
      ctx.setLineDash(dashPattern(level.dash||object.dash));
      const y1 = y(at(startIndex) + delta*level.value), y2 = y(at(endIndex) + delta*level.value);
      ctx.beginPath(); ctx.moveTo(cx1, y1); ctx.lineTo(cx2, y2); ctx.stroke();
      registerSegment(object.id, cx1, y1, cx2, y2);
    });
  } else if (object.type === 'long' || object.type === 'short') {
    const entry = object.a.value;
    const target = b.value;
    const stop = object.stop ?? (entry + (entry - target) * .55);
    const x1 = Math.min(ax, bx), x2 = Math.max(ax, bx);
    ctx.globalAlpha = (object.targetAlpha ?? object.regionAlpha ?? .24)*(object.alpha ?? 1);
    ctx.fillStyle = object.targetColor || '#089981'; if(object.showTarget !== false)ctx.fillRect(x1, y(target), x2 - x1, y(entry) - y(target));
    ctx.globalAlpha = (object.stopAlpha ?? object.regionAlpha ?? .24)*(object.alpha ?? 1);
    ctx.fillStyle = object.stopColor || '#f23645'; if(object.showStop !== false)ctx.fillRect(x1, y(entry), x2 - x1, y(stop) - y(entry));
    ctx.globalAlpha = object.alpha ?? 1; ctx.strokeStyle = object.color; ctx.setLineDash([]);
    [target, entry, stop].forEach(value => { ctx.beginPath(); ctx.moveTo(x1, y(value)); ctx.lineTo(x2, y(value)); ctx.stroke(); });
    registerRect(object.id, x1, Math.min(y(target), y(stop)), x2, Math.max(y(target), y(stop)));
    if(!draft && (selectedObjectNow || (object.alwaysShowStats ?? !object.position_version))){
      const result=AplusPositionMetrics.metrics(object,bars,replayIndex,chartBoot.priceStep||NaN);
      drawPositionStats(ctx,object,result,x1,x2,y);
    }
  } else if (object.type === 'up' || object.type === 'down') {
    const scale=(object.size||30)/28, direction=object.type==='up'?1:-1;
    ctx.beginPath();[[0,-13],[-11,0],[-4,0],[-4,12],[4,12],[4,0],[11,0]].forEach(([x,v],i)=>i?ctx.lineTo(ax+x*scale,ay+v*scale*direction):ctx.moveTo(ax+x*scale,ay+v*scale*direction));
    ctx.closePath();ctx.fill();ctx.stroke();
    registerRect(object.id, ax - 14, ay - 18, ax + 14, ay + 14);
  } else if (object.type === 'text') {
    ctx.font = '13px Segoe UI'; ctx.textAlign = 'left'; ctx.fillText(object.text || '笔记', ax, ay);
    registerRect(object.id, ax, ay - 16, ax + 38, ay + 5);
  }

  if(object.text && ['trend','ray','channel','rectangle','path','arrow','up','down'].includes(object.type)) drawAttachedText(ctx,view,object);
  if (selectedObjectNow && !draft) drawSelectionHandles(ctx, object, ax, ay, bx, by);
  ctx.restore();
}

function arrowMarkerPoints(ax, ay, bx, by) {
  const length=Math.hypot(bx-ax,by-ay);
  if(length<.01)return [[ax,ay],[bx,by]];
  const ux=(bx-ax)/length,uy=(by-ay)/length;
  const head=Math.min(48,length*.4),wing=head*.625,shaft=wing*.45;
  const base=[bx-ux*head,by-uy*head];
  const side=offset=>[base[0]-uy*offset,base[1]+ux*offset];
  return [[ax,ay],side(shaft),side(wing),[bx,by],side(-wing),side(-shaft)];
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
  if (['long', 'short'].includes(object.type)) points = [
    {x:ax,y:ay,handle:'a'}, {x:bx,y:ay,handle:'right'},
    {x:ax,y:by,handle:'target'}, {x:ax,y:canvas._view.y(object.stop),handle:'stop'}
  ];
  if (object.type === 'rectangle') points.push(
    {x:ax,y:by,handle:'rect-ab'}, {x:bx,y:ay,handle:'rect-ba'},
    {x:ax,y:(ay+by)/2,handle:'rect-a-x'}, {x:bx,y:(ay+by)/2,handle:'rect-b-x'},
    {x:(ax+bx)/2,y:ay,handle:'rect-a-y'}, {x:(ax+bx)/2,y:by,handle:'rect-b-y'}
  );
  if (object.type === 'channel' && object.c) points.push({x:canvas._view.xGlobal(object.c.index), y:canvas._view.y(object.c.value), handle:'c'});
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
  const index = view.begin + (x - view.left) / view.candleWidth - .5;
  const value = view.high - (yPx - view.top) / view.plotHeight * (view.high - view.low);
  return { x, y: yPx, index, value };
}

function selectObject(id, x, y, refreshTree = true) {
  if (id === 'strategy-evidence') {
    showToast('策略证据为只读；不会被人工标绘覆盖');
    return;
  }
  const selectionChanged = selectedId !== id;
  selectedId = id;
  const object = userObjects.find(item => item.id === id);
  selected = Boolean(object);
  if (!object) {
    document.querySelector('#selectedObject').classList.remove('visible');
    if (selectionChanged) renderWorkspace('overlay');
    return;
  }
  const toolbar = document.querySelector('#selectedObject');
  toolbar.classList.add('visible');
  // Keep editing controls above the price plot. A toolbar tied to the last
  // click can cover an endpoint when that endpoint is dragged toward it.
  toolbar.style.left = `${Math.max(8, wrap.clientWidth - 190)}px`;
  toolbar.style.top = '0px';
  document.querySelector('#quickWidth').textContent = `${object.width || 2}px`;
  document.querySelector('#quickWidth').hidden = ['up','down','text'].includes(object.type) || Boolean(object.arrow_marker_version);
  document.querySelector('.color-swatch').style.background = object.color || '#2962ff';
  if(refreshTree)updateObjectTree();
  else document.querySelectorAll('[data-object-id]').forEach(row=>row.classList.toggle('selected',row.dataset.objectId===id));
  if (selectionChanged) renderWorkspace('overlay');
}

function objectAtSelection() { return userObjects.find(item => item.id === selectedId); }

function startTool(type, button) {
  if (type === 'smile') {
    exitDrawingMode(); window.openChartIconPicker?.(); return;
  }
  if (type === 'magnet') {
    magnetEnabled = !magnetEnabled;
    button.classList.toggle('on', magnetEnabled);
    button.setAttribute('aria-pressed', String(magnetEnabled));
    indicatorSettings.magnet = magnetEnabled; savePrototypeState();
    showToast(magnetEnabled ? '磁铁模式已开启：端点吸附最近K线的OHLC' : '磁铁模式已关闭');
    return;
  }
  if (type === 'lock') {
    rememberObjects(); indicatorSettings.lock_drawings = !indicatorSettings.lock_drawings;
    syncWorkspaceControls(); savePrototypeState();
    showToast(indicatorSettings.lock_drawings ? '人工标绘已锁定' : '人工标绘已解锁');
    return;
  }
  pendingDrawingText = null;
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
  const instruction = type === 'path' ? '依次单击转折点，双击完成' : (type === 'zoom' || type === 'zoomOut') ? '在图上拖框选择区域' : onePointTools.has(type)||['long','short'].includes(type) ? '在图上单击放置' : '依次单击起点和终点';
  showToast(activeDrawingTool ? `${toolNames[type] || button?.title}：${instruction}` : `${button?.title || '十字光标'}已选择`);
  renderWorkspace('overlay');
}

function normalizeDrawingPrice(value) {
  const tick=Number(chartBoot.priceStep || (chartBoot.production?0:.01));
  return tick>0?Number((Math.round(value/tick)*tick).toFixed(8)):value;
}
function snapDrawingPoint(point) {
  const limits = chartCoordinates.bounds(bars, activeTimeframeUnit);
  const snapped = { index: clamp(Math.round(point.index), limits.min, limits.max), value: normalizeDrawingPrice(point.value) };
  if (!magnetEnabled || !canvas._view) return snapped;
  if (snapped.index < 0 || snapped.index >= bars.length || (replayActive && snapped.index>replayIndex)) return snapped;
  const index = clamp(Math.round(point.index), 0, bars.length - 1);
  const row = bars[index];
  const candidates = [row.open, row.high, row.low, row.close];
  const nearest = candidates.reduce((best, value) => Math.abs(canvas._view.y(value) - point.y) < Math.abs(canvas._view.y(best) - point.y) ? value : best, candidates[0]);
  if (Math.abs(canvas._view.y(nearest) - point.y) <= 16) return { index, value: normalizeDrawingPrice(nearest) };
  return snapped;
}

function exitDrawingMode() {
  activeDrawingTool = null; draftAnchor = null; draftCursor = null; draftPoints = [];
  pendingDrawingText = null;
  document.querySelectorAll('[data-favorite], .tool[data-tool]:not(.magnet):not([data-tool="lock"])').forEach(item => item.classList.remove('active'));
  document.querySelector('[data-tool="cross"]').classList.add('active');
  canvas.classList.remove('drawing');
}

function placeDrawing(point) {
  const snapped = snapDrawingPoint(point);
  if (!draftAnchor) {
    draftAnchor = snapped;
    draftCursor = { ...draftAnchor };
    if (activeDrawingTool === 'path') { draftPoints = [draftAnchor]; renderWorkspace('overlay'); return; }
    if (!onePointTools.has(activeDrawingTool) && !['long','short'].includes(activeDrawingTool)) { renderWorkspace('overlay'); return; }
  }
  if (activeDrawingTool === 'path') {
    const last = draftPoints.at(-1);
    if (!last || Math.abs(last.index - snapped.index) > .2 || Math.abs(last.value - snapped.value) > .01) draftPoints.push(snapped);
    draftCursor = snapped; renderWorkspace('overlay'); return;
  }
  if (activeDrawingTool === 'channel' && draftPoints.length < 2) {
    draftPoints = [structuredClone(draftAnchor), snapped];
    draftCursor = snapped; renderWorkspace('overlay'); showToast('点击第三点设置平行通道宽度'); return;
  }
  const finish = activeDrawingTool === 'channel' ? draftPoints[1] : snapped;
  const object = {
    id: `${activeDrawingTool}-${Date.now()}`, type: activeDrawingTool,
    a: draftAnchor, b: onePointTools.has(activeDrawingTool) ? draftAnchor : finish,
    color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true, createdAtIndex: replayActive ? replayIndex : bars.length - 1,
    createdAtDate: replayActive ? replayCutoffDate || bars[replayIndex].date : bars.at(-1).date
  };
  if (object.type === 'fib') {
    Object.assign(object, structuredClone(loadFibTemplate() || {}));
    ensureFibSettings(object);
  }
  if (object.type === 'long') object.stop = object.a.value - Math.abs(object.b.value - object.a.value) * .55;
  if (object.type === 'short') object.stop = object.a.value + Math.abs(object.b.value - object.a.value) * .55;
  if (object.type === 'channel') object.c = snapped;
  if (object.type==='up')object.color='#089981';
  if (object.type==='down')object.color='#f23645';
  if (['up','down'].includes(object.type))object.fontSize=14;
  if (object.type==='arrow')Object.assign(object,{arrow_marker_version:1,color:'#f23645',fontSize:16});
  if (object.type === 'long' || object.type === 'short') {
    const view=canvas._view,limits=chartCoordinates.bounds(bars,activeTimeframeUnit);
    const distance=normalizeDrawingPrice(Math.min(object.a.value*.9,Math.max(Number(chartBoot.priceStep)||.000001,
      (view.high-view.low)*Math.min(110,view.plotHeight*.25)/view.plotHeight)));
    object.b={index:clamp(object.a.index+Math.max(1,Math.round(150/view.candleWidth)),limits.min,limits.max),
      value:normalizeDrawingPrice(object.a.value+(object.type==='long'?distance:-distance))};
    object.stop=object.a.value+(object.type==='long'?-1:1)*distance;
    Object.assign(object,{position_version:1,account_size:1000,risk_value:25,risk_unit:'percent',
      lot_size:1,leverage:10000,qty_precision:'default',risk_budget:250,alwaysShowStats:false,color:'#787b86',width:1});
    object.stop=normalizeDrawingPrice(object.stop);
    if(Math.min(object.b.value,object.stop)<=0||object.b.value===object.a.value||object.stop===object.a.value){
      exitDrawingMode();renderWorkspace('overlay');showToast('当前价格无法放置有效的初始目标和止损，请调整放置价格');return;
    }
  }
  rememberObjects();
  Object.assign(object,structuredClone(drawingTemplates[object.type] || {}));
  if(object.type==='arrow')object.arrow_marker_version=1;
  if (object.type === 'text' && pendingDrawingText) object.text = pendingDrawingText;
  userObjects.push(object);
  draftAnchor = null; draftCursor = null;
  savePrototypeState(); updateObjectTree();
  selectObject(object.id, point.x, point.y);
  showToast(`${toolNames[object.type]}已保存到当前标的`);
  exitDrawingMode();
}

function finishPath(point = null) {
  if (activeDrawingTool !== 'path' || draftPoints.length < 2) return false;
  const snapped = point ? snapDrawingPoint(point) : draftPoints.at(-1);
  const points = [...draftPoints];
  if (Math.abs(points.at(-1).index - snapped.index) > .2 || Math.abs(points.at(-1).value - snapped.value) > .01) points.push(snapped);
  rememberObjects();
  const object = { id: `path-${Date.now()}`, type: 'path', a: points[0], b: points.at(-1), points, color: '#2962ff', width: 2, dash: 'solid', alpha: 1, visible: true, startStyle:'none',endStyle:'arrow',createdAtIndex: replayActive ? replayIndex : bars.length - 1,
    createdAtDate: replayActive ? replayCutoffDate || bars[replayIndex].date : bars.at(-1).date };
  Object.assign(object,structuredClone(drawingTemplates.path || {}));
  // Old polyline objects/templates keep their saved geometry. New drawings use
  // the Path tool seen in the trader's favourites, rather than closing a shape.
  object.path_version=1;delete object.closed;delete object.fill;
  userObjects.push(object); savePrototypeState(); updateObjectTree(); exitDrawingMode();
  selectObject(object.id, point?.x || 0, point?.y || 0);showToast('多段折线已保存到当前标的');return true;
}

function zoomAround(factor, ratio, deferred = false) {
  const view = canvas._view;
  if (!view) return;
  const current = visibleWindow();
  const anchorIndex = current.begin + ratio * (viewCount + RIGHT_MARGIN_BARS) - .5;
  const maxCount = Math.min(bars.length, replayIndex + 1);
  const nextCount = clamp(Math.round(viewCount * factor), 18, maxCount);
  const maxEnd = Math.min(bars.length,replayIndex+1);
  const begin = anchorIndex-ratio*(nextCount+RIGHT_MARGIN_BARS)+.5;
  viewCount = nextCount;
  const limits=panLimits();
  viewOffset = clamp(Math.round(maxEnd-nextCount-begin),limits.min,limits.max);
  if (deferred) { scheduleWorkspaceRender('all'); scheduleViewStateSave(); }
  else { renderWorkspace(); savePrototypeState(); }
}

function panChart(barDelta, deferred = false) {
  const limits=panLimits();
  viewOffset = clamp(viewOffset + Math.round(barDelta), limits.min, limits.max);
  if (deferred) { scheduleWorkspaceRender('all'); scheduleViewStateSave(); }
  else { renderWorkspace(); savePrototypeState(); }
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
  hoverPoint=inPlot?point:null;
  canvas.classList.toggle('price-scale', point.x > view.width - view.right && point.y < view.height - 39);
  canvas.classList.toggle('time-scale', point.y > view.height - view.bottom && point.y < view.height - 39 && point.x < view.width - view.right);
  if (!inPlot) { crosshair.classList.remove('show'); return; }
  const index=Math.round(point.index);
  const row=index>=0&&index<=replayIndex?bars[index]:null;
  let date;
  try{date=chartCoordinates.dateAt(bars,index,activeTimeframeUnit);}catch(_error){date='交易日历未覆盖';}
  crosshair.style.setProperty('--cx', `${view.xGlobal(index)}px`);
  crosshair.style.setProperty('--cy', `${point.y}px`);
  document.querySelector('#crossPrice').textContent = chartPrice(point.value);
  document.querySelector('#crossDate').textContent = date;
  crosshair.classList.add('show');updateReadout(row||view.rows.at(-1));
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
    ? clamp(Math.round((viewCount+RIGHT_MARGIN_BARS)/widthFraction)-RIGHT_MARGIN_BARS,18,replayIndex+1)
    : clamp(Math.round(Math.abs(end.index-start.index))-RIGHT_MARGIN_BARS,18,replayIndex+1);
  viewCount = nextCount;
  const limits=panLimits(),begin=centerIndex-(nextCount+RIGHT_MARGIN_BARS)/2;
  viewOffset=clamp(Math.round(Math.min(replayIndex+1,bars.length)-nextCount-begin),limits.min,limits.max);
  const heightFraction = clamp(Math.abs(end.y - start.y) / view.plotHeight, .08, 1);
  const desiredCenter = (start.value + end.value) / 2;
  const rows=visibleWindow().data;
  const priceLow=Math.min(...rows.map(row=>row.low)),priceHigh=Math.max(...rows.map(row=>row.high));
  const padding=Math.max(priceHigh-priceLow,Math.abs(priceHigh)*.02,.01)*.08;
  const rawSpan=priceHigh-priceLow+2*padding;
  const targetSpan=(view.high-view.low)*(zoomOut?1/heightFraction:heightFraction);
  priceCenterOffset=desiredCenter-(priceHigh+priceLow)/2;
  priceZoom=clamp(rawSpan/targetSpan,.45,7);
  exitDrawingMode(); hideZoomSelection(); renderWorkspace(); savePrototypeState();
  showToast(zoomOut ? '已按框选区域缩小' : '已放大到框选区域');
}

function moveObjectFromOriginal(object, original, start, point, handle) {
  if (!object || !original) return;
  if (handle) {
    const snapped = snapDrawingPoint(point);
    const edited = old => ({ ...snapped,
      date: Math.round(snapped.index) === Math.round(old.index) && old.date
        ? old.date : chartCoordinates.dateAt(bars, snapped.index, activeTimeframeUnit) });
    if (handle === 'a') object.a = edited(original.a);
    else if (handle === 'b') object.b = edited(original.b || original.a);
    else if (handle === 'right') object.b = {...edited(original.b),value:original.b.value};
    else if (handle === 'target') object.b = {...original.b,value:snapped.value};
    else if (handle === 'rect-ab') {
      object.a = {...edited(original.a),value:original.a.value};object.b = {...original.b,value:snapped.value};
    } else if (handle === 'rect-ba') {
      object.b = {...edited(original.b),value:original.b.value};object.a = {...original.a,value:snapped.value};
    } else if (handle.startsWith('rect-')) {
      const [,key,axis]=handle.split('-');
      object[key] = axis==='x'?{...edited(original[key]),value:original[key].value}:{...original[key],value:snapped.value};
    }
    else if (handle === 'c' && original.c) object.c = edited(original.c);
    else if (handle === 'stop') object.stop = snapped.value;
    else if (handle.startsWith('point:')) {
      const index = Number(handle.split(':')[1]); object.points[index] = edited(original.points[index]);
      object.a = object.points[0]; object.b = object.points.at(-1);
    }
    return;
  }
  const anchors=original.points || [original.a,original.b,original.c].filter(Boolean);
  const limits=chartCoordinates.bounds(bars,activeTimeframeUnit);
  const deltaIndex = clamp(Math.round(point.index - start.index),
    limits.min-Math.min(...anchors.map(p=>p.index)),limits.max-Math.max(...anchors.map(p=>p.index)));
  const tick = Number(chartBoot.priceStep) || .01;
  // Move by whole price ticks, preserving each original canonical price.
  const deltaValue = Math.round((point.value - start.value) / tick) * tick;
  const movePoint = value => chartCoordinates.move(value, deltaIndex, deltaValue, bars, activeTimeframeUnit);
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
    if(region?.handle?.startsWith('fib-text:')){
      event.preventDefault();
      canvas.releasePointerCapture(event.pointerId);gesture=null;
      editFibLevelText(userObjects.find(object=>object.id===region.id),Number(region.handle.split(':')[1]),point);return;
    }
    if (region?.id?.startsWith('alert:')) {
      canvas.releasePointerCapture(event.pointerId);
      window.openAlertEditor(region.id.slice(6)); return;
    }
    if (region && region.id !== 'strategy-evidence' && !document.querySelector('[data-tool="lock"]').classList.contains('active')) {
      if (selectedId !== region.id) selectObject(region.id, point.x, point.y);
      const object = userObjects.find(item => item.id === region.id);
      if (object.locked) { gesture = null; return; }
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
  if (draftAnchor && activeDrawingTool) { draftCursor = snapDrawingPoint(point); scheduleWorkspaceRender('overlay'); }
  if (!gesture) {if(objectAtSelection()?.type==='fib')scheduleWorkspaceRender('overlay');return;}
  const dx = event.clientX - gesture.startX;
  const dy = event.clientY - gesture.startY;
  gesture.moved ||= Math.abs(dx) + Math.abs(dy) > 4;
  if (gesture.mode === 'price-scale') {
    priceZoom = clamp(gesture.startZoom * Math.exp(-dy / 105), .45, 7);
    scheduleWorkspaceRender('all');
  } else if (gesture.mode === 'time-scale') {
    const next = clamp(Math.round(gesture.startCount * Math.exp(dx / 230)), 18, replayIndex + 1);
    viewCount = next;const limits=panLimits();viewOffset=clamp(viewOffset,limits.min,limits.max);scheduleWorkspaceRender('all');
  } else if (gesture.mode === 'pan' && gesture.moved) {
    const candle = Math.max(.01, canvas._view.candleWidth);
    const limits=panLimits();
    viewOffset = clamp(gesture.startOffset + Math.round(dx / candle), limits.min, limits.max);
    scheduleWorkspaceRender('all');
  } else if (gesture.mode === 'zoom-box') {
    showZoomSelection(gesture.start, point);
  } else if ((gesture.mode === 'object-move' || gesture.mode === 'object-handle') && gesture.moved) {
    const object = userObjects.find(item => item.id === gesture.objectId);
    moveObjectFromOriginal(object, gesture.original, gesture.start, point, gesture.mode === 'object-handle' ? gesture.handle : null);
    scheduleWorkspaceRender('overlay');
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
    undoStack.push(gesture.before);indicatorUndoStack.push(structuredClone(indicatorSettings));
    if(undoStack.length>30){undoStack.shift();indicatorUndoStack.shift();}
    redoStack = [];indicatorRedoStack=[]; updateUndoButtons(); flushWorkspaceRender('overlay'); savePrototypeState(); updateObjectTree();
    showToast(gesture.mode === 'object-handle' ? '结构端点已调整并保存' : '图形位置已移动并保存');
  } else if ((gesture.mode === 'object-move' || gesture.mode === 'object-handle') && !gesture.moved) selectObject(gesture.objectId, point.x, point.y);
  if (gesture.moved && ['price-scale', 'time-scale', 'pan'].includes(gesture.mode)) savePrototypeState();
  gesture = null;
});

function cancelChartGesture() {
  const overlayOnly=gesture?.mode==='object-move'||gesture?.mode==='object-handle';
  if(gesture?.before)userObjects=gesture.before;
  if(gesture?.mode==='price-scale')priceZoom=gesture.startZoom;
  if(gesture?.mode==='time-scale')viewCount=gesture.startCount;
  if(gesture?.mode==='pan')viewOffset=gesture.startOffset;
  gesture=null;hideZoomSelection();flushWorkspaceRender(overlayOnly?'overlay':'all');
}
canvas.addEventListener('pointercancel', cancelChartGesture);
canvas.addEventListener('mouseleave', () => { crosshair.classList.remove('show'); updateReadout(bars[Math.min(replayIndex, bars.length - 1)]); });
canvas.addEventListener('wheel', event => {
  event.preventDefault();
  if(event.shiftKey){panChart(Math.sign(event.deltaY)*Math.max(1,Math.round(viewCount*.08)),true);return;}
  const view = canvas._view;
  const rect = canvas.getBoundingClientRect();
  // TV's ordinary wheel keeps the right edge; Ctrl/Meta focuses the cursor.
  const ratio = event.ctrlKey || event.metaKey
    ? clamp((event.clientX - rect.left - view.left) / view.plotWidth, 0, 1) : 1;
  zoomAround(Math.exp(event.deltaY * .0016), ratio, true);
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
    priceZoom = 1; priceCenterOffset = 0; renderWorkspace();savePrototypeState();showToast('价格轴已恢复自动刻度'); return;
  }
  if(point.y>view.height-view.bottom){viewCount=Math.min(DEFAULT_VISIBLE_BARS,replayIndex+1);viewOffset=0;renderWorkspace();savePrototypeState();return;}
  const id = hitObject(point.x, point.y);
  if (id === 'strategy-evidence') { showToast('策略证据为只读'); return; }
  if (id) { selectObject(id, point.x, point.y); openObjectSettings(); }
});

document.querySelectorAll('.tool[data-tool], [data-favorite]').forEach(button => {
  button.addEventListener('click', () => startTool(button.dataset.favorite || button.dataset.tool, button));
});

const favoriteToolbar = document.querySelector('#favoriteTools');
const savedToolbar = loadPrototypeState().toolbar;
let favoriteToolbarMoved=Boolean(savedToolbar?.userMoved ?? savedToolbar?.left);
if (savedToolbar?.left) { favoriteToolbar.style.left = savedToolbar.left; favoriteToolbar.style.top = savedToolbar.top; }
document.querySelector('#favoriteDragHandle').addEventListener('pointerdown', event => {
  event.preventDefault(); event.stopPropagation();
  favoriteToolbarMoved=true;
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

function commonLineSettings(object, label = '线条') {
  return `<div class="setting-row line-setting-row"><span>${object.type==='ray'?'线形图':label}</span><span class="stroke-preview-button" data-stroke-preview="line"><i class="stroke-preview-color" style="background:${object.color||'#2962ff'}"></i><i class="stroke-preview-line" style="border-top-width:${object.width||1}px;border-top-style:${object.dash||'solid'}"></i><input type="color" data-setting="color" data-stroke-width="width" data-stroke-dash="dash" value="${object.color||'#2962ff'}" aria-label="${object.type==='ray'?'射线':label}颜色、粗细和线型"></span></div>`;
}

function paletteSettings(object) {
  return `<div class="setting-row"><span>颜色</span><input type="color" data-setting="color" value="${object.color||'#2962ff'}" aria-label="选择完整色板"></div>`;
}

function commitObjectSetting(object, key, value) {
  rememberObjects(); object[key] = value; savePrototypeState(); renderWorkspace('overlay');
}

function refreshFibStylePreviews(object) {
  const host = document.querySelector('#toolSpecificSettings');
  host.querySelectorAll('[data-fib-level-color]').forEach(input => {
    input.value = object.fibUseOneColor ? object.fibSingleColor || object.color : object.fibLevels[Number(input.dataset.fibLevelColor)].color;
  });
  host.querySelectorAll('[data-stroke-preview]').forEach(button => {
    const trend = button.dataset.strokePreview === 'trend';
    const line = button.querySelector('.stroke-preview-line');
    if(button.dataset.strokePreview==='background'){
      const enabled=['rectangle','channel'].includes(object.type)?object.fill!==false:object.fill===true;
      button.classList.toggle('disabled',!enabled);button.querySelector('input').disabled=!enabled;
      button.querySelector('.stroke-preview-color').style.background=object.backgroundColor||object.color;return;
    }
    if(button.dataset.strokePreview==='middle'){
      const enabled=Boolean(object.showMiddle);
      button.classList.toggle('disabled',!enabled);button.querySelector('input').disabled=!enabled;
      line.style.borderTopWidth=(object.middleWidth||1)+'px';line.style.borderTopStyle=object.middleDash||'dashed';
      button.querySelector('.stroke-preview-color').style.background=object.middleColor||object.color;return;
    }
    if(button.dataset.strokePreview==='channel'){
      const level=object.channelLevels[Number(button.closest('[data-channel-index]').dataset.channelIndex)];
      line.style.borderTopWidth=(level.width||1)+'px';line.style.borderTopStyle=level.dash||'solid';
      button.querySelector('.stroke-preview-color').style.background=level.color||object.color;return;
    }
    if(button.dataset.strokePreview==='line'){
      line.style.borderTopWidth=(object.width||1)+'px';line.style.borderTopStyle=object.dash||'solid';
      button.querySelector('.stroke-preview-color').style.background=object.color;return;
    }
    line.style.borderTopWidth = (object[trend ? 'fibTrendWidth' : 'fibHorizontalWidth'] || object.width || 1) + 'px';
    line.style.borderTopStyle = object[trend ? 'fibTrendDash' : 'fibHorizontalDash'] || (trend ? 'dashed' : object.dash || 'solid');
    if(button.dataset.fibStroke==='fibHorizontalWidth')line.style.borderTopStyle='solid';
    if(button.dataset.fibStroke==='fibHorizontalDash')line.style.borderTopWidth='1px';
    if (trend) {
      button.classList.toggle('disabled', !object.fibTrendLine);
      button.querySelector('input').disabled = !object.fibTrendLine;
      button.querySelector('.stroke-preview-color').style.background = object.color;
    }
  });
}

function renderFibSettings(object) {
  ensureFibSettings(object);
  const host = document.querySelector('#toolSpecificSettings');
  const levels = object.fibLevels.map((level, index) => `
    <label class="fib-level-cell ${level.enabled ? '' : 'disabled'}">
      <input type="checkbox" data-fib-level-enabled="${index}" ${level.enabled ? 'checked' : ''} aria-label="启用 ${level.value} 水平位">
      <input type="number" step="any" data-fib-level-value="${index}" value="${level.value}" ${level.enabled ? '' : 'disabled'} aria-label="水平位 ${level.value}">
      <input type="color" data-fib-level-color="${index}" value="${object.fibUseOneColor ? object.fibSingleColor || object.color : level.color}" ${level.enabled ? '' : 'disabled'} aria-label="水平位 ${level.value} 颜色">
    </label>`).join('');
  host.innerHTML = `<div class="fib-setting-stack">
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibTrendLine" ${object.fibTrendLine ? 'checked' : ''}>趋势线</label><span class="stroke-preview-button" data-stroke-preview="trend"><i class="stroke-preview-color"></i><i class="stroke-preview-line"></i><input type="color" data-fib-setting="color" data-stroke-width="fibTrendWidth" data-stroke-dash="fibTrendDash" value="${object.color || '#4caf50'}" aria-label="趋势线颜色、粗细和线型"></span></div>
    <div class="fib-setting-row"><b>水平线</b><button type="button" class="stroke-preview-button" data-stroke-preview="horizontal" data-fib-stroke="fibHorizontalWidth" aria-label="水平线粗细"><i class="stroke-preview-line"></i></button><button type="button" class="stroke-preview-button stroke-dash-button" data-stroke-preview="horizontal" data-fib-stroke="fibHorizontalDash" aria-label="水平线线型"><i class="stroke-preview-line"></i></button></div>
    <div class="fib-setting-row"><b>延伸</b><select class="fib-wide-select" data-fib-setting="fibExtend" aria-label="延伸方式"><option value="none">不要扩大</option><option value="left">向左延伸</option><option value="right">向右延伸</option><option value="both">双向延伸</option></select></div>
    <div class="fib-level-grid">${levels}</div>
    <div class="fib-setting-row"><b>使用一种颜色</b><input type="color" data-fib-setting="fibSingleColor" value="${object.fibSingleColor || object.color || '#4caf50'}" aria-label="斐波那契单一颜色"></div>
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibBackground" ${object.fibBackground ? 'checked' : ''}>背景</label><input type="range" min="0" max="100" data-fib-setting="fibBackgroundAlpha" value="${Math.round((object.fibBackgroundAlpha ?? .12) * 100)}" aria-label="背景透明度"><span>${Math.round((object.fibBackgroundAlpha ?? .12) * 100)}%</span></div>
    <div class="fib-setting-row compact"><label><input type="checkbox" data-fib-setting="fibReverse" ${object.fibReverse ? 'checked' : ''}>反手</label></div>
    <div class="fib-setting-row compact"><label><input type="checkbox" data-fib-setting="fibShowPrices" ${object.fibShowPrices === false ? '' : 'checked'}>价格</label></div>
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibShowLevels" ${object.fibShowLevels === false ? '' : 'checked'}>水平位</label><select data-fib-setting="fibLevelFormat" aria-label="水平位格式"><option value="value">数值</option><option value="percent">百分比</option></select></div>
    <div class="fib-setting-row"><b>标签</b><select data-fib-setting="fibLabelHorizontal" aria-label="标签水平位置"><option value="left">左</option><option value="center">中心</option><option value="right">右</option></select><select data-fib-setting="fibLabelVertical" aria-label="标签垂直位置"><option value="top">顶部</option><option value="middle">中间</option><option value="bottom">底部</option></select></div>
    <div class="fib-setting-row"><label><input type="checkbox" data-fib-setting="fibText" ${object.fibText === false ? '' : 'checked'}>文本</label><select data-fib-setting="fibTextHorizontal" aria-label="文本水平位置"><option value="left">左</option><option value="center">中心</option><option value="right">右</option></select><select data-fib-setting="fibTextVertical" aria-label="文本垂直位置"><option value="top">顶部</option><option value="middle">中间</option><option value="bottom">底部</option></select></div>
    <div class="fib-setting-row"><b>字体大小</b><select data-fib-setting="fibFontSize" aria-label="字体大小"><option value="10">10</option><option value="11">11</option><option value="12">12</option><option value="14">14</option><option value="16">16</option></select></div>
    <div class="fib-setting-row compact"><label title="与TV一致：仅对数价格轴可用；当前为线性轴"><input type="checkbox" data-fib-setting="fibLog" ${object.fibLog ? 'checked' : ''} disabled>基于对数坐标的Fib水平</label></div>
  </div>`;
  ['fibTrendDash', 'fibTrendWidth', 'fibHorizontalWidth', 'fibHorizontalDash', 'fibExtend', 'fibLevelFormat', 'fibLabelHorizontal', 'fibLabelVertical', 'fibTextHorizontal', 'fibTextVertical', 'fibFontSize'].forEach(key => {
    const control = host.querySelector(`[data-fib-setting="${key}"]`);
    if (control) control.value = String(object[key] ?? ({ fibTrendDash: 'dashed', fibTrendWidth: object.width || 1, fibHorizontalWidth: object.width || 1, fibHorizontalDash: object.dash || 'solid' }[key] || ''));
  });
  refreshFibStylePreviews(object);
  host.querySelectorAll('[data-fib-stroke]').forEach(button => {
    button.onclick = () => window.openChartStrokePicker(object, button.dataset.fibStroke, button);
  });
  host.querySelectorAll('[data-fib-level-enabled]').forEach(control => control.addEventListener('change', () => {
    const index = Number(control.dataset.fibLevelEnabled); rememberObjects(); object.fibLevels[index].enabled = control.checked;
    savePrototypeState(); renderFibSettings(object); renderWorkspace('overlay');
  }));
  host.querySelectorAll('[data-fib-level-value]').forEach(control => control.addEventListener('input', () => {
    const index = Number(control.dataset.fibLevelValue); commitObjectSetting(object.fibLevels[index], 'value', Number(control.value));
  }));
  host.querySelectorAll('[data-fib-level-color]').forEach(control => control.addEventListener('change', () => {
    const index = Number(control.dataset.fibLevelColor); rememberObjects();materializeFibUniformColor(object);object.fibLevels[index].color = control.value; refreshFibStylePreviews(object);savePrototypeState(); renderWorkspace('overlay');
  }));
  host.querySelectorAll('[data-fib-setting]').forEach(control => control.addEventListener('change', () => {
    const key = control.dataset.fibSetting;
    let value = control.type === 'checkbox' ? control.checked : control.value;
    if (key === 'fibHorizontalWidth' || key === 'fibTrendWidth' || key === 'fibFontSize') value = Number(value);
    if (key === 'fibBackgroundAlpha') value = Number(value) / 100;
    if (key === 'fibSingleColor') {
      rememberObjects();assignFibUniformColor(object, value);savePrototypeState();renderWorkspace('overlay');
    } else commitObjectSetting(object, key, value);
    refreshFibStylePreviews(object);
    if (key === 'color') document.querySelector('.color-swatch').style.background = value;
  }));
}

function renderToolSpecificSettings(object) {
  if(object.type==='fib'){renderFibSettings(object);return;}
  if(['long','short'].includes(object.type)){renderPositionStyle(object);return;}
  if(object.type==='trend'||(object.type==='arrow'&&!object.arrow_marker_version)){renderTrendSettings(object);return;}
  if(object.type==='channel'){renderChannelSettings(object);return;}
  const host=document.querySelector('#toolSpecificSettings');
  const marker=['up','down','text'].includes(object.type)||Boolean(object.arrow_marker_version);
  let extra='';
  if(object.type==='rectangle'||(object.type==='path'&&!object.path_version))extra=`
    <div class="setting-row"><label><input type="checkbox" data-setting="fill" ${object.fill===true||(object.type==='rectangle'&&object.fill!==false)?'checked':''}>背景</label><span class="stroke-preview-button fill-preview-button" data-stroke-preview="background"><i class="stroke-preview-color" style="background:${object.backgroundColor||object.color}"></i><input type="color" data-setting="backgroundColor" value="${object.backgroundColor||object.color}" aria-label="背景颜色和不透明度"></span></div>`;
  if(object.type==='rectangle')extra+=`
    <div class="setting-row"><label><input type="checkbox" data-setting="showMiddle" ${object.showMiddle?'checked':''}>中线</label><span class="stroke-preview-button" data-stroke-preview="middle"><i class="stroke-preview-color" style="background:${object.middleColor||object.color}"></i><i class="stroke-preview-line" style="border-top-width:${object.middleWidth||1}px;border-top-style:${object.middleDash||'dashed'}"></i><input type="color" data-setting="middleColor" data-stroke-width="middleWidth" data-stroke-dash="middleDash" value="${object.middleColor||object.color}" aria-label="中线颜色、粗细和线型"></span></div>`;
  if(object.type==='ray')extra='<label><input type="checkbox" data-setting="showPrice" '+(object.showPrice===false?'':'checked')+'>价格标签</label>';
  if(object.type==='path')extra+=`${!object.path_version?`<label><input type="checkbox" data-setting="closed" ${object.closed?'checked':''}>兼容封闭图形</label>`:''}
    <div class="setting-row"><span>起点／终点</span>${['startStyle','endStyle'].map(key=>`<select data-setting="${key}" aria-label="${key==='startStyle'?'起点':'终点'}样式">${[['none','普通'],['arrow','箭头']].map(([value,label])=>`<option value="${value}" ${(object[key]||(key==='endStyle'&&object.path_version?'arrow':'none'))===value?'selected':''}>${label}</option>`).join('')}</select>`).join('')}</div>
    <p class="notice">路径 · ${object.points?.length||2} 个转折点，可逐点拖动；双击或 Esc 结束绘制。</p>`;
  if(object.type==='ruler')extra='<label><input type="checkbox" data-setting="showLabel" '+(object.showLabel===false?'':'checked')+'>价格、涨跌幅和K线数</label>';
  if(object.type==='text')extra=`<textarea data-setting="text" aria-label="笔记内容">${escapeChartHtml(object.text||'')}</textarea>`;
  const extensionLabel=()=>object.extendLeft?(object.extendRight?'双向延伸':'向左延伸'):object.extendRight?'向右延伸':'不要扩大';
  const extension=object.type==='rectangle'?`<div class="setting-row"><span>延伸</span><details class="position-stats rectangle-extend"><summary><span data-extension-label>${extensionLabel()}</span><span>⌄</span></summary><div class="stats-menu">
    <label><input type="checkbox" data-setting="extendLeft" ${object.extendLeft?'checked':''}>向左延伸</label>
    <label><input type="checkbox" data-setting="extendRight" ${object.extendRight?'checked':''}>向右延伸</label>
    </div></details></div>`:'';
  host.innerHTML=`<div class="tool-setting-stack">${extension}${marker?paletteSettings(object):commonLineSettings(object,object.type==='rectangle'?'边框':'线条')}${extra}</div>`;
  bindDrawingStyle(host,object);
  refreshFibStylePreviews(object);
  host.querySelectorAll('.rectangle-extend input').forEach(control=>control.addEventListener('change',()=>{
    host.querySelector('[data-extension-label]').textContent=extensionLabel();
  }));
}

function renderCoordinateSettings(object) {
  const host = document.querySelector('#coordinateSettings');
  const points = object.type==='path' && object.points?.length
    ? object.points.map((point,index)=>({key:`point:${index}`,point}))
    : [{key:'a',point:object.a}, ...(!onePointTools.has(object.type)?[{key:'b',point:object.b||object.a}]:[]),
       ...(object.type==='channel' && object.c?[{key:'c',point:object.c}]:[])];
  host.innerHTML = `<div class="coordinate-rows">${points.map(({key,point},index)=>`
    <div class="coordinate-row"><b>#${index+1}（价格，K线）</b><input type="number" min="0.000001" step="any" data-coordinate-point="${key}" data-coordinate-field="value" value="${drawingPriceInput(point.value)}" aria-label="#${index+1} 价格"><input type="number" step="1" min="${chartCoordinates.bounds(bars,activeTimeframeUnit).min+1}" max="${chartCoordinates.bounds(bars,activeTimeframeUnit).max+1}" data-coordinate-point="${key}" data-coordinate-field="index" value="${Math.round(point.index)+1}" aria-label="#${index+1} K线"></div>`).join('')}
    ${['long','short'].includes(object.type)?`<label>止损价格<input id="positionStopPrice" type="number" min="0.000001" step="any" value="${object.stop}"></label>`:''}</div>`;
  host.querySelector('#positionStopPrice')?.addEventListener('change',event=>{
    const value=Number(event.target.value);if(!Number.isFinite(value)||value<=0){showToast('止损价格必须为正数');return;}
    rememberObjects();object.stop=value;savePrototypeState();renderWorkspace('overlay');
  });
  host.querySelectorAll('[data-coordinate-field]').forEach(control => control.addEventListener('change', () => {
    const value=Number(control.value);
    const limits=chartCoordinates.bounds(bars,activeTimeframeUnit);
    const isIndex=control.dataset.coordinateField==='index';
    if(!Number.isFinite(value)||(isIndex?(!Number.isInteger(value)||value-1<limits.min||value-1>limits.max):value<=0)){
      showToast('请输入有效的价格或K线序号');return;
    }
    rememberObjects();
    const pointKey = control.dataset.coordinatePoint;
    const point = pointKey.startsWith('point:') ? object.points[Number(pointKey.split(':')[1])] : object[pointKey];
    point[control.dataset.coordinateField] = control.dataset.coordinateField === 'index' ? value-1 : value;
    if (control.dataset.coordinateField === 'index') delete point.date;
    if(object.type==='path'){object.a=object.points[0];object.b=object.points.at(-1);}
    savePrototypeState(); renderWorkspace('overlay');
  }));
}

function renderVisibilitySettings(object) {
  const host = document.querySelector('#visibilitySettings');
  if (object.type !== 'fib') {
    object.visibility = object.visibility || {daily:true,weekly:true,monthly:true};
    host.innerHTML = `<p class="visibility-note">在所选周期中显示</p><div class="visibility-grid">${[['daily','日'],['weekly','周'],['monthly','月']].map(([unit,label])=>`<label><input data-visible-unit="${unit}" type="checkbox" ${object.visibility[unit]===false?'':'checked'}>${label}</label>`).join('')}</div><p class="notice">分钟周期待真实数据源接入</p>`;
    host.querySelectorAll('[data-visible-unit]').forEach(control=>control.addEventListener('change',()=>{
      rememberObjects();object.visibility[control.dataset.visibleUnit]=control.checked;savePrototypeState();renderWorkspace('overlay');
    }));
    return;
  }
  ensureFibSettings(object);
  const labels = { ticks: 'ticks', seconds: '秒', minutes: '分钟', hours: '小时', days: '日', weeks: '周', months: '个月', ranges: 'Ranges' };
  host.innerHTML = `<div class="fib-visibility-list">${Object.entries(labels).map(([key, label]) => {
    const setting = object.fibVisibility[key];
    const limit=fibVisibilityDefaults[key].max;
    const bounds = setting.min === undefined ? '' : `<input type="number" min="1" max="${setting.max}" data-fib-visibility-bound="min" value="${setting.min}" ${setting.enabled ? '' : 'disabled'} aria-label="${label}最小值"><span class="visibility-range-track"></span><input type="number" min="${setting.min}" max="${limit}" data-fib-visibility-bound="max" value="${setting.max}" ${setting.enabled ? '' : 'disabled'} aria-label="${label}最大值">`;
    return `<div class="fib-visibility-row" data-fib-visibility-key="${key}"><label><input type="checkbox" data-fib-visibility-enabled ${setting.enabled ? 'checked' : ''}>${label}</label>${bounds}</div>`;
  }).join('')}</div>`;
  host.querySelectorAll('[data-fib-visibility-key]').forEach(row => {
    const setting = object.fibVisibility[row.dataset.fibVisibilityKey];
    row.querySelector('[data-fib-visibility-enabled]').addEventListener('change', event => {
      rememberObjects(); setting.enabled = event.target.checked; savePrototypeState(); renderVisibilitySettings(object); renderWorkspace('overlay');
    });
    row.querySelectorAll('[data-fib-visibility-bound]').forEach(control => control.addEventListener('input', () => {
      const key=control.dataset.fibVisibilityBound,value=Number(control.value),limit=fibVisibilityDefaults[row.dataset.fibVisibilityKey].max;
      if(!control.value||!Number.isInteger(value)||value<1||value>limit||(key==='min'?value>setting.max:value<setting.min))return;
      rememberObjects(); setting[key] = value;savePrototypeState(); renderWorkspace('overlay');
    }));
  });
}

function openObjectSettings() {
  const object = objectAtSelection();
  if (!object) { showToast('先在图上单击一个人工标绘'); return; }
  objectSettingsSnapshot = structuredClone(object);
  userObjects.forEach(item=>chartCoordinates.capture(item,bars,activeTimeframeUnit));
  objectSettingsHistory = {objects:structuredClone(userObjects),dirty:false};
  document.querySelector('#objectModal').classList.toggle('fib-object-modal', object.type === 'fib');
  document.querySelector('#objectModal').dataset.toolType = object.type;
  document.querySelector('#objectModalTitle').textContent = object.name || toolNames[object.type] || '图形设置';
  document.querySelector('#objectNameInput').hidden = true;
  document.querySelector('#objectModalTitle').hidden = false;
  configureDrawingTabs(object);
  renderToolSpecificSettings(object); renderCoordinateSettings(object); renderVisibilitySettings(object);
  openWorkspaceModal('objectModal');
}

function closeObjectSettings(revert = false) {
  if(!revert && document.querySelector('#objectModal').contains(document.activeElement))document.activeElement.blur();
  if (revert && objectSettingsSnapshot) {
    const index = userObjects.findIndex(item => item.id === objectSettingsSnapshot.id);
    if (index >= 0) userObjects[index] = structuredClone(objectSettingsSnapshot);
  }
  const history=objectSettingsHistory;objectSettingsSnapshot = null;objectSettingsHistory=null;
  if(!revert && history?.dirty){const finalObjects=userObjects;userObjects=history.objects;rememberObjects();userObjects=finalObjects;}
  closeWorkspaceModal(); savePrototypeState(); updateObjectTree(); renderWorkspace('overlay');
}

document.querySelector('#quickSettings').addEventListener('click', openObjectSettings);
document.querySelector('#quickDelete').addEventListener('click', () => {
  if (!selectedId) return;
  rememberObjects();
  userObjects = userObjects.filter(item => item.id !== selectedId);
  selectedId = null; selected = false;
  document.querySelector('#selectedObject').classList.remove('visible');
  savePrototypeState(); updateObjectTree(); renderWorkspace('overlay'); showToast('图形已删除');
});
document.querySelector('#quickColor').addEventListener('click', () => {
  const object=objectAtSelection();if(!object)return;
  if(!window.openChartColorPicker){openObjectSettings();return;}
  const input=document.createElement('input');input.type='color';input.value=object.color||'#2962ff';input.dataset.setting='color';
  input.onchange=()=>{commitObjectSetting(object,'color',input.value);document.querySelector('.color-swatch').style.background=input.value;};
  window.openChartColorPicker(input,document.querySelector('#quickColor'),object);
});
document.querySelector('#quickWidth').addEventListener('click', () => window.openChartWidthPicker ? window.openChartWidthPicker() : openObjectSettings());
document.querySelectorAll('#objectTabs button').forEach(button => button.addEventListener('click', () => {
  lastObjectTab=button.dataset.tab;
  document.querySelectorAll('#objectTabs button').forEach(item => item.classList.toggle('active', item === button));
  document.querySelectorAll('.object-tab-panel').forEach(panel => panel.classList.toggle('active', panel.dataset.panel === button.dataset.tab));
}));

function updateObjectTree() {
  const tree = document.querySelector('.object-tree');
  const escaped = value => String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  tree.innerHTML = `
    <label data-layer="strategy_visible"><input type="checkbox" ${indicatorSettings.strategy_visible !== false ? 'checked' : ''}><span class="tree-eye">◉</span><b>策略证据</b><small>只读</small></label>
    <label data-layer="ema"><input type="checkbox" ${indicatorSettings.ema ? 'checked' : ''}><span class="tree-eye">◉</span><b>EMA ${indicatorSettings.ema_length}</b><small>指标设置</small></label>
    ${userObjects.map(item => `<label data-object-id="${escaped(item.id)}" class="${item.id === selectedId ? 'selected' : ''}"><input type="checkbox" ${item.visible === false ? '' : 'checked'}><span class="tree-eye">${item.visible === false ? '○' : '◉'}</span><b>${escaped(item.name || toolNames[item.type] || item.type)}</b><button data-object-lock title="${item.locked ? '解锁图形' : '锁定图形'}" aria-pressed="${Boolean(item.locked)}"><svg><use href="#i-lock"/></svg></button></label>`).join('')}
    ${chartAlerts.map(alert => `<div class="alert-tree-row" data-alert-id="${escaped(alert.id)}"><input type="checkbox" aria-label="启用提醒" ${alert.enabled === false ? '' : 'checked'}><button data-alert-edit title="编辑提醒">${escaped(alert.name)}</button><small>${({daily:'日',weekly:'周',monthly:'月'})[alert.timeframe] || escaped(alert.timeframe)} · ${(alert.events || []).length}次</small><button data-alert-delete title="删除提醒">×</button></div>`).join('')}`;
  tree.querySelectorAll('[data-alert-id]').forEach(row => {
    row.querySelector('[data-alert-edit]').onclick = () => window.openAlertEditor(row.dataset.alertId);
    row.querySelector('input').onchange = async event => {
      try { await window.writeAlertChange('update_alert', row.dataset.alertId, {enabled:event.target.checked}); }
      catch (error) { updateObjectTree(); showToast(`提醒未更新：${error.message || error}`); }
    };
    row.querySelector('[data-alert-delete]').onclick = async () => {
      if (!confirm('移除此提醒？已保存的画线和策略证据保持不变。')) return;
      try { await window.writeAlertChange('delete_alert', row.dataset.alertId); }
      catch (error) { showToast(`提醒未删除：${error.message || error}`); }
    };
  });
  tree.querySelectorAll('[data-layer]').forEach(label => {
    label.querySelector('input').addEventListener('change', event => {
      rememberObjects(); indicatorSettings[label.dataset.layer] = event.target.checked;
      savePrototypeState(); renderWorkspace();
    });
    if (label.dataset.layer === 'ema') label.addEventListener('dblclick', () => window.openEmaSettings?.());
  });
  tree.querySelectorAll('[data-object-id]').forEach(label => {
    label.querySelector('[data-object-lock]').addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      const object = userObjects.find(item => item.id === label.dataset.objectId); if (!object) return;
      rememberObjects(); object.locked = !object.locked;
      savePrototypeState(); updateObjectTree(); renderWorkspace('overlay');
    });
    label.addEventListener('click', event => {
      if (event.target.closest('[data-object-lock]')) return;
      const object = userObjects.find(item => item.id === label.dataset.objectId); if (!object) return;
      if(event.target.matches('input')||event.target.closest('.tree-eye')){
        event.preventDefault();rememberObjects();object.visible=event.target.matches('input')?event.target.checked:object.visible===false;
        savePrototypeState();updateObjectTree();renderWorkspace('overlay');
      }
      else{event.preventDefault();selectObject(object.id,wrap.clientWidth*.55,180,false);}
    });
    label.addEventListener('dblclick', () => { selectedId = label.dataset.objectId; openObjectSettings(); });
  });
}

document.querySelectorAll('.timeframes .disabled').forEach(button => button.addEventListener('click', () => showToast(button.dataset.unavailable)));
function switchProductionTimeframe(timeframe, persist = true) {
  const target = chartBoot.series?.[timeframe];
  if (!target?.length || timeframe === activeTimeframeUnit) return false;
  const cutoff = replayActive ? replayCutoffDate || bars[replayIndex].date : null;
  const replayFrame = cutoff && chartBoot.series?.daily?.length ? AplusChartIndicators.replayFrame(chartBoot.series,timeframe,cutoff) : null;
  const lastVisible = replayFrame ? replayFrame.index : cutoff ? target.findLastIndex(row =>
    (chartBoot.seriesCloseDates?.[timeframe]?.[row.date] || row.date) <= cutoff) : target.length - 1;
  if (lastVisible < 0) {showToast('所选日期之前没有已收盘的该周期K线');return false;}
  if (replayActive && typeof stopReplayTimer === 'function') stopReplayTimer();
  userObjects.forEach(object => chartCoordinates.capture(object, bars, activeTimeframeUnit));
  bars.splice(0, bars.length, ...structuredClone(replayFrame ? replayFrame.rows : target));
  userObjects.forEach(object => chartCoordinates.projectObject(object, bars, timeframe));
  // Undo/redo snapshots carry canonical dates too; project them before restoring an edit.
  [undoStack, redoStack].forEach(stack => stack.forEach(objects =>
    objects.forEach(object => chartCoordinates.projectObject(object, bars, timeframe))));
  activeTimeframeUnit = timeframe; replayIndex = lastVisible;
  calculateEma();
  if (replayActive && typeof updateReplayUi === 'function') updateReplayUi();
  viewCount = Math.min(DEFAULT_VISIBLE_BARS, replayIndex+1, bars.length); viewOffset = 0; priceZoom = 1; priceCenterOffset = 0;
  document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.classList.toggle('active', button.dataset.timeframe === timeframe));
  const period = timeframe === 'weekly' ? '1周' : timeframe === 'monthly' ? '1月' : '1天';
  document.querySelector('.instrument-title b').textContent = `${chartBoot.name} · ${period} · ${String(chartBoot.symbol || '').startsWith('sh.') ? 'SSE' : 'SZSE'}`;
  updateReadout(bars[replayIndex]);
  document.querySelectorAll('.quote b').forEach(node=>{node.textContent=chartPrice(bars[replayIndex].close);});
  renderWorkspace(); if (persist) savePrototypeState(); return true;
}

document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.addEventListener('click', () => {
  if(button.dataset.timeframe === activeTimeframeUnit)return;
  if(switchProductionTimeframe(button.dataset.timeframe))showToast(`已切换到${button.textContent}线真实聚合行情`);
  else showToast('当前入口没有该周期的真实底层行情');
}));
document.querySelector('#openAlert').addEventListener('click', () => openWorkspaceModal('alertModal'));
document.querySelector('#openObject').addEventListener('click', openObjectSettings);
function refreshRemoveMenu() {
  const options=document.querySelectorAll('#removeModal .remove-option');
  const count=userObjects.length,indicators=indicatorSettings.ema?1:0;
  options[0].textContent=`移除${count}个绘图`;
  options[1].textContent=`移除${indicators}个指标`;
  options[2].textContent=`移除${count}个绘图和${indicators}个指标`;
  options[0].disabled=userObjects.length===0;
  options[1].disabled=!indicatorSettings.ema;
  options[2].disabled=userObjects.length===0&&!indicatorSettings.ema;
  document.querySelector('#removeLockedDrawings').checked=Boolean(indicatorSettings.remove_locked);
}
function positionRemoveMenu() {
  const menu=document.querySelector('#removeModal');
  if(!menu.classList.contains('show'))return;
  const rect=document.querySelector('#removeObjects').getBoundingClientRect();
  menu.style.left=`${clamp(rect.right+4,8,innerWidth-menu.offsetWidth-8)}px`;
  menu.style.top=`${clamp(rect.bottom-menu.offsetHeight,8,innerHeight-menu.offsetHeight-8)}px`;
}
window.addEventListener('resize',positionRemoveMenu);
document.querySelector('#removeObjects').addEventListener('click', event => {
  event.stopPropagation();refreshRemoveMenu();
  document.querySelector('#removeModal').classList.toggle('show');positionRemoveMenu();
});
document.querySelector('#removeLockedDrawings').addEventListener('change', event=>{
  indicatorSettings.remove_locked=event.target.checked;savePrototypeState();refreshRemoveMenu();
});
document.addEventListener('click',event=>{
  if(!event.target.closest('#removeModal,#removeObjects'))document.querySelector('#removeModal').classList.remove('show');
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape')document.querySelector('#removeModal').classList.remove('show');
});
document.querySelectorAll('.close-modal').forEach(button => button.addEventListener('click', closeWorkspaceModal));
document.querySelector('#closeObjectSettings').addEventListener('click', () => closeObjectSettings(false));
document.querySelector('#cancelObjectSettings').addEventListener('click', () => closeObjectSettings(true));
document.querySelector('#confirmObjectSettings').addEventListener('click', () => closeObjectSettings(false));
document.querySelector('#objectTemplate').addEventListener('click', () => {
  const object = objectAtSelection();
  if (!object) return;
  const menu = document.querySelector('#templateMenu');
  menu.classList.toggle('show');
  menu.querySelector('[data-template-action="apply"]').disabled = !drawingTemplates[object.type];
  const rect = document.querySelector('#objectTemplate').getBoundingClientRect();
  menu.style.left = `${rect.left}px`;menu.style.top = `${Math.max(4,rect.top-menu.offsetHeight-5)}px`;
});
document.querySelectorAll('[data-template-action]').forEach(button => button.addEventListener('click', () => {
  const object = objectAtSelection();if(!object)return;
  const excluded = new Set(['id','type','name','a','b','c','points','stop','createdAtIndex','createdAtDate','visible','locked','arrow_marker_version','path_version']);
  if (button.dataset.templateAction === 'save') {
    drawingTemplates[object.type] = structuredClone(Object.fromEntries(Object.entries(object).filter(([key])=>!excluded.has(key))));
    savePrototypeState();showToast('绘图模板已保存到本机');
  } else if(drawingTemplates[object.type]) {
    rememberObjects();
    const versions={arrow_marker_version:object.arrow_marker_version,path_version:object.path_version};
    Object.assign(object,structuredClone(drawingTemplates[object.type]));
    for(const [key,value] of Object.entries(versions)){if(value===undefined)delete object[key];else object[key]=value;}
    renderToolSpecificSettings(object);renderVisibilitySettings(object);
    if(['long','short'].includes(object.type))renderPositionInputs(object);
    if(['trend','ray','channel','rectangle','path','arrow'].includes(object.type))renderDrawingTextSettings(object);
    savePrototypeState();renderWorkspace('overlay');
  }
  document.querySelector('#templateMenu').classList.remove('show');
}));
document.addEventListener('click', event => {
  if(!event.target.closest('#templateMenu,#objectTemplate'))document.querySelector('#templateMenu').classList.remove('show');
});
document.querySelector('#modalBackdrop').addEventListener('click', event => {
  if (event.target.id !== 'modalBackdrop') return;
  if (document.querySelector('#objectModal').classList.contains('show')) closeObjectSettings(true);
  else closeWorkspaceModal();
});
function alertConditionKind(condition=document.querySelector('#alertCondition').value) {
  if(['enter_channel','exit_channel','inside_channel','outside_channel'].includes(condition))return 'channel';
  if(['rise','fall','rise_pct','fall_pct'].includes(condition))return 'movement';
  return 'level';
}
function syncAlertConditionFields() {
  const condition=document.querySelector('#alertCondition').value;
  const kind=['enter_channel','exit_channel','inside_channel','outside_channel'].includes(condition)?'channel':
    ['rise','fall','rise_pct','fall_pct'].includes(condition)?'movement':'level';
  document.querySelector('#alertChannelFields').hidden=kind!=='channel';
  document.querySelector('#alertMovementFields').hidden=kind!=='movement';
  document.querySelector('#alertValueField').hidden=kind==='channel';
  document.querySelector('#alertValueTitle').textContent=condition.endsWith('_pct')?'幅度（%）':kind==='movement'?'幅度':'价格';
  document.querySelector('#alertPrice').disabled=kind==='level'&&document.querySelector('#alertTarget').value==='ema';
}
document.querySelector('#alertCondition').addEventListener('change',syncAlertConditionFields);
document.querySelector('#createAlert').addEventListener('click', async () => {
  const value = Number(document.querySelector('#alertPrice').value);
  const name = document.querySelector('#alertName').value.trim();
  const condition=document.querySelector('#alertCondition').value;
  const kind=['enter_channel','exit_channel','inside_channel','outside_channel'].includes(condition)?'channel':
    ['rise','fall','rise_pct','fall_pct'].includes(condition)?'movement':'level';
  const upper=Number(document.querySelector('#alertUpper').value),lower=Number(document.querySelector('#alertLower').value);
  const lookback=Number(document.querySelector('#alertBars').value);
  if (kind!=='channel'&&(!Number.isFinite(value)||value<=0)) { showToast('请输入有效提醒数值'); return; }
  if (kind==='channel'&&(!Number.isFinite(upper)||!Number.isFinite(lower)||lower<=0||upper<=lower)) { showToast('上轨必须高于下轨，且都大于零'); return; }
  if (kind==='movement'&&(!Number.isInteger(lookback)||lookback<1||lookback>500)) { showToast('回看K线数量必须为 1～500'); return; }
  try {
    if (typeof editingAlertId !== 'undefined' && editingAlertId) {
      await window.writeAlertChange('update_alert', editingAlertId, {
        name, value, condition,
        target:document.querySelector('#alertTarget').value,
        timeframe:document.querySelector('#alertTimeframe').value,
        ema_length:Number(document.querySelector('#alertEmaLength').value),
        frequency:document.querySelector('#alertFrequency').value,
        upper, lower, bars:lookback,
        expires:document.querySelector('#alertExpires').value || null});
      editingAlertId = null; closeWorkspaceModal(); showToast('提醒设置已保存'); return;
    }
    if (chartBoot.production && window.pywebview?.api?.create_price_alert) {
      const timeframe = document.querySelector('#alertTimeframe')?.value || activeTimeframeUnit;
      const pending = bridgeSaveChain.then(() => {
        if (bridgeSaveFailed) throw new Error('请先保存当前图表修改');
        const args = [value,
          condition || 'cross_up', name,
          document.querySelector('#alertTarget')?.value || 'price', timeframe,
          Number(document.querySelector('#alertEmaLength')?.value || 20),
          document.querySelector('#alertFrequency')?.value || 'bar_close',
          document.querySelector('#alertExpires')?.value || null, upper, lower, lookback];
        if (chartBoot.sessionToken != null) args.push(chartBoot.sessionToken);
        return window.pywebview.api.create_price_alert(...args);
      });
      bridgeSaveChain = pending.then(result => {
        bridgeRevision = Number(result.revision);
        if (Array.isArray(result.alerts)) chartAlerts = result.alerts;
        else chartAlerts.push(result.alert);
        updateObjectTree();
      }).catch(error => { showToast(`提醒未保存：${error.message || error}`); });
      await pending;
    }
    closeWorkspaceModal(); showToast('收盘提醒已保存到本机');
  } catch (error) { showToast(`提醒未保存：${error.message || error}`); }
});

function escapeChartHtml(value) { return String(value).replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[character]); }
function toggleChartVisibility(scope = indicatorSettings.hide_scope) {
  rememberObjects(); indicatorSettings.hide_scope = scope;
  const keys = scope === 'all' ? ['drawings_visible','indicators_visible','strategy_visible']
    : [({drawings:'drawings_visible',indicators:'indicators_visible',strategy:'strategy_visible'})[scope]];
  const show = keys.some(key => indicatorSettings[key] === false);
  keys.forEach(key => {indicatorSettings[key] = show;});
  drawingsVisible = indicatorSettings.drawings_visible !== false;
  syncWorkspaceControls(); savePrototypeState(); updateObjectTree(); renderWorkspace();
}
function syncWorkspaceControls() {
  const locked = Boolean(indicatorSettings.lock_drawings);
  const lock = document.querySelector('[data-tool="lock"]');
  lock.classList.toggle('active', locked);lock.setAttribute('aria-pressed', String(locked));
  const hidden = indicatorSettings[({drawings:'drawings_visible',indicators:'indicators_visible',strategy:'strategy_visible'})[indicatorSettings.hide_scope]] === false;
  document.querySelector('#toggleDrawings').classList.toggle('active', hidden ||
    (indicatorSettings.hide_scope === 'all' && !drawingsVisible && indicatorSettings.indicators_visible === false && indicatorSettings.strategy_visible === false));
  document.querySelector('#favoriteTools').hidden = indicatorSettings.favorites_visible === false;
}
document.querySelector('#toggleDrawings').addEventListener('click', () => toggleChartVisibility());
document.querySelector('#visibilityMenuButton').addEventListener('click', event => {
  event.stopPropagation(); const menu = document.querySelector('#visibilityMenu');
  menu.classList.toggle('show'); const rect = event.currentTarget.getBoundingClientRect();
  menu.style.left = `${rect.right+4}px`;menu.style.top = `${Math.min(rect.top,innerHeight-menu.offsetHeight-10)}px`;
});
document.querySelectorAll('[data-hide-scope]').forEach(button => button.addEventListener('click', () => {
  toggleChartVisibility(button.dataset.hideScope);document.querySelector('#visibilityMenu').classList.remove('show');
}));
document.addEventListener('click', event => {if(!event.target.closest('#visibilityMenu'))document.querySelector('#visibilityMenu').classList.remove('show');});
document.querySelector('#togglePanel').addEventListener('click', () => {
  document.querySelector('#rightPanel').classList.toggle('collapsed');
  indicatorSettings.sidebar_open = !document.querySelector('#rightPanel').classList.contains('collapsed');
  savePrototypeState();setTimeout(renderWorkspace, 190);
});
function removeChartObjects(scope) {
  if(!['drawings','indicators','all'].includes(scope))return;
  rememberObjects();
  if(scope!=='indicators')userObjects=userObjects.filter(object=>!indicatorSettings.remove_locked&&
    (object.locked||indicatorSettings.lock_drawings));
  if(scope!=='drawings')indicatorSettings.ema=false;
  selectedId=null;document.querySelector('#selectedObject').classList.remove('visible');
  document.querySelector('#removeModal').classList.remove('show');
  savePrototypeState();updateObjectTree();renderWorkspace();
}
document.querySelectorAll('[data-remove-scope]').forEach(button=>button.addEventListener('click',()=>removeChartObjects(button.dataset.removeScope)));

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
  const cells = ['一', '二', '三', '四', '五', '六', '日'].map(day => `<span>${day}</span>`);
  for (let blank = 0; blank < mondayOffset; blank += 1) cells.push('<span></span>');
  for (let day = 1; day <= days; day += 1) {
    const date = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const source=chartBoot.series?.daily||bars;
    const disabled = date < source[0].date || date > source.at(-1).date;
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
  const savedDate = chartBoot.production ? replayBookmarkDate : localStorage.getItem(`${tvStateKey}-replay-date`);
  const savedValue = localStorage.getItem(`${tvStateKey}-replay-index`);
  const savedIndex = savedValue === null ? Number.NaN : Number(savedValue);
  const preferredIndex = replayActive ? replayIndex : savedDate ? dateAtOrBefore(savedDate) : Number.isFinite(savedIndex) && savedIndex >= 0 ? savedIndex : bars.length - 1;
  const source=chartBoot.series?.daily||bars;
  const preferred=replayActive ? replayCutoffDate || bars[replayIndex].date : savedDate || bars[clamp(preferredIndex, 0, bars.length - 1)].date;
  const date=preferred<source[0].date?source[0].date:preferred>source.at(-1).date?source.at(-1).date:preferred;
  document.querySelector('#replayStartDate').min = source[0].date;
  document.querySelector('#replayStartDate').max = source.at(-1).date;
  document.querySelector('#replayStartDate').value = date;
  replayCalendarCursor = new Date(`${date}T00:00:00`);
  renderReplayCalendar(); openWorkspaceModal('replayStartModal');
}

function updateReplayUi() {
  document.querySelector('#replayDate').textContent = '选择日期';
  document.querySelector('#replayDatePicker').title = `当前回放：${replayCutoffDate || bars[replayIndex].date}`;
  const rank = {daily:0,weekly:1,monthly:2};
  if(replayIntervalUnit !== 'auto' && rank[replayIntervalUnit] > rank[activeTimeframeUnit])replayIntervalUnit='auto';
  document.querySelector('#replayInterval').textContent = ({daily:'天',weekly:'周',monthly:'月'})[replayIntervalUnit==='auto'?activeTimeframeUnit:replayIntervalUnit];
  document.querySelector('#replayPlay').innerHTML = replayPlaying
    ? '<svg viewBox="0 0 24 24"><path d="M8 4v16M16 4v16"/></svg>'
    : '<svg viewBox="0 0 24 24"><path d="m7 4 12 8-12 8Z"/></svg>';
  document.querySelector('#replayPlay').classList.toggle('playing', replayPlaying);
  document.querySelector('#replayPlay').title = replayPlaying ? '暂停 Shift + ↓' : '播放 Shift + ↓';
  document.querySelector('#replayPlay').setAttribute('aria-label',replayPlaying?'暂停回放':'播放回放');
  document.querySelector('#replaySpeed').textContent = `${replaySpeedValue}x`;
  updateReadout(bars[replayIndex]);
  document.querySelectorAll('.quote b').forEach(node => {node.textContent=chartPrice(bars[replayIndex].close);});
}

function stopReplayTimer() {
  clearInterval(replayTimer); replayTimer = null; replayPlaying = false; updateReplayUi();
}

function stepReplay() {
  const unit = replayIntervalUnit === 'auto' ? activeTimeframeUnit : replayIntervalUnit;
  const next = AplusChartIndicators.nextReplayDate(chartBoot.series || {[activeTimeframeUnit]:bars},unit,
    replayCutoffDate || bars[replayIndex].date,chartBoot.seriesCloseDates);
  if (!next) { stopReplayTimer(); showToast('已到最后一根K线'); return; }
  replayCutoffDate=next;
  const frame=AplusChartIndicators.replayFrame(chartBoot.series || {[activeTimeframeUnit]:bars},activeTimeframeUnit,next,chartBoot.seriesCloseDates);
  bars.splice(0,bars.length,...frame.rows);replayIndex=frame.index;
  viewOffset = 0; calculateEma();updateReplayUi(); renderWorkspace();
}

function startReplayTimer() {
  clearInterval(replayTimer); replayPlaying = true; updateReplayUi();
  replayTimer = setInterval(stepReplay, Math.max(90, Math.round(1000 / replaySpeedValue)));
}

function enterReplay(index, date = null) {
  clearInterval(replayTimer);replayTimer=null;
  if (!replayActive) preReplayView = { count: viewCount, offset: viewOffset, priceZoom, priceCenterOffset, timeframe:activeTimeframeUnit };
  replayActive = true; replayIndex = clamp(index, 0, bars.length - 1); replayPlaying = false; viewOffset = 0;
  replayCutoffDate = chartBoot.seriesCloseDates?.[activeTimeframeUnit]?.[bars[replayIndex].date] || bars[replayIndex].date;
  const daily=chartBoot.series?.daily;
  if(daily?.length){
    const requested=date||replayCutoffDate;
    replayCutoffDate=(daily.findLast(row=>row.date<=requested)||daily[0]).date;
    const frame=AplusChartIndicators.replayFrame(chartBoot.series,activeTimeframeUnit,replayCutoffDate);
    bars.splice(0,bars.length,...frame.rows);replayIndex=frame.index;
  }
  viewCount = Math.min(DEFAULT_VISIBLE_BARS, replayIndex + 1);
  replayBar.classList.add('visible');
  document.querySelector('#replayWatermark').classList.add('visible');
  document.querySelector('#replayModeBadge').classList.add('visible');
  document.querySelector('#startReplay').classList.add('active-box');
  document.querySelector('#rightPanel').classList.add('replay-context');
  chartNavControls.classList.remove('visible');
  closeWorkspaceModal();calculateEma();updateReplayUi(); renderWorkspace();
  showToast(`回放从 ${replayCutoffDate} 开始；未来K线已隐藏`);
}

function leaveReplay() {
  stopReplayTimer();
  if (document.querySelector('#saveReplayChoice').checked) {
    replayBookmarkDate=replayCutoffDate || bars[replayIndex].date;
    localStorage.setItem(`${tvStateKey}-replay-date`, replayCutoffDate || bars[replayIndex].date);
  } else {replayBookmarkDate=null;localStorage.removeItem(`${tvStateKey}-replay-date`);localStorage.removeItem(`${tvStateKey}-replay-index`);}
  replayActive = false; replayIndex = bars.length - 1;
  replayCutoffDate = null;
  if(chartBoot.series?.[activeTimeframeUnit])bars.splice(0,bars.length,...structuredClone(chartBoot.series[activeTimeframeUnit]));
  if (preReplayView?.timeframe && preReplayView.timeframe !== activeTimeframeUnit) switchProductionTimeframe(preReplayView.timeframe, false);
  replayBar.classList.remove('visible');
  document.querySelector('#replayWatermark').classList.remove('visible');
  document.querySelector('#replayModeBadge').classList.remove('visible');
  document.querySelector('#startReplay').classList.remove('active-box');
  document.querySelector('#rightPanel').classList.remove('replay-context');
  if (preReplayView) {
    viewCount = preReplayView.count; viewOffset = preReplayView.offset;
    priceZoom = preReplayView.priceZoom; priceCenterOffset = preReplayView.priceCenterOffset;
  }
  updateReadout(bars.at(-1));
  document.querySelectorAll('.quote b').forEach(node => {node.textContent=chartPrice(bars.at(-1).close);});
  closeWorkspaceModal();calculateEma();renderWorkspace();savePrototypeState();showToast('已退出K线回放');
}

document.querySelector('#startReplay').addEventListener('click', openReplayDatePicker);
document.querySelector('#replayDatePicker').addEventListener('click', openReplayDatePicker);
document.querySelector('#replayModeBadge').addEventListener('click', openReplayDatePicker);
document.querySelector('#replayFirstDate').addEventListener('click', () => {
  const first=(chartBoot.series?.daily||bars)[0].date;
  document.querySelector('#replayStartDate').value=first;
  replayCalendarCursor = new Date(`${first}T00:00:00`); renderReplayCalendar();
});
document.querySelector('#replayStartDate').addEventListener('change', event => {
  replayCalendarCursor = new Date(`${event.target.value}T00:00:00`); renderReplayCalendar();
});
document.querySelector('#calendarPrevious').addEventListener('click', () => { replayCalendarCursor.setMonth(replayCalendarCursor.getMonth() - 1); renderReplayCalendar(); });
document.querySelector('#calendarNext').addEventListener('click', () => { replayCalendarCursor.setMonth(replayCalendarCursor.getMonth() + 1); renderReplayCalendar(); });
document.querySelector('#confirmReplayStart').addEventListener('click', () => {
  const date=document.querySelector('#replayStartDate').value;enterReplay(dateAtOrBefore(date),date);
});
document.querySelector('#replayPlay').addEventListener('click', () => replayPlaying ? stopReplayTimer() : startReplayTimer());
document.querySelector('#replayStep').addEventListener('click', () => { stopReplayTimer(); stepReplay(); });
document.querySelector('#replayToLatest').addEventListener('click', () => {
  stopReplayTimer();replayIndex = bars.length - 1;
  if(chartBoot.series?.[activeTimeframeUnit])bars.splice(0,bars.length,...structuredClone(chartBoot.series[activeTimeframeUnit]));
  replayIndex=bars.length-1;
  replayCutoffDate = chartBoot.series?.daily?.at(-1).date || chartBoot.seriesCloseDates?.[activeTimeframeUnit]?.[bars[replayIndex].date] || bars[replayIndex].date;
  viewOffset = 0;calculateEma();updateReplayUi();renderWorkspace();showToast('已更新到最后一根K线');
});
const replayIntervalMenu=document.querySelector('#replayIntervalMenu');
document.querySelector('#replayInterval').addEventListener('click',event=>{
  event.stopPropagation();
  const rank={daily:0,weekly:1,monthly:2}, names={auto:'自动选择',daily:'1天',weekly:'1周',monthly:'1月'};
  const options=['auto',...Object.keys(rank).filter(unit=>rank[unit]<=rank[activeTimeframeUnit] &&
    (unit===activeTimeframeUnit || chartBoot.series?.daily?.length))];
  replayIntervalMenu.innerHTML=options.map(unit=>`<button role="menuitemradio" aria-checked="${unit===replayIntervalUnit}" data-replay-interval="${unit}" class="${unit===replayIntervalUnit?'active':''}">${names[unit]}<span>${unit===replayIntervalUnit?'✓':''}</span></button>`).join('')+
    '<small class="replay-source-note">日内周期待接入真实分钟行情</small>';
  replayIntervalMenu.querySelectorAll('[data-replay-interval]').forEach(button=>button.addEventListener('click',()=>{
    stopReplayTimer();replayIntervalUnit=button.dataset.replayInterval;replayIntervalMenu.classList.remove('visible');
    document.querySelector('#replayInterval').setAttribute('aria-expanded','false');updateReplayUi();
  }));
  const visible=replayIntervalMenu.classList.toggle('visible');
  event.currentTarget.setAttribute('aria-expanded',String(visible));
});
document.addEventListener('click',event=>{if(!event.target.closest('#replayIntervalMenu')&&!event.target.closest('#replayInterval')){
  replayIntervalMenu.classList.remove('visible');document.querySelector('#replayInterval').setAttribute('aria-expanded','false');
}});
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
function cleanSnapshotCanvas() {
  if(typeof flushWorkspaceRender==='function')flushWorkspaceRender();
  const selection=selectedId;selectedId=null;renderWorkspace('overlay');
  try {const result=document.createElement('canvas'),ratio=window.devicePixelRatio||1;
    const narrow=canvas.width/ratio<800,header=narrow?68:50;
    result.width=canvas.width;result.height=canvas.height+Math.round(header*ratio);
    const ctx=result.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);
    ctx.fillStyle=css('--bg');ctx.fillRect(0,0,result.width/ratio,result.height/ratio);
    const background=typeof baseCanvas==='undefined'||!baseCanvas?canvas:baseCanvas;
    ctx.drawImage(background,0,header,canvas.width/ratio,canvas.height/ratio);
    if(background!==canvas)ctx.drawImage(canvas,0,header,canvas.width/ratio,canvas.height/ratio);
    ctx.fillStyle='#d1d4dc';ctx.font='12px "Microsoft YaHei",sans-serif';ctx.textAlign='left';
    ctx.fillText(document.querySelector('.instrument-title b').textContent,10,20);
    ctx.font='11px "Microsoft YaHei",sans-serif';
    ctx.fillText(document.querySelector('.ohlc').textContent,narrow?10:300,narrow?38:20);
    const asof=replayActive?`回放截至 ${replayCutoffDate || bars[replayIndex].date}`:`行情截至 ${bars.at(-1).date}`;
    const provenance=[asof,chartBoot.source,chartBoot.adjustment].filter(Boolean).join(' · ');
    ctx.fillStyle='#9a9ca3';ctx.fillText(provenance,10,narrow?56:38);
    if(indicatorSettings.ema&&indicatorSettings.indicators_visible!==false&&indicatorSettings['ema_'+activeTimeframeUnit]!==false){const rect=document.querySelector('.indicator-legend').getBoundingClientRect(),plot=wrap.getBoundingClientRect();
      const name=indicatorSettings.ema_inputs===false?'EMA':`EMA ${indicatorSettings.ema_length} ${indicatorSettings.ema_source}`;
      const value=indicatorSettings.ema_values===false?'':document.querySelector('.ema-value').textContent;
      ctx.fillStyle=indicatorSettings.ema_color;ctx.fillText(`${name}  ${value}`,rect.left-plot.left,rect.top-plot.top+header+14);}
    return result;
  } finally {selectedId=selection;renderWorkspace('overlay');}
}

async function copyCurrentChartSnapshot() {
  const snapshotCanvas = cleanSnapshotCanvas();
  const preview = snapshotCanvas.toDataURL('image/png');
  document.querySelector('#snapshotImage').src = preview;
  try {
    if(chartBoot.production && window.pywebview?.api?.copy_snapshot){
      await window.pywebview.api.copy_snapshot(preview);
      showToast('当前缩放范围的完整图表已复制到剪贴板');return;
    }
    const blob = await new Promise(resolve => snapshotCanvas.toBlob(resolve, 'image/png'));
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
  const day=replayActive?replayCutoffDate||bars[replayIndex].date:bars.at(-1).date;
  const link = document.createElement('a'); link.download = `Aplus-${chartBoot.displaySymbol || '002259'}-${String(day).slice(0, 10)}.png`; link.href = document.querySelector('#snapshotImage').src; link.click(); closeWorkspaceModal();
});
document.querySelectorAll('.candidate').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.candidate').forEach(item => item.classList.remove('active')); button.classList.add('active'); if (!button.textContent.includes('升达林业')) showToast('静态原型仅嵌入 002259 真实日线');
}));
document.querySelectorAll('.panel-tabs button[data-panel]').forEach(button => button.addEventListener('click', () => {
  if (chartBoot.production) return;
  document.querySelectorAll('.panel-tabs button[data-panel]').forEach(item => item.classList.toggle('active', item === button));
  document.querySelector('.panel-context span').textContent = button.dataset.panel === 'watch' ? '我的自选 · 3' : 'GAP H2 · 2026-09-30';
  showToast(button.dataset.panel === 'watch' ? '已切换到自选标的，主图保持不变' : '已切换到策略匹配，主图保持可操作');
}));

document.querySelector('#undoAction').addEventListener('click', () => {
  if (!undoStack.length) { showToast('没有可撤销的图形操作'); return; }
  redoStack.push(structuredClone(userObjects)); userObjects = undoStack.pop(); selectedId = null;
  indicatorRedoStack.push(structuredClone(indicatorSettings));
  if (indicatorUndoStack.length) indicatorSettings = indicatorUndoStack.pop();
  magnetEnabled=indicatorSettings.magnet!==false;drawingsVisible=indicatorSettings.drawings_visible!==false;
  calculateEma();
  document.querySelector('#selectedObject').classList.remove('visible'); savePrototypeState(); updateUndoButtons(); updateObjectTree(); renderWorkspace(); showToast('已撤销图形操作');
});
document.querySelector('#redoAction').addEventListener('click', () => {
  if (!redoStack.length) { showToast('没有可重做的图形操作'); return; }
  undoStack.push(structuredClone(userObjects)); userObjects = redoStack.pop(); selectedId = null;
  indicatorUndoStack.push(structuredClone(indicatorSettings));
  if (indicatorRedoStack.length) indicatorSettings = indicatorRedoStack.pop();
  magnetEnabled=indicatorSettings.magnet!==false;drawingsVisible=indicatorSettings.drawings_visible!==false;
  calculateEma();
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
  ['open', 'high', 'low', 'close'].forEach(key => { document.querySelector(`#${key}Value`).textContent = chartPrice(latest[key]); });
  const change = bars.length > 1 ? Number(latest.close) - Number(bars.at(-2).close) : 0;
  const pct = bars.length > 1 && Number(bars.at(-2).close) ? change / Number(bars.at(-2).close) * 100 : 0;
  const changeNode = document.querySelector('.ohlc .positive');
  changeNode.textContent = `${change >= 0 ? '+' : ''}${chartPrice(change)} (${change >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
  changeNode.className = change >= 0 ? 'positive' : 'negative';
  document.querySelector('.quote.sell b').textContent = chartPrice(latest.close);
  document.querySelector('.quote.buy b').textContent = chartPrice(latest.close);
  document.querySelectorAll('.quote span').forEach(label => label.textContent = '收盘参考');
  document.querySelector('.quote-buttons small').textContent = '非买卖盘报价';
  document.querySelector('.panel-context span').textContent = `${chartBoot.contextTitle} · ${String(latest.date).slice(0, 10)}`;
  document.querySelector('.panel-tabs [data-panel="strategy"]').textContent = chartBoot.contextTitle;
  document.querySelector('.candidate-list').innerHTML = `<button class="candidate active"><span class="badge ${market === '沪' ? 'sh' : 'sz'}">${market}</span><span class="candidate-name"><b></b><small></small></span><span class="candidate-price"><b></b><small></small></span></button>`;
  const candidate = document.querySelector('.candidate-list .candidate');
  candidate.querySelector('.candidate-name b').textContent = chartBoot.name;
  candidate.querySelector('.candidate-name small').textContent = `${display} · ${chartBoot.strategyType ? chartBoot.strategyType.replace('STRATEGY_', '') : chartBoot.contextTitle}`;
  candidate.querySelector('.candidate-price b').textContent = chartPrice(latest.close);
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
    [entry, stop, target].forEach((value, index) => { values[index].textContent = chartPrice(value); });
    const risk = entry > stop ? (entry - stop) / entry * 100 : Number(evidence.initial_risk_pct || 0);
    const reward = entry > stop ? (target - entry) / (entry - stop) : Number(evidence.mm_r_multiple || 0);
    const spans = card.querySelectorAll('.risk-line span');
    spans[0].textContent = `风险 ${risk.toFixed(2)}%`; spans[1].textContent = `收益 ${reward.toFixed(2)}R`;
  } else if ((chartBoot.holdingLevels || []).length) {
    card.querySelector('.decision-title span').textContent = chartBoot.contextTitle;
    card.querySelector('.decision-title b').textContent = '账本只读';
    card.querySelector('dl').innerHTML = chartBoot.holdingLevels.slice(0, 3).map(level => `<div><dt>${escapeChartHtml(level.label)}</dt><dd>${chartPrice(level.value)}</dd></div>`).join('');
    card.querySelector('.risk-line').textContent = '实际成交与参考水平来自本机账本';
  } else {
    card.querySelector('.decision-title span').textContent = chartBoot.contextTitle;
    card.querySelector('.decision-title b').textContent = '只读';
    card.querySelector('dl').innerHTML = '<div><dt>行情</dt><dd>真实日线</dd></div>';
    card.querySelector('.risk-line').textContent = '当前没有可显示的计划价位';
  }
  const alertSymbol = document.querySelector('#alertModal .select-row b');
  if (alertSymbol) alertSymbol.textContent = `${chartBoot.name} · ${display}`;
  document.querySelector('#alertPrice').value = chartPrice(latest.close);
  document.querySelector('#alertPrice').step = chartBoot.priceStep || 'any';
  document.querySelector('#alertUpper').step = chartBoot.priceStep || 'any';
  document.querySelector('#alertLower').step = chartBoot.priceStep || 'any';
  document.querySelector('#alertName').value = `${display} 穿越 ${chartPrice(latest.close)}`;
  document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.classList.toggle('active', button.dataset.timeframe === chartBoot.timeframe));
  activeTimeframeUnit = chartBoot.timeframe;
  userObjects.forEach(object=>chartCoordinates.projectObject(object,bars,activeTimeframeUnit));
  const restoredTimeframe = initialPrototypeState.timeframe || chartBoot.timeframe;
  if (restoredTimeframe !== chartBoot.timeframe && switchProductionTimeframe(restoredTimeframe, false)) {
    const savedView = initialPrototypeState.view || {};
    viewCount = clamp(Number(savedView.count) || DEFAULT_VISIBLE_BARS, 18, bars.length);
    const limits=panLimits();viewOffset=clamp(Number(savedView.offset)||0,limits.min,limits.max);
    priceZoom = clamp(Number(savedView.priceZoom) || 1, .45, 7);
    priceCenterOffset = Number(savedView.priceCenterOffset) || 0;
  }
}

window.addEventListener('keydown', event => {
  const editingText=event.target.matches?.('input,textarea,select,[contenteditable="true"]');
  const modalOpen=document.querySelector('#modalBackdrop').classList.contains('show');
  if(!editingText&&!modalOpen){
    const control=event.ctrlKey||event.metaKey;
    if(event.altKey&&event.key.toLowerCase()==='r'){event.preventDefault();resetChartView();return;}
    if(event.altKey&&event.key.toLowerCase()==='a'){event.preventDefault();document.querySelector('#openAlert').click();return;}
    if(control&&event.shiftKey&&event.key.toLowerCase()==='s'){event.preventDefault();document.querySelector('#snapshot').click();return;}
    if(replayActive&&event.shiftKey&&event.key==='ArrowDown'){event.preventDefault();replayPlaying?stopReplayTimer():startReplayTimer();return;}
    if(replayActive&&event.shiftKey&&event.key==='ArrowRight'){event.preventDefault();stopReplayTimer();stepReplay();return;}
    if(control&&['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();zoomAround(event.key==='ArrowUp'?.8:1.25,1);return;}
    if(!event.shiftKey&&!event.altKey&&['ArrowLeft','ArrowRight'].includes(event.key)){
      event.preventDefault();panChart((event.key==='ArrowLeft'?1:-1)*(control?Math.max(1,Math.round(viewCount*.12)):1));return;
    }
  }
  if((event.ctrlKey||event.metaKey)&&!editingText&&!document.querySelector('#modalBackdrop').classList.contains('show')){
    if(event.key.toLowerCase()==='z'){event.preventDefault();document.querySelector(event.shiftKey?'#redoAction':'#undoAction').click();return;}
    if(event.key.toLowerCase()==='y'){event.preventDefault();document.querySelector('#redoAction').click();return;}
  }
  if (replayActive && event.shiftKey && (event.key === '+' || event.key === '=')) {
    event.preventDefault(); replayPlaying ? stopReplayTimer() : startReplayTimer(); return;
  }
  if (event.key === 'Escape') {
    const menus=document.querySelectorAll('.chart-context-menu.show');
    if(menus.length){menus.forEach(menu=>menu.classList.remove('show'));return;}
    if(!modalOpen&&!editingText&&finishPath()){event.preventDefault();return;}
    cancelChartGesture();
    draftAnchor = null; draftCursor = null; draftPoints = []; activeDrawingTool = null; hideZoomSelection();
    if(objectSettingsSnapshot)closeObjectSettings(true);else closeWorkspaceModal();
    startTool('cross', document.querySelector('[data-tool="cross"]'));
  }
  if (!editingText && (event.key === 'Delete' || event.key === 'Backspace') && selectedId && !document.querySelector('#modalBackdrop').classList.contains('show')) {event.preventDefault();document.querySelector('#quickDelete').click();}
});

function fitFavoriteToolbar() {
  const toolbar=document.querySelector('#favoriteTools');if(toolbar.hidden)return;
  if(!favoriteToolbarMoved){toolbar.style.left='';toolbar.style.top='';}
  toolbar.style.left=`${clamp(toolbar.offsetLeft,4,Math.max(4,wrap.clientWidth-toolbar.offsetWidth-4))}px`;
  toolbar.style.top=`${clamp(toolbar.offsetTop,4,Math.max(4,wrap.clientHeight-toolbar.offsetHeight-43))}px`;
}
const workspaceResize = new ResizeObserver(() => {fitFavoriteToolbar();scheduleWorkspaceRender('all');});
workspaceResize.observe(wrap);
applyProductionContext();
if(!chartBoot.production && initialPrototypeState.timeframe && initialPrototypeState.timeframe!=='daily')
  switchProductionTimeframe(initialPrototypeState.timeframe,false);
calculateEma();
updateObjectTree(); renderWorkspace();
updateUndoButtons();

const initialState = new URLSearchParams(location.search).get('state');
if (initialState === 'alert') openWorkspaceModal('alertModal');
if (initialState === 'object') { selectedId = userObjects[1]?.id || userObjects[0]?.id; selectObject(selectedId, wrap.clientWidth * .55, 220); openObjectSettings(); }
if (initialState === 'replay') document.querySelector('#startReplay').click();
if (initialState === 'snapshot') document.querySelector('#snapshot').click();
