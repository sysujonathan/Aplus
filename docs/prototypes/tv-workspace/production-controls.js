/* Shared controls for the default embedded chart and optional detached chart. */
let highlightedTradeDate=null;
let lastActiveNotification=0;
function notifyNativeActiveChart() {
  if(!chartBoot.embedded||closingWorkspace||Date.now()-lastActiveNotification<300)return;
  lastActiveNotification=Date.now();window.pywebview?.api?.notify_active().catch(()=>{});
}
document.addEventListener('pointerdown',notifyNativeActiveChart,{passive:true});
// WebView may focus a newly loaded child without any trader action.
// Activate a grid slot only after actual pointer or keyboard input in it.
document.addEventListener('keydown',notifyNativeActiveChart);
const objectNameInput=document.querySelector('#objectNameInput');
document.querySelector('#renameObject').addEventListener('click',()=>{
  const object=objectAtSelection();if(!object)return;
  objectNameInput.value=object.name||toolNames[object.type]||'';
  objectNameInput.hidden=false;document.querySelector('#objectModalTitle').hidden=true;
  objectNameInput.focus();objectNameInput.select();
});
objectNameInput.addEventListener('input',()=>{
  const object=objectAtSelection();if(!object)return;
  rememberObjects();object.name=objectNameInput.value.slice(0,60);savePrototypeState();
});
objectNameInput.addEventListener('blur',()=>{
  const object=objectAtSelection();
  document.querySelector('#objectModalTitle').textContent=object?.name||toolNames[object?.type]||'图形设置';
  objectNameInput.hidden=true;document.querySelector('#objectModalTitle').hidden=false;
});
objectNameInput.addEventListener('keydown',event=>{
  if(event.key==='Enter'){event.preventDefault();objectNameInput.blur();}
});
document.addEventListener('click',event=>{
  document.querySelectorAll('.position-stats[open]').forEach(menu=>{
    if(!menu.contains(event.target))menu.open=false;
  });
});
window.aplusNavigateChart = request => {
  if(closingWorkspace)return false;
  if(!request.date){resetChartView();return true;}
  const index=chartCoordinates.indexForDate(bars,request.date,activeTimeframeUnit);
  if(index<0 || (replayActive && index>replayIndex)){
    showToast('当前周期或回放范围内没有该笔成交对应的K线');return true;
  }
  const maxEnd=Math.min(bars.length,replayIndex+1);
  viewCount=Math.min(viewCount,80,maxEnd);
  const end=clamp(index+Math.floor(viewCount/2),Math.min(viewCount,maxEnd),maxEnd);
  viewOffset=maxEnd-end;priceZoom=1;priceCenterOffset=0;
  highlightedTradeDate=request.date;
  renderWorkspace();savePrototypeState();showToast(`已定位至 ${request.date}`);return true;
};
function drawVectorEvidence(ctx, view) {
  const data = chartBoot.strategyEvidence || {};
  const replayDate = replayCutoffDate || bars[Math.min(replayIndex, bars.length - 1)].date;
  const position = point => {
    if (!point) return null;
    const index = chartCoordinates.indexForDate(bars, point.date, activeTimeframeUnit);
    return index < 0 ? null : {x:view.xGlobal(index + Number(point.offset || 0)), y:view.y(Number(point.value))};
  };
  const marker = (p, kind, size, color) => {
    ctx.fillStyle = color; ctx.beginPath();
    if (kind === '*') for (let n=0;n<10;n++) {
      const radius = n % 2 ? size*.42 : size;
      const angle = n*Math.PI/5-Math.PI/2;
      n ? ctx.lineTo(p.x+Math.cos(angle)*radius,p.y+Math.sin(angle)*radius)
        : ctx.moveTo(p.x+Math.cos(angle)*radius,p.y+Math.sin(angle)*radius);
    } else {
      const direction = kind === 'v' ? -1 : 1;
      ctx.moveTo(p.x,p.y-size*direction);ctx.lineTo(p.x-size,p.y+size*direction);ctx.lineTo(p.x+size,p.y+size*direction);
    }
    ctx.closePath();ctx.fill();
  };
  if (!replayActive || !data.availableDate || data.availableDate <= replayDate) (data.items || []).forEach(item => {
    ctx.save();ctx.strokeStyle=item.color||'#cbd5e1';ctx.fillStyle=item.color||'#cbd5e1';
    ctx.lineWidth=Number(item.width||1);ctx.globalAlpha=Number(item.alpha ?? 1);
    ctx.setLineDash(item.dash==='--'?[6,4]:item.dash===':'?[2,4]:item.dash==='-.'?[6,3,2,3]:[]);
    const a=position(item.a), b=position(item.b);
    if (item.kind==='hline') {
      const y=view.y(item.value);ctx.beginPath();ctx.moveTo(view.left,y);ctx.lineTo(view.width-view.right,y);ctx.stroke();
      if(item.text){ctx.font='11px Segoe UI';ctx.textAlign='left';ctx.fillText(`${item.text} ${chartPrice(item.value)}`,view.left+8,y-5);}
    } else if (item.kind==='line') {
      ctx.beginPath();let started=false;
      for(const point of item.points||[]){const p=position(point);if(!p){started=false;continue;} started?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);started=true;}ctx.stroke();
    } else if (item.kind==='rectangle' && a && b) {
      if(item.fill){ctx.fillStyle=item.fill;ctx.fillRect(a.x,a.y,b.x-a.x,b.y-a.y);}
      ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);
    } else if (item.kind==='label' && a && b) {
      if(item.leader){ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}
      ctx.font=`${Math.max(10,Number(item.size||10))}px "Microsoft YaHei",sans-serif`;
      ctx.textAlign=item.align||'center';ctx.textBaseline=item.vertical==='top'?'top':item.vertical==='bottom'?'bottom':'middle';
      const lines=String(item.text).split('\n');const height=14*lines.length;
      const width=Math.max(...lines.map(line=>ctx.measureText(line).width))+8;
      const left=ctx.textAlign==='left'?b.x-4:ctx.textAlign==='right'?b.x-width+4:b.x-width/2;
      ctx.fillStyle='#171717';ctx.fillRect(left,b.y-(ctx.textBaseline==='top'?0:ctx.textBaseline==='bottom'?height:height/2),width,height);
      ctx.fillStyle=item.color||'#cbd5e1';lines.forEach((line,n)=>ctx.fillText(line,b.x,b.y+n*14));
      registerRect('strategy-evidence',left,b.y-height,width+left,b.y+height);
    } else if (item.kind==='marker' && a) marker(a,item.marker,Math.max(4,Number(item.size||8)),item.color);
    ctx.restore();
  });
  (chartBoot.tradeEvents || []).forEach(event => {
    if (replayActive && event.date > replayDate) return;
    const p=position({date:String(event.date).slice(0,10),value:event.price});
    if(!p || !Number.isFinite(p.y))return;
    const buy=event.kind==='buy';ctx.save();marker(p,buy?'^':'v',7,buy?'#089981':'#f23645');
    if(event.date===highlightedTradeDate){ctx.strokeStyle='#d1d4dc';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(p.x,p.y,11,0,Math.PI*2);ctx.stroke();}
    ctx.fillStyle='#cbd5e1';ctx.font='11px Segoe UI';ctx.textAlign='left';
    ctx.fillText(`${event.label || (buy?'买入':'卖出')}${event.estimated?'（参考）':''}`,p.x+10,p.y);ctx.restore();
  });
}

window.aplusSwitchContext = async () => {
  if (closingWorkspace) return;
  document.activeElement?.blur?.();
  closingWorkspace = true;
  const cover = document.createElement('div');
  cover.className = 'context-switch-cover';
  cover.textContent = '正在保存当前分析并切换标的…';
  document.querySelector('.app-shell').append(cover);
  try {
    if (replayActive) stopReplayTimer();
    await savePrototypeState(true);
    if (bridgeSaveFailed) throw new Error('当前分析尚未确认保存，已保留当前图表');
    const result = await window.pywebview.api.activate_context();
    location.replace(location.pathname + '?chart=' + result.token);
  } catch (error) {
    closingWorkspace = false;
    cover.remove();
    await window.pywebview.api.cancel_context_switch(String(error.message || error));
    showToast(`${error.message || error}，未切换或丢弃画线`);
    const retry = document.createElement('button');
    retry.textContent = '重试保存并切换'; retry.className = 'retry-chart-switch';
    retry.onclick = async () => { retry.remove(); await window.pywebview.api.retry_context_switch(); };
    document.querySelector('.app-shell').append(retry);
    if (window.aplusCloseRequested) await window.aplusFlushAndClose();
  }
};

window.openEmaSettings = () => {
  const fields = {emaLength:'ema_length', emaSource:'ema_source', emaOffset:'ema_offset',
    emaColor:'ema_color', emaWidth:'ema_width', emaSmoothing:'ema_smoothing',
    emaSmoothLength:'ema_smooth_length', emaBbDeviation:'ema_bb_deviation',emaPrecision:'ema_precision',
    emaTimeframe:'ema_timeframe',emaSmoothColor:'ema_smooth_color',emaBbUpperColor:'ema_bb_upper_color',
    emaBbLowerColor:'ema_bb_lower_color',emaBbFillColor:'ema_bb_fill_color'};
  for (const [id, key] of Object.entries(fields)) document.getElementById(id).value = indicatorSettings[key] ?? (key === 'ema_width' ? 1.5 : '');
  document.querySelector('#emaVisible').checked = Boolean(indicatorSettings.ema);
  for(const [id,key] of Object.entries({emaWaitClose:'ema_wait_close',emaSmoothVisible:'ema_smooth_visible',emaBbVisible:'ema_bb_visible'}))document.getElementById(id).checked=indicatorSettings[key]!==false;
  document.querySelector('#emaBbFillAlpha').value=Math.round((indicatorSettings.ema_bb_fill_alpha??.1)*100);
  document.querySelectorAll('#emaTimeframe option').forEach(option=>{option.disabled=option.value!=='chart'&&chartBoot.production&&!chartBoot.series?.[option.value];});
  for(const [id,key] of Object.entries({emaPriceLabels:'ema_price_labels',emaValues:'ema_values',emaInputs:'ema_inputs'}))document.getElementById(id).checked=indicatorSettings[key]!==false;
  document.querySelector('#emaModal').dataset.alpha = String(indicatorSettings.ema_alpha ?? 1);
  for(const [id,key] of Object.entries({emaSmoothColor:'ema_smooth_alpha',emaBbUpperColor:'ema_bb_upper_alpha',emaBbLowerColor:'ema_bb_lower_alpha',emaBbFillColor:'ema_bb_fill_alpha'}))document.querySelector('#emaModal').dataset[id+'Alpha']=String(indicatorSettings[key]??1);
  for (const unit of ['Daily', 'Weekly', 'Monthly']) document.querySelector('#ema' + unit).checked = indicatorSettings['ema_' + unit.toLowerCase()] !== false;
  syncEmaSmoothingInputs();
  openWorkspaceModal('emaModal');
};
document.querySelector('[title="技术指标"]').addEventListener('click', window.openEmaSettings);
document.querySelector('#openChartLayout').addEventListener('click', () => {
  const available = chartBoot.embedded && chartBoot.layoutSupported;
  document.querySelectorAll('[data-chart-layout]').forEach(button => {button.disabled = !available;});
  document.querySelector('#layoutAvailability').textContent = available ? '切换前将保存当前图表分析。' : '多图布局在主工作区提供；当前为独立图表。';
  openWorkspaceModal('chartLayoutModal');
});
document.querySelectorAll('[data-chart-layout]').forEach(button => button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    document.activeElement?.blur?.();
    await savePrototypeState(true);if(bridgeSaveFailed)throw new Error('当前分析尚未保存，布局已保留');
    await window.pywebview.api.request_layout(Number(button.dataset.chartLayout));closeWorkspaceModal();
  } catch(error){showToast(error.message||String(error));}
  finally {button.disabled = false;}
}));
function syncEmaSmoothingInputs() {
  const method = document.querySelector('#emaSmoothing').value;
  document.querySelector('#emaSmoothLength').disabled = method === 'none';
  document.querySelector('#emaBbDeviation').disabled = method !== 'sma_bb';
}
document.querySelector('#emaSmoothing').addEventListener('change', syncEmaSmoothingInputs);
document.querySelector('.indicator-legend > div:first-child').addEventListener('dblclick', window.openEmaSettings);
document.querySelector('.indicator-legend > div:first-child button').addEventListener('click', window.openEmaSettings);
document.querySelectorAll('#emaTabs button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('#emaTabs button').forEach(item => item.classList.toggle('active', item === button));
  document.querySelectorAll('.ema-tab-panel').forEach(panel => panel.classList.toggle('active', panel.dataset.emaPanel === button.dataset.emaTab));
}));
document.querySelectorAll('#emaModal .close-modal').forEach(button => button.addEventListener('click', closeWorkspaceModal));
document.querySelector('#saveEmaSettings').addEventListener('click', () => {
  const length = Number(document.querySelector('#emaLength').value);
  const offset = Number(document.querySelector('#emaOffset').value);
  const width = Number(document.querySelector('#emaWidth').value);
  const smoothLength = Number(document.querySelector('#emaSmoothLength').value);
  const deviation = Number(document.querySelector('#emaBbDeviation').value);
  if (!Number.isInteger(length) || length < 1 || length > 500 || !Number.isInteger(offset) || Math.abs(offset) > 500 || !Number.isFinite(width) || width < 1 || width > 8 || !Number.isInteger(smoothLength) || smoothLength < 1 || smoothLength > 500 || !Number.isFinite(deviation) || deviation <= 0 || deviation > 50) {
    showToast('请核对 EMA 长度、偏移和线宽'); return;
  }
  rememberObjects();
  Object.assign(indicatorSettings, {ema_length:length, ema_offset:offset, ema_width:width,
    ema_timeframe:document.querySelector('#emaTimeframe').value,ema_wait_close:document.querySelector('#emaWaitClose').checked,
    ema_smooth_visible:document.querySelector('#emaSmoothVisible').checked,ema_bb_visible:document.querySelector('#emaBbVisible').checked,
    ema_smooth_color:document.querySelector('#emaSmoothColor').value,ema_bb_upper_color:document.querySelector('#emaBbUpperColor').value,
    ema_bb_lower_color:document.querySelector('#emaBbLowerColor').value,ema_bb_fill_color:document.querySelector('#emaBbFillColor').value,
    ema_bb_fill_alpha:Number(document.querySelector('#emaBbFillAlpha').value)/100,
    ema_source:document.querySelector('#emaSource').value, ema_color:document.querySelector('#emaColor').value,
    ema:document.querySelector('#emaVisible').checked,
    ema_alpha:Number(document.querySelector('#emaModal').dataset.alpha ?? 1),
    ema_smooth_alpha:Number(document.querySelector('#emaModal').dataset.emaSmoothColorAlpha??1),
    ema_bb_upper_alpha:Number(document.querySelector('#emaModal').dataset.emaBbUpperColorAlpha??1),
    ema_bb_lower_alpha:Number(document.querySelector('#emaModal').dataset.emaBbLowerColorAlpha??1),
    ema_smoothing:document.querySelector('#emaSmoothing').value,
    ema_smooth_length:smoothLength, ema_bb_deviation:deviation,ema_precision:document.querySelector('#emaPrecision').value,
    ema_price_labels:document.querySelector('#emaPriceLabels').checked,
    ema_values:document.querySelector('#emaValues').checked,ema_inputs:document.querySelector('#emaInputs').checked});
  for (const unit of ['Daily', 'Weekly', 'Monthly']) indicatorSettings['ema_' + unit.toLowerCase()] = document.querySelector('#ema' + unit).checked;
  calculateEma(); closeWorkspaceModal(); savePrototypeState(); updateObjectTree(); renderWorkspace();
});
['#ffffff','#cbd5e1','#787b86','#ff9800','#f23645','#089981','#2962ff','#ab47bc','#23b9d6','#fdd835'].forEach(color => {
  const button = document.createElement('button'); button.type = 'button'; button.title = color;
  button.style.background = color; button.style.width = '24px'; button.style.height = '24px';
  button.onclick = () => { document.querySelector('#emaColor').value = color; };
  document.querySelector('#emaPalette').append(button);
});

const emaLabel = document.querySelector('.indicator-legend > div:first-child span');
function syncIndicatorLegend() {
  emaLabel.textContent = indicatorSettings.ema_inputs===false?'EMA':`EMA ${indicatorSettings.ema_length} ${indicatorSettings.ema_source}`;
  document.querySelector('.ema-value').hidden=indicatorSettings.ema_values===false;
  document.querySelector('.ema-value').style.color = indicatorSettings.ema_color;
  emaLabel.closest('div').classList.toggle('indicator-hidden', !indicatorSettings.ema || indicatorSettings.indicators_visible === false);
  const magnet = document.querySelector('[data-tool="magnet"]');
  magnet.classList.toggle('on', magnetEnabled);
  magnet.setAttribute('aria-pressed', String(magnetEnabled));
}
syncIndicatorLegend();
renderWorkspace();
document.querySelector('#alertTarget').addEventListener('change', event => {
  document.querySelector('#alertPrice').disabled = event.target.value === 'ema';
  document.querySelector('#alertEmaLength').disabled = event.target.value !== 'ema';
});
document.querySelector('#alertTarget').dispatchEvent(new Event('change'));

window.openAlertEditor = id => {
  const alert = id ? chartAlerts.find(item => item.id === id) : null;
  editingAlertId = alert?.id || null;
  document.querySelector('#alertModal header b').textContent = alert ? '编辑提醒' : '创建提醒';
  document.querySelector('#createAlert').textContent = alert ? '保存' : '创建';
  const fields = {alertName:'name', alertPrice:'value', alertCondition:'condition',
    alertTarget:'target', alertEmaLength:'ema_length', alertFrequency:'frequency', alertTimeframe:'timeframe'};
  if (alert) for (const [id, key] of Object.entries(fields)) document.getElementById(id).value = alert[key];
  else document.querySelector('#alertTimeframe').value = activeTimeframeUnit;
  const expires = alert?.expires ? new Date(alert.expires) : null;
  document.querySelector('#alertExpires').value = expires ?
    new Date(expires.getTime() + 8 * 3600000).toISOString().slice(0, 16) : '';
  document.querySelector('#alertTarget').dispatchEvent(new Event('change'));
  renderAlertStatus(alert);
  openWorkspaceModal('alertModal');
};
function renderAlertStatus(alert) {
  let host=document.querySelector('#alertStatus');
  if(!host){host=document.createElement('section');host.id='alertStatus';host.className='alert-status';
    document.querySelector('#alertModal .modal-body').append(host);}
  host.replaceChildren();if(!alert)return;
  const line=text=>{const p=document.createElement('p');p.textContent=text;host.append(p);};
  line(`${alert.enabled===false?'已暂停':'已启用'} · ${alert.status||'等待创建后的新收盘K线'}`);
  if(alert.cursor)line(`已检查至：${alert.cursor}`);
  const events=alert.events||[];line(`触发记录（${events.length}）`);
  if(!events.length)line('尚无触发记录');
  [...events].reverse().forEach(event=>line(`${event.bar} · ${chartPrice(event.price)} · ${event.text||alert.name}`));
}
document.querySelector('#openAlert').addEventListener('click', () => window.openAlertEditor(null));
window.writeAlertChange = async (method, id, changes) => {
  if (!chartBoot.production) throw new Error('提醒管理需要正式本机图表');
  const pending = bridgeSaveChain.then(() => {
    if (bridgeSaveFailed) throw new Error('请先保存当前图表修改');
    return window.pywebview.api[method](id, changes);
  });
  bridgeSaveChain = pending.then(result => {
    bridgeRevision = Number(result.revision); chartAlerts = structuredClone(result.alerts);
    updateObjectTree(); renderWorkspace();
  }).catch(() => {});
  return pending;
};
let alertPolling=false;
setInterval(async()=>{
  if(!chartBoot.production||closingWorkspace||alertPolling||!window.pywebview?.api?.read_alerts)return;
  alertPolling=true;
  try {
    await bridgeSaveChain;
    if(closingWorkspace)return;
    const result=await window.pywebview.api.read_alerts();
    if(JSON.stringify(result.alerts)!==JSON.stringify(chartAlerts)){
      chartAlerts=structuredClone(result.alerts);updateObjectTree();renderWorkspace();
      if(editingAlertId&&document.querySelector('#alertModal').classList.contains('show'))
        renderAlertStatus(chartAlerts.find(item=>item.id===editingAlertId));
    }
  } catch(_error){ /* A failed status read must not stop the monitor or replace edits. */ }
  finally {alertPolling=false;}
},5000);
setInterval(() => {
  document.querySelector('.clock').textContent = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false}).format(new Date()) + ' UTC+8';
}, 1000);

// Native candidate/watch lists are the navigation source, never the prototype's fake list.
if (chartBoot.embedded) {
  document.querySelector('#rightPanel').classList.toggle('collapsed', !indicatorSettings.sidebar_open);
  document.querySelector('.panel-tabs').style.display = 'none';
  document.querySelector('.candidate-list').style.display = 'none';
  document.querySelector('.panel-context').style.display = 'none';
}
syncWorkspaceControls();
document.querySelector('#toggleFavorites').addEventListener('click', () => {
  indicatorSettings.favorites_visible = !indicatorSettings.favorites_visible;
  syncWorkspaceControls();savePrototypeState();
});
if (chartBoot.production && chartBoot.strategyEvidence?.facts?.length) {
  const facts = document.createElement('div');facts.className = 'strategy-facts';
  for (const text of chartBoot.strategyEvidence.facts) {
    const line = document.createElement('p');line.textContent = text;facts.append(line);
  }
  document.querySelector('.decision-card').after(facts);
}
document.querySelectorAll('.panel-tabs [data-panel]').forEach(button => button.addEventListener('click', () => {
  if (!chartBoot.production) return;
  document.querySelector('.panel-context span').textContent = `${chartBoot.contextTitle} · ${chartBoot.name}`;
}));

function chartControlMenu(title, choices) {
  document.querySelector('#chartControlTitle').textContent=title;
  const body=document.querySelector('#chartControlBody');body.replaceChildren();
  for(const choice of choices){
    const row=document.createElement(choice.action?'button':'p');row.textContent=choice.label;
    if(choice.action){row.className='remove-option';row.disabled=Boolean(choice.disabled);
      row.addEventListener('click',()=>{closeWorkspaceModal();choice.action();});}
    body.append(row);
  }
  openWorkspaceModal('chartControlModal');
}
function setChartType(type) {
  rememberObjects();indicatorSettings.chart_type=type;savePrototypeState();renderWorkspace();
  syncChartTypeButtons();
}
function syncChartTypeButtons(){
  document.querySelector('[title="折线"]').classList.toggle('active-box',indicatorSettings.chart_type==='line');
  document.querySelector('[title="K线类型"]').classList.toggle('active-box',indicatorSettings.chart_type!=='line');
}
syncChartTypeButtons();
document.querySelector('[title="折线"]').addEventListener('click',()=>setChartType('line'));
document.querySelector('[title="K线类型"]').addEventListener('click',()=>chartControlMenu('图表类型',[
  {label:`${indicatorSettings.chart_type!=='line'?'✓ ':''}蜡烛图`,action:()=>setChartType('candles')},
  {label:`${indicatorSettings.chart_type==='line'?'✓ ':''}折线图`,action:()=>setChartType('line')}
]));
document.querySelector('[aria-label="更多周期"]').addEventListener('click',()=>chartControlMenu('时间周期',[
  ...[['daily','1天'],['weekly','1周'],['monthly','1月']].map(([unit,label])=>({
    label,disabled:!chartBoot.series?.[unit]?.length,action:()=>switchProductionTimeframe(unit)})),
  {label:'分钟行情尚未接入；不以日线模拟分钟线。'}
]));
window.openChartIconPicker=()=>chartControlMenu('选择图标',[
  ...['★','●','✓','!','?','↑','↓'].map(text=>({label:text,action:()=>{
    startTool('text',document.querySelector('[data-tool="smile"]'));pendingDrawingText=text;
  }}))
]);
function showChartIdentity(){
  chartControlMenu('当前标的行情资料',[
    {label:`${chartBoot.name} · ${chartBoot.symbol}`},
    {label:`来源：${chartBoot.source||'公开日线样例'} · 复权：${chartBoot.adjustment||'原样例口径'}`},
    {label:`行情区间：${bars[0].date} ～ ${bars.at(-1).date}`},
    {label:chartBoot.production?'从主工作区的候选、自选或交易记录切换标的；画线按证券、来源和复权独立保存。':'原型用于操作核对；示意策略标绘不代表真实策略命中。'}
  ]);
}
document.querySelector('.symbol-pill').title='当前标的行情资料';
document.querySelector('.symbol-pill').addEventListener('click',showChartIdentity);
document.querySelector('.currency').addEventListener('click',showChartIdentity);
document.querySelector('.bottom-axis .adjustment')?.addEventListener('click',showChartIdentity);
document.querySelectorAll('button').forEach(button=>{
  if(button.textContent.trim()==='ADJ')button.addEventListener('click',showChartIdentity);
});
document.querySelector('.round-button').title='多图对照';
document.querySelector('.round-button').addEventListener('click',()=>document.querySelector('#openChartLayout').click());
document.querySelector('.layout-name').addEventListener('click',()=>document.querySelector('#openChartLayout').click());
document.querySelector('.avatar').title='本机图表工作区';
document.querySelector('.avatar').addEventListener('click',()=>chartControlMenu('本机图表工作区',[
  {label:'标绘、指标、视口和收藏栏保存到本机；切换或关闭先等待最后一次保存。'},
  {label:'提醒仅检查运行期间新增的正式收盘行情；仓位工具仅作分析测算。'},
  {label:'撤销 Ctrl+Z · 重做 Ctrl+Shift+Z · 重置 Alt+R · 快照 Ctrl+Shift+S'}
]));
