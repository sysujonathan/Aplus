/* Execute the real intraday update path with only its UI boundaries replaced. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const assets = path.resolve(__dirname, '../docs/prototypes/tv-workspace');
const source = fs.readFileSync(path.join(assets, 'prototype-interactions.js'), 'utf8');

const functionSource = name => {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `missing function ${name}`);
  return source.slice(start, source.indexOf('\n}', start) + 2);
};
const arrowSource = name => {
  const start = source.indexOf(`window.${name} = `);
  assert.ok(start >= 0, `missing window.${name}`);
  return source.slice(start, source.indexOf('\n};', start) + 3);
};

const minuteBar = (date, close, volume = 1000) =>
  ({date, open: close - .05, high: close + .05, low: close - .1, close, volume});

function minuteSeries(count = 60, start = '2026-10-22 10:00') {
  const [day, clock] = start.split(' ');
  let [hour, minute] = clock.split(':').map(Number);
  const rows = [];
  for (let index = 0; index < count; index += 1) {
    const stamp = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
    rows.push(minuteBar(`${day} ${stamp}`, 25 + index * .01));
    minute += 5;
    if (minute >= 60) { minute -= 60; hour += 1; }
  }
  return rows;
}

const daily = ['2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23'].map((date, index) =>
  ({date, open: 24 + index, high: 25 + index, low: 23 + index, close: 24.5 + index, volume: 1e6}));

const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {style: {}, innerHTML: '', textContent: ''});
  return elements.get(selector);
};
const renders = [];
const toasts = [];
const boot = {production: true, symbol: 'sz.002912', name: '中新赛克', timeframe: 'daily',
  series: {daily: structuredClone(daily)}, seriesCloseDates: {}, live: null};
const context = vm.createContext({
  window: {}, structuredClone, console,
  document: {querySelector: element, querySelectorAll: () => [], addEventListener: () => {}},
  localStorage: {setItem: () => {}},
  bars: structuredClone(daily), chartBoot: boot,
  userObjects: [], undoStack: [], redoStack: [], ema: [],
  activeTimeframeUnit: 'daily', replayIndex: daily.length - 1, replayActive: false,
  replayCutoffDate: null, viewCount: daily.length, viewOffset: 0,
  priceZoom: 1, priceCenterOffset: 0, DEFAULT_VISIBLE_BARS: 204, RIGHT_MARGIN_BARS: 12,
  calculateEma: () => {}, updateObjectTree: () => {}, savePrototypeState: () => {},
  renderWorkspace: mode => renders.push(mode), showToast: text => toasts.push(text),
  updateReadout: () => {}, chartPrice: value => Number(value).toFixed(2), chartAlerts: []
});
vm.runInContext(fs.readFileSync(path.join(assets, 'chart-coordinates.js'), 'utf8'), context);
context.chartCoordinates = context.window.AplusChartCoordinates;
vm.runInContext(['clamp', 'visibleWindow', 'panLimits', 'switchProductionTimeframe']
  .map(functionSource).join('\n') + '\n' + arrowSource('aplusApplyLiveBars'), context);

const apply = payload => context.window.aplusApplyLiveBars(payload);

function beginIndex() {
  return context.bars.length - context.viewCount - context.viewOffset;
}

function run() {
  const coordinates = context.chartCoordinates;
  const rows = minuteSeries();

  // ---- intraday coordinates never collapse two bars of one day ----------
  assert.equal(coordinates.indexForDate(rows, rows[2].date, 'm5'), 2);
  assert.notEqual(coordinates.indexForDate(rows, rows[2].date, 'm5'),
                  coordinates.indexForDate(rows, rows[3].date, 'm5'));
  assert.equal(coordinates.indexForDate(rows, '2026-10-22', 'm5'), -1,
               'a bare date must not match an intraday bar');
  const bounds = coordinates.bounds(rows, 'm5');
  assert.equal(Math.abs(bounds.min), 0, 'no reserved future minutes for an intraday axis');
  assert.equal(bounds.max, rows.length - 1);

  // ---- first delivery switches the chart onto real intraday bars --------
  assert.equal(apply({series: {m5: rows}, live: {source: '腾讯 5 分钟'}}), true);
  assert.equal(context.activeTimeframeUnit, 'm5');
  assert.equal(context.bars.length, rows.length);
  assert.equal(context.bars.at(-1).date, rows.at(-1).date);
  assert.equal(context.viewOffset, 0);
  assert.equal(context.chartBoot.series.m5.length, rows.length);
  assert.equal(context.chartBoot.seriesCloseDates.m5[rows.at(-1).date], rows.at(-1).date);
  assert.equal(context.chartBoot.live.source, '腾讯 5 分钟');
  assert.ok(toasts.some(text => text.includes('5 分钟')));

  // ---- newest pinned: one more bar shifts everything left ---------------
  context.viewCount = 20;
  context.viewOffset = 0;
  const before = context.bars.slice(-20).map(row => row.date);
  const next = [...rows, minuteBar('2026-10-22 15:05', 26.2)];
  assert.equal(apply({series: {m5: next}}), true);
  assert.equal(context.viewOffset, 0, 'a pinned view stays pinned to the newest bar');
  assert.equal(context.bars.length, rows.length + 1);
  assert.equal(context.bars.at(-1).date, '2026-10-22 15:05');
  // The visible window advances by exactly one bar: that is the leftward slide.
  assert.deepEqual(context.bars.slice(-20).map(row => row.date), before.slice(1).concat('2026-10-22 15:05'));
  assert.equal(renders.at(-1), 'all', 'one canvas frame per tick, never a page reload');

  // ---- reading history: the anchored bar must not drift ----------------
  context.viewCount = 20;
  context.viewOffset = 12;
  const anchor = context.bars[beginIndex()].date;
  assert.ok(anchor);
  const grown = [...next, minuteBar('2026-10-22 15:10', 26.3), minuteBar('2026-10-22 15:15', 26.4)];
  assert.equal(apply({series: {m5: grown}}), true);
  assert.equal(context.bars[beginIndex()].date, anchor,
               'an anchored window keeps the same left-hand bar');
  assert.equal(context.viewOffset, 14, 'offset absorbs the two inserted bars');

  // ---- nothing is drawn while replay is frozen -------------------------
  context.replayActive = true;
  const frozen = context.bars.map(row => row.date).join();
  apply({series: {m5: grown.concat(minuteBar('2026-10-22 15:20', 26.5))}});
  assert.equal(context.bars.map(row => row.date).join(), frozen);
  assert.equal(context.chartBoot.series.m5.length, grown.length + 1,
               'the cached series still advances for the next real frame');
  context.replayActive = false;

  // ---- unusable payloads are refused, not partially applied ------------
  for (const payload of [undefined, null, {}, {series: {}}, {series: {m5: [minuteBar('2026-10-22 15:25', 26.6)]}}]) {
    assert.equal(apply(payload), false);
  }

  console.log(JSON.stringify({ok: true, renders: renders.length, toasts, bars: context.bars.length}));
}

try {
  run();
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
