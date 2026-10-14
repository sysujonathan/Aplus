const bars = [{"date":"2026-06-22","open":4.25,"high":4.25,"low":4.02,"close":4.14,"volume":22044135},{"date":"2026-06-23","open":4.12,"high":4.2,"low":4.1,"close":4.14,"volume":16185800},{"date":"2026-06-24","open":4.14,"high":4.17,"low":3.99,"close":4.07,"volume":18753300},{"date":"2026-06-25","open":4.03,"high":4.29,"low":3.97,"close":4.02,"volume":21442443},{"date":"2026-06-26","open":3.99,"high":4.07,"low":3.9,"close":3.9,"volume":17242500},{"date":"2026-06-29","open":3.9,"high":3.93,"low":3.71,"close":3.8,"volume":18393000},{"date":"2026-06-30","open":3.84,"high":4,"low":3.74,"close":3.79,"volume":19185225},{"date":"2026-07-01","open":3.8,"high":3.94,"low":3.77,"close":3.88,"volume":20069503},{"date":"2026-07-02","open":3.86,"high":4.02,"low":3.86,"close":3.92,"volume":19850802},{"date":"2026-07-03","open":3.97,"high":4.01,"low":3.91,"close":3.96,"volume":14266100},{"date":"2026-07-06","open":3.97,"high":3.97,"low":3.78,"close":3.8,"volume":13655104},{"date":"2026-07-07","open":3.78,"high":3.82,"low":3.62,"close":3.66,"volume":15052300},{"date":"2026-07-08","open":3.65,"high":3.68,"low":3.57,"close":3.6,"volume":12587405},{"date":"2026-07-09","open":3.56,"high":3.67,"low":3.54,"close":3.62,"volume":13232800},{"date":"2026-07-10","open":3.6,"high":3.74,"low":3.53,"close":3.63,"volume":15172700},{"date":"2026-07-13","open":3.61,"high":3.7,"low":3.48,"close":3.48,"volume":15514261},{"date":"2026-07-14","open":3.48,"high":3.71,"low":3.48,"close":3.69,"volume":20871461},{"date":"2026-07-15","open":3.69,"high":4,"low":3.63,"close":3.74,"volume":23065172},{"date":"2026-07-16","open":3.72,"high":3.82,"low":3.66,"close":3.76,"volume":16678800},{"date":"2026-07-17","open":3.76,"high":3.82,"low":3.51,"close":3.54,"volume":20416672},{"date":"2026-07-20","open":3.61,"high":3.65,"low":3.38,"close":3.45,"volume":18443200},{"date":"2026-07-21","open":3.41,"high":3.46,"low":3.14,"close":3.37,"volume":20787600},{"date":"2026-07-22","open":3.36,"high":3.45,"low":3.25,"close":3.35,"volume":15208500},{"date":"2026-07-23","open":3.33,"high":3.48,"low":3.32,"close":3.47,"volume":21113500},{"date":"2026-07-24","open":3.47,"high":3.52,"low":3.34,"close":3.48,"volume":15496267},{"date":"2026-07-27","open":3.43,"high":3.54,"low":3.37,"close":3.51,"volume":19584064},{"date":"2026-07-28","open":3.53,"high":3.6,"low":3.49,"close":3.55,"volume":15408800},{"date":"2026-07-29","open":3.56,"high":3.69,"low":3.53,"close":3.65,"volume":17248900},{"date":"2026-07-30","open":3.67,"high":3.67,"low":3.48,"close":3.53,"volume":14790743},{"date":"2026-07-31","open":3.54,"high":3.78,"low":3.53,"close":3.75,"volume":23953101},{"date":"2026-08-03","open":3.75,"high":3.88,"low":3.66,"close":3.85,"volume":17678560},{"date":"2026-08-04","open":3.9,"high":3.93,"low":3.81,"close":3.86,"volume":11793400},{"date":"2026-08-05","open":3.92,"high":3.98,"low":3.85,"close":3.9,"volume":18130500},{"date":"2026-08-06","open":3.85,"high":4,"low":3.8,"close":3.97,"volume":14586900},{"date":"2026-08-07","open":3.98,"high":4.06,"low":3.91,"close":4.01,"volume":15303100},{"date":"2026-08-10","open":4.03,"high":4.14,"low":4,"close":4.1,"volume":13643500},{"date":"2026-08-11","open":4.09,"high":4.1,"low":3.99,"close":4.01,"volume":11210000},{"date":"2026-08-12","open":4.01,"high":4.05,"low":3.97,"close":4.02,"volume":10078896},{"date":"2026-08-13","open":4.04,"high":4.05,"low":3.94,"close":3.97,"volume":10625640},{"date":"2026-08-14","open":3.99,"high":3.99,"low":3.89,"close":3.92,"volume":9108200},{"date":"2026-08-17","open":3.93,"high":3.97,"low":3.87,"close":3.95,"volume":8399100},{"date":"2026-08-18","open":3.94,"high":3.97,"low":3.76,"close":3.83,"volume":16665500},{"date":"2026-08-19","open":3.78,"high":3.85,"low":3.65,"close":3.7,"volume":20156156},{"date":"2026-08-20","open":3.73,"high":3.93,"low":3.69,"close":3.76,"volume":23018400},{"date":"2026-08-21","open":3.76,"high":3.87,"low":3.71,"close":3.85,"volume":11803300},{"date":"2026-08-24","open":3.85,"high":3.88,"low":3.76,"close":3.81,"volume":11490000},{"date":"2026-08-25","open":3.79,"high":4.19,"low":3.78,"close":4.19,"volume":28564724},{"date":"2026-08-26","open":4.21,"high":4.24,"low":4.07,"close":4.11,"volume":36616274},{"date":"2026-08-27","open":4.07,"high":4.17,"low":4.07,"close":4.13,"volume":18754652},{"date":"2026-08-28","open":4.13,"high":4.42,"low":4.11,"close":4.37,"volume":31558820},{"date":"2026-08-31","open":4.38,"high":4.4,"low":4.25,"close":4.3,"volume":20654700},{"date":"2026-09-01","open":4.35,"high":4.37,"low":4.3,"close":4.34,"volume":14103700},{"date":"2026-09-02","open":4.38,"high":4.42,"low":4.27,"close":4.39,"volume":13014900},{"date":"2026-09-03","open":4.42,"high":4.43,"low":4.25,"close":4.28,"volume":11924900},{"date":"2026-09-04","open":4.28,"high":4.32,"low":4.24,"close":4.27,"volume":9496100},{"date":"2026-09-07","open":4.29,"high":4.31,"low":4.2,"close":4.28,"volume":7551260},{"date":"2026-09-08","open":4.31,"high":4.44,"low":4.29,"close":4.41,"volume":14719300},{"date":"2026-09-09","open":4.41,"high":4.56,"low":4.37,"close":4.41,"volume":18762500},{"date":"2026-09-10","open":4.39,"high":4.45,"low":4.34,"close":4.35,"volume":12362800},{"date":"2026-09-11","open":4.33,"high":4.36,"low":4.12,"close":4.21,"volume":13514924},{"date":"2026-09-14","open":4.23,"high":4.26,"low":4.17,"close":4.18,"volume":10916000},{"date":"2026-09-15","open":4.26,"high":4.27,"low":4.09,"close":4.1,"volume":13810900},{"date":"2026-09-16","open":4.08,"high":4.15,"low":4.03,"close":4.11,"volume":10880900},{"date":"2026-09-17","open":4.1,"high":4.14,"low":4.03,"close":4.13,"volume":10359900},{"date":"2026-09-18","open":4.14,"high":4.15,"low":4.07,"close":4.11,"volume":8956100},{"date":"2026-09-21","open":4.08,"high":4.52,"low":4.08,"close":4.52,"volume":49175977},{"date":"2026-09-22","open":4.67,"high":4.97,"low":4.67,"close":4.97,"volume":62528337},{"date":"2026-09-23","open":4.91,"high":5.14,"low":4.72,"close":4.78,"volume":67340334},{"date":"2026-09-24","open":4.78,"high":4.78,"low":4.58,"close":4.74,"volume":34934634},{"date":"2026-09-28","open":4.79,"high":4.84,"low":4.61,"close":4.81,"volume":41533494},{"date":"2026-09-29","open":4.75,"high":4.93,"low":4.66,"close":4.89,"volume":37523500},{"date":"2026-09-30","open":4.99,"high":5.19,"low":4.88,"close":5.01,"volume":36713400}];

const canvas = document.querySelector('#chartCanvas');
const wrap = document.querySelector('#chartWrap');
const crosshair = document.querySelector('#crosshair');
const selectedObject = document.querySelector('#selectedObject');
const replayBar = document.querySelector('#replayBar');
let replayIndex = bars.length - 1;
let drawingsVisible = true;
let selected = false;
let viewOffset = 0;
let viewCount = bars.length;
let pointerDown = null;

const ema = bars.map((row, index) => {
  const begin = Math.max(0, index - 19);
  const source = bars.slice(begin, index + 1);
  return source.reduce((sum, item) => sum + item.close, 0) / source.length;
});

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function xFor(index, left, candleWidth) { return left + (index + .5) * candleWidth; }

function visibleBars() {
  const end = Math.min(replayIndex + 1, bars.length - viewOffset);
  const begin = Math.max(0, end - viewCount);
  return { begin, end, data: bars.slice(begin, end) };
}

function renderChart() {
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

  const visible = visibleBars();
  const rows = visible.data;
  if (!rows.length) return;
  let low = Math.min(...rows.map(row => row.low), 3.14) - .12;
  let high = Math.max(...rows.map(row => row.high), 5.98) + .12;
  const y = value => top + (high - value) / (high - low) * plotHeight;
  const candleWidth = plotWidth / rows.length;

  ctx.strokeStyle = css('--grid');
  ctx.lineWidth = 1;
  ctx.font = '12px Segoe UI';
  ctx.fillStyle = '#8c8f96';
  ctx.textAlign = 'left';
  const priceStep = .4;
  for (let value = Math.ceil(low / priceStep) * priceStep; value <= high; value += priceStep) {
    const py = Math.round(y(value)) + .5;
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(width - right, py); ctx.stroke();
    ctx.fillText(value.toFixed(2), width - right + 8, py + 4);
  }
  const monthMarks = [];
  rows.forEach((row, index) => {
    const day = row.date.slice(8);
    if (day <= '05' && !monthMarks.some(mark => mark.label === row.date.slice(5, 7))) {
      monthMarks.push({ index, label: `${Number(row.date.slice(5, 7))}月` });
    }
  });
  monthMarks.forEach(mark => {
    const px = Math.round(xFor(mark.index, left, candleWidth)) + .5;
    ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, height - bottom); ctx.stroke();
    ctx.fillText(mark.label, px + 5, height - 52);
  });

  // EMA 20
  ctx.strokeStyle = css('--orange');
  ctx.lineWidth = 1.45;
  ctx.beginPath();
  rows.forEach((row, index) => {
    const px = xFor(index, left, candleWidth);
    const py = y(ema[visible.begin + index]);
    index ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
  });
  ctx.stroke();

  // TradingView-style white-up / blue-down candles from the user's chart setup.
  rows.forEach((row, index) => {
    const px = xFor(index, left, candleWidth);
    const up = row.close >= row.open;
    const color = up ? '#f5f5f5' : '#2962ff';
    const bodyWidth = Math.max(3, Math.min(13, candleWidth * .62));
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(px, y(row.high)); ctx.lineTo(px, y(row.low)); ctx.stroke();
    const bodyTop = y(Math.max(row.open, row.close));
    const bodyBottom = y(Math.min(row.open, row.close));
    ctx.fillRect(px - bodyWidth / 2, bodyTop, bodyWidth, Math.max(2, bodyBottom - bodyTop));
  });

  if (drawingsVisible) drawOverlays(ctx, { rows, begin: visible.begin, left, width, right, top, bottom, height, y, candleWidth });
  drawLastPrice(ctx, rows.at(-1).close, y, width, right);
  canvas._view = { rows, begin: visible.begin, left, right, top, bottom, width, height, y, low, high, candleWidth };
  document.querySelector('.ema-value').textContent = ema[Math.min(replayIndex, bars.length - 1)].toFixed(2);
}

function drawOverlays(ctx, v) {
  const { rows, begin, left, width, right, top, height, bottom, y, candleWidth } = v;
  const local = globalIndex => globalIndex - begin;
  const px = globalIndex => xFor(local(globalIndex), left, candleWidth);
  const plotRight = width - right;

  // User trend line.
  ctx.strokeStyle = '#f23645'; ctx.lineWidth = 1.3;
  ctx.beginPath(); ctx.moveTo(px(20), y(3.43)); ctx.lineTo(px(70), y(5.34)); ctx.stroke();

  // Original GAP H2 language restored: blue dashed outline, no fill, solid floor.
  const gapLeft = Math.max(left, px(57) - candleWidth * .55);
  const gapRight = Math.min(plotRight, px(64) + candleWidth * .55);
  ctx.save();
  ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 1.2; ctx.globalAlpha = .72;
  ctx.setLineDash([6, 4]); ctx.strokeRect(gapLeft, y(4.56), gapRight - gapLeft, y(4.38) - y(4.56));
  ctx.setLineDash([]); ctx.globalAlpha = .6;
  ctx.beginPath(); ctx.moveTo(gapLeft, y(4.38)); ctx.lineTo(plotRight, y(4.38)); ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#fff'; ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 1;
  ctx.fillRect((gapLeft + gapRight) / 2 - 19, y(4.47) - 10, 38, 20);
  ctx.strokeRect((gapLeft + gapRight) / 2 - 19, y(4.47) - 10, 38, 20);
  ctx.fillStyle = '#2962ff'; ctx.font = '12px Segoe UI'; ctx.textAlign = 'center'; ctx.fillText('Gap', (gapLeft + gapRight) / 2, y(4.47) + 4);
  ctx.restore();

  // Neutral strategy callouts and leader lines.
  callout(ctx, 'BO', px(57), y(4.56), px(54), y(4.83));
  callout(ctx, 'H1', px(58), y(4.45), px(55), y(4.72));
  callout(ctx, 'H2', px(70), y(5.19), px(67), y(5.48), true);
  callout(ctx, 'Gap Floor', gapLeft, y(4.38), gapLeft - 32, y(4.18));

  // Fibonacci retracement kept as a trader drawing layer.
  const fibStart = px(21), fibEnd = Math.min(plotRight, px(60));
  [[3.14, '1 (3.14)', '#4caf50'], [4.06, '0.5 (4.06)', '#4caf50'], [4.08, '', '#9c27b0']].forEach(([value, label, color]) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(fibStart, y(value)); ctx.lineTo(fibEnd, y(value)); ctx.stroke();
    if (label) { ctx.fillStyle = color; ctx.font = '12px Segoe UI'; ctx.textAlign = 'right'; ctx.fillText(label, fibStart - 7, y(value) + 4); }
  });

  // Parallel channel.
  ctx.strokeStyle = '#21b9d6'; ctx.lineWidth = 1.3;
  [0, .34].forEach(delta => { ctx.beginPath(); ctx.moveTo(px(64), y(4.45 + delta)); ctx.lineTo(plotRight, y(5.45 + delta)); ctx.stroke(); });
  ctx.save(); ctx.setLineDash([6, 6]); ctx.beginPath(); ctx.moveTo(px(64), y(4.62)); ctx.lineTo(plotRight, y(5.62)); ctx.stroke(); ctx.restore();

  // Long position tool.
  const riskX = Math.max(left, px(66));
  const riskW = plotRight - riskX;
  ctx.fillStyle = 'rgba(8,153,129,.22)'; ctx.fillRect(riskX, y(5.98), riskW, y(4.76) - y(5.98));
  ctx.fillStyle = 'rgba(242,54,69,.25)'; ctx.fillRect(riskX, y(4.76), riskW, y(4.08) - y(4.76));
  ctx.strokeStyle = '#8d939c'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(riskX, y(4.76)); ctx.lineTo(plotRight, y(4.76)); ctx.stroke();
  priceTag(ctx, '5.98', y(5.98), '#26a69a', width, right);
  priceTag(ctx, '5.53', y(5.53), '#f23645', width, right);
  priceTag(ctx, '4.76', y(4.76), '#7b8088', width, right);
  priceTag(ctx, '4.08', y(4.08), '#f23645', width, right);

  // Selected handles communicate expected TV editing behavior.
  if (selected) {
    ctx.fillStyle = '#0b0b0b'; ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 2;
    [[riskX, y(4.76)], [plotRight, y(4.76)], [riskX, y(4.08)], [plotRight, y(4.08)]].forEach(([x, yy]) => {
      ctx.beginPath(); ctx.arc(x, yy, 6, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    });
  }
}

function callout(ctx, label, x, y, tx, ty, dashed = false) {
  ctx.save();
  ctx.strokeStyle = '#cbd5e1'; ctx.fillStyle = '#cbd5e1'; ctx.lineWidth = .9;
  if (dashed) ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(tx, ty); ctx.stroke();
  ctx.setLineDash([]); ctx.font = '12px Segoe UI'; ctx.textAlign = 'center';
  const width = ctx.measureText(label).width + 10;
  ctx.fillStyle = 'rgba(31,38,48,.94)'; ctx.fillRect(tx - width / 2, ty - 17, width, 20);
  ctx.fillStyle = '#cbd5e1'; ctx.fillText(label, tx, ty - 3);
  ctx.restore();
}

function priceTag(ctx, value, py, color, width, right) {
  ctx.fillStyle = color; ctx.fillRect(width - right, py - 10, right, 20);
  ctx.fillStyle = '#fff'; ctx.font = 'bold 12px Segoe UI'; ctx.textAlign = 'center'; ctx.fillText(value, width - right / 2, py + 4);
}

function drawLastPrice(ctx, value, y, width, right) {
  const py = y(value);
  ctx.save(); ctx.strokeStyle = '#e6e6e6'; ctx.globalAlpha = .85; ctx.setLineDash([2, 4]);
  ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(width - right, py); ctx.stroke(); ctx.restore();
  priceTag(ctx, value.toFixed(2), py, '#f3f3f3', width, right);
  ctx.fillStyle = '#111'; ctx.font = 'bold 12px Segoe UI'; ctx.textAlign = 'center'; ctx.fillText(value.toFixed(2), width - right / 2, py + 4);
}

function updateReadout(row) {
  document.querySelector('#openValue').textContent = row.open.toFixed(2);
  document.querySelector('#highValue').textContent = row.high.toFixed(2);
  document.querySelector('#lowValue').textContent = row.low.toFixed(2);
  document.querySelector('#closeValue').textContent = row.close.toFixed(2);
}

function showToast(message) {
  const toast = document.querySelector('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 1900);
}

function openModal(id) {
  document.querySelectorAll('.modal').forEach(modal => modal.classList.remove('show'));
  document.querySelector(`#${id}`).classList.add('show');
  document.querySelector('#modalBackdrop').classList.add('show');
}
function closeModal() { document.querySelector('#modalBackdrop').classList.remove('show'); }

canvas.addEventListener('mousemove', event => {
  const view = canvas._view;
  if (!view) return;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const yPx = event.clientY - rect.top;
  if (x < view.left || x > view.width - view.right || yPx < view.top || yPx > view.height - view.bottom) {
    crosshair.classList.remove('show'); return;
  }
  const index = Math.max(0, Math.min(view.rows.length - 1, Math.floor((x - view.left) / view.candleWidth)));
  const row = view.rows[index];
  const price = view.high - (yPx - view.top) / (view.height - view.bottom - view.top) * (view.high - view.low);
  crosshair.style.setProperty('--cx', `${x}px`);
  crosshair.style.setProperty('--cy', `${yPx}px`);
  document.querySelector('#crossPrice').textContent = price.toFixed(2);
  document.querySelector('#crossDate').textContent = row.date;
  crosshair.classList.add('show');
  updateReadout(row);
  if (pointerDown) {
    const dx = event.clientX - pointerDown.x;
    viewOffset = Math.max(0, Math.min(bars.length - 30, pointerDown.offset + Math.round(-dx / Math.max(4, view.candleWidth))));
    renderChart();
  }
});
canvas.addEventListener('mouseleave', () => { crosshair.classList.remove('show'); updateReadout(bars[Math.min(replayIndex, bars.length - 1)]); pointerDown = null; });
canvas.addEventListener('mousedown', event => { pointerDown = { x: event.clientX, offset: viewOffset }; });
window.addEventListener('mouseup', () => { pointerDown = null; });
canvas.addEventListener('wheel', event => {
  event.preventDefault();
  viewCount = Math.max(30, Math.min(bars.length, viewCount + (event.deltaY > 0 ? 6 : -6)));
  renderChart();
}, { passive: false });
canvas.addEventListener('dblclick', event => {
  selected = true;
  selectedObject.classList.add('visible');
  selectedObject.style.left = `${Math.min(event.offsetX + 14, wrap.clientWidth - 220)}px`;
  selectedObject.style.top = `${Math.max(58, event.offsetY - 54)}px`;
  renderChart();
  openModal('objectModal');
});

document.querySelectorAll('.tool[data-tool], [data-favorite]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.tool').forEach(item => item.classList.remove('active'));
  if (button.classList.contains('tool')) button.classList.add('active');
  document.querySelectorAll('[data-favorite]').forEach(item => item.classList.remove('active'));
  if (button.dataset.favorite) button.classList.add('active');
  showToast(`${button.title || '工具'}已选择`);
}));

document.querySelectorAll('.timeframes .disabled').forEach(button => button.addEventListener('click', () => showToast(button.dataset.unavailable)));
document.querySelectorAll('.timeframes button[data-timeframe]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.timeframes button').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  showToast(`${button.textContent}线原型状态`);
}));
document.querySelector('#openAlert').addEventListener('click', () => openModal('alertModal'));
document.querySelector('#openObject').addEventListener('click', () => openModal('objectModal'));
selectedObject.addEventListener('click', () => openModal('objectModal'));
document.querySelector('#removeObjects').addEventListener('click', () => openModal('removeModal'));
document.querySelectorAll('.close-modal').forEach(button => button.addEventListener('click', closeModal));
document.querySelector('#modalBackdrop').addEventListener('click', event => { if (event.target.id === 'modalBackdrop') closeModal(); });
document.querySelector('#createAlert').addEventListener('click', () => { closeModal(); showToast('提醒已保存：收盘价向上穿越 5.20'); });

document.querySelector('#toggleDrawings').addEventListener('click', () => {
  drawingsVisible = !drawingsVisible;
  renderChart();
  showToast(drawingsVisible ? '已显示所有图形' : '已隐藏所有图形');
});
document.querySelector('#togglePanel').addEventListener('click', () => {
  document.querySelector('#rightPanel').classList.toggle('collapsed');
  setTimeout(renderChart, 190);
});

document.querySelector('#startReplay').addEventListener('click', () => {
  replayBar.classList.add('visible');
  replayIndex = Number(document.querySelector('#replaySlider').value);
  viewOffset = 0;
  renderChart();
});
document.querySelector('#exitReplay').addEventListener('click', () => {
  replayBar.classList.remove('visible'); replayIndex = bars.length - 1; renderChart();
});
document.querySelector('#replaySlider').addEventListener('input', event => {
  replayIndex = Number(event.target.value);
  document.querySelector('#replayDate').textContent = bars[replayIndex].date;
  updateReadout(bars[replayIndex]);
  renderChart();
});

document.querySelector('#snapshot').addEventListener('click', () => {
  document.querySelector('#snapshotImage').src = canvas.toDataURL('image/png');
  openModal('snapshotModal');
});
document.querySelector('#downloadSnapshot').addEventListener('click', () => {
  const link = document.createElement('a');
  link.download = 'Aplus-002259-2026-09-30.png';
  link.href = canvas.toDataURL('image/png');
  link.click();
  closeModal();
} );

document.querySelectorAll('.candidate').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.candidate').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  if (!button.textContent.includes('升达林业')) showToast('静态原型仅嵌入 002259 真实日线');
}));

const resize = new ResizeObserver(renderChart);
resize.observe(wrap);
renderChart();

const state = new URLSearchParams(location.search).get('state');
if (state === 'alert') openModal('alertModal');
if (state === 'object') { selected = true; selectedObject.classList.add('visible'); openModal('objectModal'); renderChart(); }
if (state === 'replay') document.querySelector('#startReplay').click();
if (state === 'snapshot') document.querySelector('#snapshot').click();
