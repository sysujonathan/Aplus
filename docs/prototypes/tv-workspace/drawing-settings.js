/* Tool-specific settings learned from the authorized TV interface. */
const positionStatNames={tp_price:'TP价格偏移',tp_percent:'TP百分比偏移',tp_ticks:'TP tick偏移',
  tp_amount:'TP金额',tp_pl:'TP PL',pnl:'开盘/收盘损益',quantity:'数量',ratio:'风险/回报比',
  sl_price:'SL价格偏移',sl_percent:'SL百分比偏移',sl_ticks:'SL tick偏移',sl_amount:'SL金额',sl_pl:'SL PL'};
let lastObjectTab=null;
function materializeFibUniformColor(object) {
  // Old documents used a persistent override. Editing a level must preserve
  // the other levels' visible style before replacing that legacy override.
  if (!object.fibUseOneColor) return;
  object.fibLevels.forEach(level => {
    level.color = object.fibSingleColor || object.color;
    level.alpha = object.fibSingleAlpha ?? 1;
  });
  object.fibUseOneColor = false;
}
function assignFibUniformColor(object, color) {
  materializeFibUniformColor(object);
  object.fibSingleColor = color;
  object.fibLevels.forEach(level => { level.color = color; });
}
function assignFibUniformAlpha(object, alpha) {
  materializeFibUniformColor(object);
  object.fibSingleAlpha = alpha;
  object.fibLevels.forEach(level => { level.alpha = alpha; });
}
function chartPrice(value,precision='default') {
  const decimals=precision==='default' ? (chartBoot.priceStep?String(chartBoot.priceStep).split('.')[1]?.length||0:2) : Number(precision);
  return Number(value).toFixed(Math.max(0,Math.min(8,decimals)));
}
function drawingPriceInput(value) {
  // Keep canonical prices intact; strip only binary floating-point display noise.
  return Number(Number(value).toFixed(8));
}
function drawAxisPriceTags(ctx,view) {
  const tag=(value,color)=>{
    if(!Number.isFinite(value)||value<view.low||value>view.high)return;
    priceTag(ctx,chartPrice(value),view.y(value),color,view.width,view.right);
  };
  if(indicatorSettings.ema && indicatorSettings.indicators_visible!==false && indicatorSettings['ema_'+activeTimeframeUnit]!==false && indicatorSettings.ema_price_labels!==false){
    const index=Math.min(replayIndex,bars.length-1)-Number(indicatorSettings.ema_offset);
    if(index<=replayIndex)tag(ema[index],indicatorSettings.ema_color);
  }
  if(!drawingsVisible)return;
  userObjects.filter(object=>object.visible!==false&&objectVisibleAtTimeframe(object)&&
    (!replayActive||(object.createdAtDate||bars[object.createdAtIndex??bars.length-1]?.date)<=
      (replayCutoffDate||bars[replayIndex].date))).forEach(object=>{
    if(object.type==='ray'&&object.showPrice!==false)tag(object.a.value,object.color);
    if(object.type==='trend'&&object.priceLabels){tag(object.a.value,object.color);tag(object.b.value,object.color);}
    if(['long','short'].includes(object.type)&&object.priceLabels!==false){
      tag(object.a.value,object.color);tag(object.b.value,object.targetColor||'#089981');tag(object.stop,object.stopColor||'#f23645');
    }
  });
}
function configureDrawingTabs(object) {
  const position=['long','short'].includes(object.type);
  const text=['trend','ray','channel','rectangle','up','down','arrow'].includes(object.type);
  document.querySelector('#objectModal').style.width=object.type==='fib'?'458px':position?'432px':'380px';
  document.querySelector('#objectTabs [data-tab="inputs"]').hidden=!position;
  document.querySelector('#objectTabs [data-tab="coordinates"]').hidden=position||object.type==='path';
  document.querySelector('#objectTabs [data-tab="text"]').hidden=!text;
  const active=position?(lastObjectTab==='style'||lastObjectTab==='visibility'?lastObjectTab:'inputs'):
    !lastObjectTab||lastObjectTab==='inputs'||(lastObjectTab==='text'&&!text)||(lastObjectTab==='coordinates'&&object.type==='path')?'style':lastObjectTab;
  document.querySelectorAll('#objectTabs button').forEach(button=>button.classList.toggle('active',button.dataset.tab===active));
  document.querySelectorAll('.object-tab-panel').forEach(panel=>panel.classList.toggle('active',panel.dataset.panel===active));
  if(position)renderPositionInputs(object);
  if(text)renderDrawingTextSettings(object);
}
function bindDrawingStyle(host,object) {
  host.querySelectorAll('[data-setting]').forEach(control=>{
    if(control.type==='checkbox')control.checked=object[control.dataset.setting]??control.checked;
    else if(object[control.dataset.setting]!==undefined)control.value=control.type==='range'
      ? Math.round(object[control.dataset.setting]*100) : object[control.dataset.setting];
    control.addEventListener(['text','textarea'].includes(control.type)?'input':'change',()=>{
      const ratio=control.type==='range';
      let value=control.type==='checkbox'?control.checked:control.type==='number'||control.dataset.numeric||ratio||['width','middleWidth'].includes(control.dataset.setting)?Number(control.value):control.value;
      if(ratio)value/=100;
      if(typeof value==='number'&&(!Number.isFinite(value)||(ratio?value<0:value<=0)||value>500)){showToast('请输入有效数值');return;}
      rememberObjects();object[control.dataset.setting]=value;savePrototypeState();renderWorkspace('overlay');
      if(host.querySelector('[data-stroke-preview]'))refreshFibStylePreviews(object);
    });
  });
  host.querySelectorAll('[data-color]').forEach(button=>button.onclick=()=>{
    rememberObjects();object.color=button.dataset.color;
    host.querySelector('[data-setting="color"]').value=object.color;
    host.querySelectorAll('[data-color]').forEach(item=>item.classList.toggle('active',item===button));
    savePrototypeState();renderWorkspace('overlay');
  });
}
function channelLevels(object) {
  return object.channelLevels || [
    {value:0,enabled:true}, ...(object.showMiddle===false?[]:[{value:.5,enabled:true,dash:'dashed'}]),
    {value:1,enabled:true}];
}
function ensureChannelLevels(object) {
  object.channelLevels ||= [-.25,0,.25,.5,.75,1,1.25].map(value=>({value,
    enabled:value===0||value===1||(value===.5&&object.showMiddle!==false),
    color:object.color,width:object.width||1,dash:value===.5?'dashed':object.dash||'solid'}));
  return object.channelLevels;
}
function renderChannelSettings(object) {
  ensureChannelLevels(object);
  const host=document.querySelector('#toolSpecificSettings');
  const boundary=index=>object.channelLevels.length===7?[1,5].includes(index):[0,object.channelLevels.length-1].includes(index);
  const extensionLabel=()=>object.extendLeft?(object.extendRight?'双向延伸':'延伸左边线条'):object.extendRight?'延伸右边线条':'不要扩大';
  host.innerHTML=`<div class="channel-levels">${object.channelLevels.map((level,index)=>`<div class="channel-level-row ${level.enabled?'':'disabled'}" data-channel-index="${index}">
      ${boundary(index)?'<span></span>':`<input type="checkbox" data-channel-field="enabled" aria-label="启用 ${level.value} 通道线" ${level.enabled?'checked':''}>`}
      <input type="number" step="any" data-channel-field="value" aria-label="通道比例 ${index+1}" value="${level.value}" ${level.enabled?'':'disabled'}>
      <span class="stroke-preview-button" data-stroke-preview="channel"><i class="stroke-preview-color" style="background:${level.color||object.color}"></i><i class="stroke-preview-line" style="border-top-width:${level.width||1}px;border-top-style:${level.dash||'solid'}"></i><input type="color" data-channel-field="color" data-stroke-width="width" data-stroke-dash="dash" aria-label="通道比例 ${level.value} 颜色、粗细和线型" value="${level.color||object.color}" ${level.enabled?'':'disabled'}></span></div>`).join('')}</div>
    <div class="setting-row"><span>延伸</span><details class="position-stats"><summary><span data-channel-extension-label>${extensionLabel()}</span><span>⌄</span></summary><div class="stats-menu">
      <label><input type="checkbox" data-setting="extendLeft" ${object.extendLeft?'checked':''}>延伸左边线条</label>
      <label><input type="checkbox" data-setting="extendRight" ${object.extendRight?'checked':''}>延伸右边线条</label></div></details></div>
    <div class="setting-row"><label><input type="checkbox" data-setting="fill" ${object.fill===false?'':'checked'}>背景</label>
    <span class="stroke-preview-button fill-preview-button" data-stroke-preview="background"><i class="stroke-preview-color" style="background:${object.backgroundColor||object.color}"></i><input type="color" data-setting="backgroundColor" value="${object.backgroundColor||object.color}" aria-label="通道背景颜色和不透明度"></span></div>`;
  bindDrawingStyle(host,object);
  refreshFibStylePreviews(object);
  host.querySelectorAll('[data-channel-field]').forEach(control=>{
    const level=object.channelLevels[Number(control.closest('[data-channel-index]').dataset.channelIndex)];
    control.addEventListener('change',()=>{
      const key=control.dataset.channelField;
      const value=control.type==='checkbox'?control.checked:['value','width'].includes(key)?Number(control.value):control.value;
      if(typeof value==='number'&&(!Number.isFinite(value)||Math.abs(value)>100)){renderChannelSettings(object);return;}
      rememberObjects();level[key]=value;savePrototypeState();renderWorkspace('overlay');
      if(key==='enabled')renderChannelSettings(object);else refreshFibStylePreviews(object);
    });
  });
  host.querySelectorAll('[data-setting="extendLeft"],[data-setting="extendRight"]').forEach(control=>control.addEventListener('change',()=>{
    host.querySelector('[data-channel-extension-label]').textContent=extensionLabel();
  }));
}
function renderTrendSettings(object) {
  const host=document.querySelector('#toolSpecificSettings');
  const ends=key=>`<select data-setting="${key}" aria-label="${key==='startStyle'?'起点':'终点'}样式">${[['none','普通'],['arrow','箭头']].map(([value,label])=>`<option value="${value}" ${(object[key]||(key==='endStyle'&&object.type==='arrow'?'arrow':'none'))===value?'selected':''}>${label}</option>`).join('')}</select>`;
  host.innerHTML=`${commonLineSettings(object)}<div class="setting-row"><span>起点／终点</span>${ends('startStyle')}${ends('endStyle')}</div>
    ${object.type==='trend'?'<label>延伸<select data-trend-extend><option value="none">不要扩大</option><option value="left">向左延伸</option><option value="right">向右延伸</option><option value="both">双向延伸</option></select></label>':''}
    <label><input type="checkbox" data-setting="showMidpoint" ${object.showMidpoint?'checked':''}>中点</label>
    <label><input type="checkbox" data-setting="priceLabels" ${object.priceLabels?'checked':''}>价格标签</label>
    <details class="position-stats"><summary>信息／统计数据 <span>⌄</span></summary><div class="stats-menu">${[['price','价格范围'],['percent','百分比变化'],['ticks','点数变化'],['bars','K线范围'],['time','日期/时间范围'],['distance','距离'],['angle','角度']].map(([key,label])=>`<label><input type="checkbox" data-trend-stat="${key}" ${object.trendStats?.[key]?'checked':''}>${label}</label>`).join('')}</div></details>
    <label>统计位置<select data-setting="statsPosition"><option value="left">左</option><option value="center">中心</option><option value="right">右</option></select></label>
    <label><input type="checkbox" data-setting="alwaysShowStats" ${object.alwaysShowStats?'checked':''}>始终显示统计信息</label>`;
  bindDrawingStyle(host,object);host.querySelector('[data-setting="statsPosition"]').value=object.statsPosition||'right';
  const extend=host.querySelector('[data-trend-extend]');
  if(extend){extend.value=object.extendLeft?(object.extendRight?'both':'left'):object.extendRight?'right':'none';
    extend.onchange=()=>{rememberObjects();object.extendLeft=['left','both'].includes(extend.value);object.extendRight=['right','both'].includes(extend.value);savePrototypeState();renderWorkspace('overlay');};}
  host.querySelectorAll('[data-trend-stat]').forEach(control=>control.addEventListener('change',()=>{
    rememberObjects();object.trendStats={...object.trendStats,[control.dataset.trendStat]:control.checked};savePrototypeState();renderWorkspace('overlay');
  }));
}
function drawTrendDetails(ctx,object,ax,ay,bx,by,selected) {
  const endpoint=(style,x,y,fromX,fromY)=>{
    if(style==='arrow')drawArrowHead(ctx,fromX,fromY,x,y);
    if(style==='circle'){ctx.beginPath();ctx.arc(x,y,3,0,2*Math.PI);ctx.fill();}
  };
  endpoint(object.startStyle||(object.arrowEnds==='both'?'arrow':'none'),ax,ay,bx,by);
  endpoint(object.endStyle||(object.type==='arrow'?'arrow':'none'),bx,by,ax,ay);
  if(object.showMidpoint){ctx.beginPath();ctx.arc((ax+bx)/2,(ay+by)/2,3,0,2*Math.PI);ctx.stroke();}
  if(!selected&&!object.alwaysShowStats)return;
  const change=object.b.value-object.a.value,fields=object.trendStats||{},tick=Number(chartBoot.priceStep);
  const items=[fields.price?chartPrice(change):'',fields.percent?`${(change/object.a.value*100).toFixed(2)}%`:'',
    fields.ticks&&tick>0?`${Math.round(change/tick)} ticks`:'',fields.bars?`${Math.abs(Math.round(object.b.index-object.a.index))}根K线`:'',
    fields.time&&object.a.date&&object.b.date?`${Math.abs(Math.round((Date.parse(object.b.date)-Date.parse(object.a.date))/86400000))}天`:'',
    fields.distance?`${(Math.hypot(bx-ax,by-ay)/(canvas._view.candleWidth||1)).toFixed(2)} 周期`:'',
    fields.angle?`${(Math.atan2(ay-by,bx-ax)*180/Math.PI).toFixed(1)}°`:''].filter(Boolean);
  if(!items.length)return;
  const at=object.statsPosition==='left'?0:object.statsPosition==='center'?.5:1;
  ctx.save();ctx.font='12px Segoe UI';ctx.textAlign=at===0?'left':at===1?'right':'center';ctx.textBaseline='bottom';
  ctx.fillText(items.join(' · '),ax+(bx-ax)*at,ay+(by-ay)*at-8);ctx.restore();
}
let activeChartInlineEditor=null;
window.flushChartInlineEditor=(commit=true,persist=true)=>activeChartInlineEditor?.(commit,persist);
function editFibLevelText(object,index,point) {
  window.flushChartInlineEditor();
  const level=object.fibLevels[index],original=String(level.text||'');
  const input=document.createElement('input');input.type='text';input.maxLength=50;input.value=original;
  input.className='chart-inline-text';input.setAttribute('aria-label',`水平位 ${level.value} 的文本`);
  input.style.left=`${clamp(point.x,8,wrap.clientWidth-215)}px`;input.style.top=`${clamp(point.y-18,8,wrap.clientHeight-70)}px`;
  let finished=false;
  activeChartInlineEditor=(commit=true,persist=true)=>{
    if(finished)return;finished=true;activeChartInlineEditor=null;
    const text=input.value.replace(/[\r\n]/g,'').slice(0,50);
    if(commit&&text!==original){rememberObjects();level.text=text;}
    input.remove();if(commit&&text!==original&&persist)savePrototypeState();renderWorkspace('overlay');
  };
  input.onkeydown=event=>{event.stopPropagation();if(event.key==='Enter'){event.preventDefault();window.flushChartInlineEditor();}
    if(event.key==='Escape'){event.preventDefault();window.flushChartInlineEditor(false);}};
  input.onblur=()=>window.flushChartInlineEditor();wrap.append(input);input.focus();input.select();
}
function drawFibLevelText(ctx,object,level,index,startX,endX,selected,draft) {
  if(object.fibText===false)return;
  const horizontal=object.fibTextHorizontal||'center',vertical=object.fibTextVertical||'middle';
  const px=horizontal==='left'?startX+5:horizontal==='right'?endX-5:(startX+endX)/2;
  const hover=typeof hoverPoint!=='undefined'&&hoverPoint&&Math.abs(hoverPoint.y-level.py)<10&&hoverPoint.x>=startX&&hoverPoint.x<=endX;
  const text=String(level.text||'')||(!draft&&selected&&hover?'+ 添加文本':'');
  if(!text)return;
  ctx.save();ctx.textAlign=horizontal;ctx.textBaseline=vertical==='top'?'bottom':vertical==='bottom'?'top':'middle';
  ctx.font=`${object.fibFontSize||12}px Segoe UI`;const py=level.py+(vertical==='top'?-5:vertical==='bottom'?5:0);
  ctx.fillText(text,px,py);const width=ctx.measureText(text).width;
  const left=horizontal==='left'?px:horizontal==='right'?px-width:px-width/2;
  if(!draft)hitRegions.push({id:object.id,handle:`fib-text:${index}`,hit:(x,y)=>x>=left-5&&x<=left+width+5&&Math.abs(y-py)<12});
  ctx.restore();
}
function renderPositionInputs(object) {
  const host=document.querySelector('#positionInputs');
  const legacy=object.risk_value===undefined;
  const tick=chartBoot.production?Number(chartBoot.priceStep):.01,direction=object.type==='short'?-1:1;
  const knownTick=Number.isFinite(tick)&&tick>0;
  const risk=(object.a.value-object.stop)*direction,reward=(object.b.value-object.a.value)*direction;
  host.innerHTML=`<div class="position-inputs">
    <label>账户规模<input data-position="account_size" type="number" min="0.01" step="any" value="${object.account_size??1000}"><select data-position="account_currency" aria-label="账户规模单位"><option value="default">系统预设</option><option value="CNY">CNY</option></select></label>
    <label>手数<input data-position="lot_size" type="number" min="0.000001" step="any" value="${object.lot_size??1}"></label>
    <label>风险<input data-position="risk_value" type="number" min="0.000001" step="any" value="${object.risk_value??object.risk_budget??1000}"><select data-position="risk_unit" aria-label="风险单位"><option value="percent">%</option><option value="cash">金额</option></select></label>
    <label>进场价格<input data-position="entry" type="number" min="0.000001" step="any" value="${drawingPriceInput(object.a.value)}"></label>
    <label>杠杆<input data-position="leverage" type="number" min="0.000001" step="any" value="${object.leverage??10000}"></label>
    <div class="position-level-row"><b>止盈水平</b><label>Ticks<input data-position="target_ticks" type="number" min="1" step="1" value="${knownTick?Math.round(reward/tick):0}" ${knownTick?'':'disabled'}></label><label>价格<input data-position="target" type="number" min="0.000001" step="any" value="${drawingPriceInput(object.b.value)}"></label></div>
    <div class="position-level-row"><b>止损水平</b><label>Ticks<input data-position="stop_ticks" type="number" min="1" step="1" value="${knownTick?Math.round(risk/tick):0}" ${knownTick?'':'disabled'}></label><label>价格<input data-position="stop" type="number" min="0.000001" step="any" value="${drawingPriceInput(object.stop)}"></label></div>
    <label>QTY精度<select data-position="qty_precision"><option value="default">默认</option>${[0,1,2,3,4,5,6,7,8].map(value=>`<option value="${value}">${value}</option>`).join('')}</select></label>
    <p class="notice">图形分析假设，不读取真实账户或建立交易；非交易建议。</p></div>`;
  host.querySelector('[data-position="risk_unit"]').value=object.risk_unit??(legacy?'cash':'percent');
  host.querySelector('[data-position="qty_precision"]').value=object.qty_precision??'default';
  host.querySelector('[data-position="account_currency"]').value=object.account_currency??'default';
  host.querySelectorAll('[data-position]').forEach(control=>control.addEventListener('change',()=>{
    const key=control.dataset.position;
    let value=control.tagName==='SELECT'?control.value:Number(control.value);
    if(typeof value==='number'&&(!Number.isFinite(value)||value<=0)){
      showToast('请输入有效正数');renderPositionInputs(object);return;
    }
    if(key.endsWith('_ticks')&&!Number.isInteger(value)){showToast('Ticks 必须为整数');renderPositionInputs(object);return;}
    const proposed=structuredClone(object);
    if(key==='entry'){
      const delta=value-proposed.a.value;proposed.a.value=value;proposed.b.value+=delta;proposed.stop+=delta;
    }else if(key==='target')proposed.b.value=value;
    else if(key==='target_ticks')proposed.b.value=Number((proposed.a.value+direction*value*tick).toFixed(8));
    else if(key==='stop_ticks')proposed.stop=Number((proposed.a.value-direction*value*tick).toFixed(8));
    else proposed[key]=value;
    if((proposed.a.value-proposed.stop)*direction<=0||(proposed.b.value-proposed.a.value)*direction<=0||proposed.stop<=0||proposed.b.value<=0){
      showToast('请核对目标和止损相对进场的方向');renderPositionInputs(object);return;
    }
    if(legacy && key!=='risk_value'){
      proposed.risk_value=Number(host.querySelector('[data-position="risk_value"]').value);
      proposed.risk_unit=host.querySelector('[data-position="risk_unit"]').value;
    }
    if(key==='risk_value')proposed.risk_unit=host.querySelector('[data-position="risk_unit"]').value;
    proposed.risk_budget=Number(proposed.risk_value??1000)*(proposed.risk_unit==='percent'?Number(proposed.account_size??1000)/100:1);
    rememberObjects();Object.assign(object,proposed);savePrototypeState();renderPositionInputs(object);renderWorkspace('overlay');
  }));
}
function renderPositionStyle(object) {
  const host=document.querySelector('#toolSpecificSettings');
  const defaults=object.position_version?false:true;
  host.innerHTML=`${commonLineSettings(object,'线条')}
    <label class="position-style-row">止损颜色<input type="color" data-setting="stopColor" value="${object.stopColor||'#f23645'}"></label>
    <label class="position-style-row">目标颜色<input type="color" data-setting="targetColor" value="${object.targetColor||'#089981'}"></label>
    <label class="position-style-row">文本<input type="color" data-setting="textColor" value="${object.textColor||'#ffffff'}"><select data-setting="fontSize" data-numeric="true">${[10,11,12,14,16,20,24].map(n=>`<option value="${n}" ${n===(object.fontSize||12)?'selected':''}>${n}</option>`).join('')}</select></label>
    <label><input type="checkbox" data-setting="priceLabels" ${object.priceLabels===false?'':'checked'}>价格标签</label>
    <details class="position-stats"><summary>信息／统计数据 <span>⌄</span></summary><div class="stats-menu">${Object.entries(positionStatNames).map(([key,label])=>`<label><input type="checkbox" data-position-stat="${key}" ${(object.positionStats?.[key]??!['tp_pl','sl_pl'].includes(key))?'checked':''}>${label}</label>`).join('')}</div></details>
    <label><input type="checkbox" data-setting="compactStats" ${object.compactStats?'checked':''}>精简统计模式</label>
    <label><input type="checkbox" data-setting="alwaysShowStats" ${(object.alwaysShowStats??defaults)?'checked':''}>始终显示统计信息</label>`;
  bindDrawingStyle(host,object);
  host.querySelectorAll('[data-position-stat]').forEach(control=>control.addEventListener('change',()=>{
    rememberObjects();object.positionStats={...object.positionStats,[control.dataset.positionStat]:control.checked};
    savePrototypeState();renderWorkspace('overlay');
  }));
}
function drawingTextDefaults(object) {
  if(object.type==='ray')return {fontSize:12,vertical:'bottom',horizontal:'right'};
  if(object.type==='rectangle')return {fontSize:14,vertical:'middle',horizontal:'center'};
  if(object.type==='channel')return {fontSize:14,vertical:'top',horizontal:'left'};
  if(object.type==='trend'||(object.type==='arrow'&&!object.arrow_marker_version))
    return {fontSize:14,vertical:'top',horizontal:'center'};
  return {fontSize:object.type==='arrow'?16:14,vertical:'top',horizontal:'center'};
}
function renderDrawingTextSettings(object) {
  const host=document.querySelector('#drawingTextSettings');
  const defaults=drawingTextDefaults(object);
  const marker=['up','down'].includes(object.type)||Boolean(object.arrow_marker_version);
  const verticalOptions=[['top','顶部'],['middle',object.type==='rectangle'?'内部':'中间'],['bottom','底部']];
  host.innerHTML=`<div class="drawing-text-toolbar"><input type="color" data-setting="textColor" value="${object.textColor||object.color||'#2962ff'}"><select data-setting="fontSize" data-numeric="true">${[10,11,12,14,16,20,24,28,32,40].map(n=>`<option value="${n}" ${n===(object.fontSize||defaults.fontSize)?'selected':''}>${n}</option>`).join('')}</select><label><input type="checkbox" data-setting="textBold" ${object.textBold?'checked':''}><b>B</b></label><label><input type="checkbox" data-setting="textItalic" ${object.textItalic?'checked':''}><i>I</i></label></div>
    <textarea data-setting="text" placeholder="添加文字" rows="4">${escapeChartHtml(object.text||'')}</textarea>
    ${marker?'':`<div class="setting-row"><span>文字对齐</span><select data-setting="textVertical">${verticalOptions.map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select><select data-setting="textAlign"><option value="left">左</option><option value="center">中心</option><option value="right">右</option></select></div>`}`;
  if(!marker){
    host.querySelector('[data-setting="textVertical"]').value=object.textVertical||defaults.vertical;
    host.querySelector('[data-setting="textAlign"]').value=object.textAlign||defaults.horizontal;
  }
  bindDrawingStyle(host,object);
}

function drawPositionStats(ctx,object,result,x1,x2,y) {
  const stat=key=>object.positionStats?.[key]??!['tp_pl','sl_pl'].includes(key);
  const number=value=>Number.isFinite(value)?Number(value).toFixed(2):'—';
  const center=(x1+x2)/2,font=Number(object.fontSize||12);
  const tag=(text,py,color)=>{
    if(!text)return;const lines=text.split('\n');ctx.save();ctx.font=`${font}px "Microsoft YaHei",sans-serif`;
    const width=Math.max(...lines.map(line=>ctx.measureText(line).width))+12,height=lines.length*(font+3)+6;
    ctx.globalAlpha=object.textAlpha??1;ctx.fillStyle=color;ctx.fillRect(center-width/2,py-height/2,width,height);
    ctx.fillStyle=object.textColor||'#ffffff';ctx.textAlign='center';ctx.textBaseline='middle';
    lines.forEach((line,n)=>ctx.fillText(line,center,py+(n-(lines.length-1)/2)*(font+3)));ctx.restore();
  };
  const edge=(prefix,distance,percent,ticks,amount,pnl)=>[
    stat(prefix+'_price')?`${prefix==='tp'?'目标':'停止'}: ${number(distance)}`:'',
    stat(prefix+'_percent')?`(${number(percent)}%)`:'',stat(prefix+'_ticks')?number(ticks):'',
    stat(prefix+'_amount')?`金额: ${number(amount)}`:'',stat(prefix+'_pl')?`PL: ${number(pnl)}`:''].filter(Boolean).join(' ');
  tag(edge('tp',result.reward,result.targetPercent,result.targetTicks,result.targetAmount,result.targetPnl),
    y(result.target)-16,object.targetColor||'#089981');
  tag(edge('sl',result.risk,result.stopPercent,result.stopTicks,result.stopAmount,result.stopPnl),
    y(result.stop)+16,object.stopColor||'#f23645');
  const firstLine=[stat('pnl')?(result.status==='ambiguous'?'同K触及两端，先后待核对':result.status==='invalid'?'止损／目标方向无效':`${result.status==='closed'?'已结束':'开仓'} PnL: ${number(result.pnl)}`):'',
    stat('quantity')&&object.showQuantity!==false?`数量: ${result.quantity}`:''].filter(Boolean).join(', ');
  const middle=[firstLine,stat('ratio')&&object.showRatio!==false?`风险/回报比: ${number(result.ratio)}`:''].filter(Boolean);
  tag(middle.join(object.compactStats?' · ':'\n'),y(result.entry)-font-5,
    result.pnl!==null&&result.pnl<0?(object.stopColor||'#f23645'):(object.targetColor||'#089981'));
  ctx.fillStyle=object.textColor||'#cbd5e1';ctx.font='10px "Microsoft YaHei",sans-serif';ctx.textAlign='left';
  ctx.fillText('分析测算 · 非交易建议',x1+5,Math.max(y(result.target),y(result.stop))+font+28);
}

function drawAttachedText(ctx,view,object) {
  const a={x:view.xGlobal(object.a.index),y:view.y(object.a.value)},b={x:view.xGlobal((object.b||object.a).index),y:view.y((object.b||object.a).value)};
  const defaults=drawingTextDefaults(object);
  const horizontal=object.textAlign||defaults.horizontal,vertical=object.textVertical||defaults.vertical,size=Number(object.fontSize||defaults.fontSize);
  let px=horizontal==='left'?Math.min(a.x,b.x):horizontal==='right'?Math.max(a.x,b.x):(a.x+b.x)/2;
  let py=(a.y+b.y)/2;
  if(object.type==='ray'){px=horizontal==='right'?view.width-view.right-6:horizontal==='left'?a.x+6:(a.x+view.width-view.right)/2;py=a.y;}
  if(['up','down'].includes(object.type)){px=a.x;py=a.y+(object.type==='up'?20:-20);}
  if(object.type==='rectangle')py=vertical==='top'?Math.min(a.y,b.y)+6:vertical==='bottom'?Math.max(a.y,b.y)-6:(a.y+b.y)/2;
  else py+=vertical==='top'?-6:vertical==='bottom'?6:0;
  ctx.save();ctx.globalAlpha=(object.textAlpha??1)*(object.alpha??1);ctx.fillStyle=object.textColor||object.color;
  ctx.font=`${object.textItalic?'italic ':''}${object.textBold?'bold ':''}${size}px "Microsoft YaHei",sans-serif`;
  ctx.textAlign=horizontal;ctx.textBaseline=object.type==='rectangle'?(vertical==='top'?'top':vertical==='bottom'?'bottom':'middle')
    :vertical==='top'?'bottom':vertical==='bottom'?'top':'middle';
  const lines=String(object.text).split('\n');
  lines.forEach((line,n)=>ctx.fillText(line,px,py+n*(size+3)));
  const width=Math.max(...lines.map(line=>ctx.measureText(line).width));
  const left=horizontal==='left'?px:horizontal==='right'?px-width:px-width/2;
  registerRect(object.id,left,py-size,left+width,py+lines.length*(size+3));ctx.restore();
}
