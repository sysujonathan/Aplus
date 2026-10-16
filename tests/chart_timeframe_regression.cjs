/* Execute the real production functions, with only their UI/Store boundaries replaced. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const assets = path.resolve(__dirname, '../docs/prototypes/tv-workspace');
const source = fs.readFileSync(path.join(assets, 'prototype-interactions.js'), 'utf8');
const functionSource = name => {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
};
const series = {
  daily: ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09',
    '2026-01-12', '2026-01-13', '2026-01-14', '2026-01-15', '2026-01-16',
    '2026-02-02', '2026-02-03'].map(date => ({date, close: 10})),
  weekly: ['2026-01-09', '2026-01-16', '2026-02-06'].map(date => ({date, close: 10})),
  monthly: ['2026-01-31', '2026-02-28'].map(date => ({date, close: 10}))
};
const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {style:{}, innerHTML:'', textContent:''});
  return elements.get(selector);
};
const saves = [];
const context = vm.createContext({
  window: {}, structuredClone, console,
  document: {querySelector: element, querySelectorAll: () => [], addEventListener: () => {}},
  localStorage: {setItem: () => {}},
  bars: structuredClone(series.daily), chartBoot: {production:true, series, name:'样本', symbol:'sz.000001'},
  userObjects: [{id:'t',type:'trend',a:{index:1,value:10.123456789},b:{index:7,value:11.234567891},createdAtIndex:7},
    {id:'p',type:'path',a:{index:1,value:10},b:{index:3,value:12},
      points:[{index:1,value:10},{index:2,value:11},{index:3,value:12}],createdAtIndex:7}],
  activeTimeframeUnit:'daily', ema:[], replayIndex:11, viewCount:12, viewOffset:0,
  priceZoom:1, priceCenterOffset:0, DEFAULT_VISIBLE_BARS:204, undoStack:[], redoStack:[],
  bridgeRevision:0, bridgeSaveChain:Promise.resolve(), bridgeSaveFailed:false, tvStateKey:'test',
  renderWorkspace:()=>{}, updateObjectTree:()=>{}, showToast:()=>{}, closeWorkspaceModal:()=>{},
  chartAlerts:[]
});
vm.runInContext(fs.readFileSync(path.join(assets,'chart-coordinates.js'),'utf8'), context);
context.chartCoordinates = context.window.AplusChartCoordinates;
let closed = false;
let cancelled = false;
context.window.pywebview = {api: {
  save_state: async (state, revision) => { saves.push(structuredClone(state)); return {revision:revision+1}; },
  finish_close: async () => {closed = true;}, cancel_close: () => {cancelled = true;}
}};
context.snapDrawingPoint = point => ({ index:point.index, value:point.value });
vm.runInContext(['savePrototypeState','switchProductionTimeframe','moveObjectFromOriginal'].map(functionSource).join('\n'), context);
async function run() {
  await vm.runInContext('savePrototypeState()', context);
  const original = JSON.stringify(context.userObjects.map(o => ({a:o.a,b:o.b,points:o.points,created:o.createdAtDate})));
  context.undoStack.push(structuredClone(context.userObjects));
  for (const timeframe of ['weekly','monthly','daily']) {
    vm.runInContext(`switchProductionTimeframe('${timeframe}')`, context);
    await context.bridgeSaveChain;
    if (timeframe === 'weekly') {
      context.editOriginal=structuredClone(context.userObjects[0]);
      context.editObject=structuredClone(context.editOriginal);
      vm.runInContext("moveObjectFromOriginal(editObject,editOriginal,{}, {index:0,value:12}, 'a')",context);
      assert.equal(context.editObject.a.date,'2026-01-06');
      assert.equal(context.editObject.a.value,12);
      vm.runInContext("moveObjectFromOriginal(editObject,editOriginal,{}, {index:1,value:12}, 'a')",context);
      assert.equal(context.editObject.a.date,'2026-01-16');
    }
    for (const object of context.userObjects) {
      assert.equal(object.a.date, '2026-01-06');
      assert.equal(object.createdAtDate, '2026-01-14');
    }
  }
  assert.equal(JSON.stringify(context.userObjects.map(o => ({a:o.a,b:o.b,points:o.points,created:o.createdAtDate}))), original);
  // Every queued snapshot was captured immediately, not mutated by later switches.
  assert.equal(saves[0].timeframe,'daily');
  assert.equal(saves[1].objects[0].a.index,0);
  assert.equal(saves[2].objects[0].a.index,0);
  assert.equal(saves[3].objects[0].a.index,1);
  assert.equal(context.undoStack[0][0].a.index,1);
  const c = context.chartCoordinates;
  assert.equal(c.indexForDate(series.weekly,'2026-01-12','weekly'),1); // nearest would select Jan 9
  assert.equal(c.indexForDate(series.monthly,'2026-02-02','monthly'),1); // nearest would select Jan 31
  assert.equal(c.indexForDate(series.daily,'2026-01-10','daily'),-1);
  assert.equal(c.indexForDate(series.weekly,'2025-01-07','weekly'),-1);
  assert.equal(c.indexForDate(series.monthly,undefined,'monthly'),-1);
  assert.equal(c.move(context.userObjects[0].a,0,2,series.daily).date,'2026-01-06');
  assert.equal(c.move(context.userObjects[0].a,1,2,series.daily).date,'2026-01-07');
  // Exercise the actual evidence renderer with real bucket locations and missing dates.
  const calls = [];
  context.callout = (_ctx,label,x) => calls.push([label,x]);
  context.registerRect = () => {};
  const ctx = new Proxy({}, {get:()=>()=>{}, set:()=>true});
  const view = {left:0,width:1000,right:0,candleWidth:10,y:v=>v,xGlobal:i=>i};
  context.ctx = ctx; context.testView = view; context.replayActive = false;
  context.chartBoot.evidence = {gap_floor:9,gap_top:10,bo_date:'2026-01-13',bo_high:11,
    h1_date:'2026-01-14',h1_high:12,h2_setup_date:'2026-02-02',h2_high:13};
  vm.runInContext(functionSource('drawStrategyEvidence'),context);
  for (const timeframe of ['weekly','monthly']) {
    vm.runInContext(`switchProductionTimeframe('${timeframe}'); drawStrategyEvidence(ctx,testView)`, context);
    await context.bridgeSaveChain;
    assert.deepEqual(calls.splice(0).slice(0,3),[['BO',timeframe==='weekly'?1:0],['H1',timeframe==='weekly'?1:0],['H2',timeframe==='weekly'?2:1]]);
  }
  context.chartBoot.evidence.bo_date='2025-01-07';
  vm.runInContext('drawStrategyEvidence(ctx,testView)',context);
  assert.equal(calls.length,0);
  // Actual alert click handler passes the displayed period and queues behind prior saves.
  const handlerStart = source.indexOf("document.querySelector('#createAlert').addEventListener");
  const handler = source.slice(handlerStart,source.indexOf('\n});',handlerStart)+4);
  element('#createAlert').addEventListener=(_type,fn)=>context.createAlert=fn;
  element('#alertPrice').value='12.50'; element('#alertName').value='周线突破';
  const alerts=[];
  context.window.pywebview.api.create_price_alert=async (...args)=>{
    alerts.push(args); return {revision:context.bridgeRevision+1,alert:{}};
  };
  vm.runInContext(handler,context);
  for (const timeframe of ['weekly','monthly']) {
    context.activeTimeframeUnit=timeframe;
    await context.createAlert();
    assert.equal(alerts.at(-1)[4],timeframe);
  }
  // Closing waits for delayed Store acknowledgement and retains the window on failure.
  const closeStart = source.indexOf('let closingWorkspace = false;');
  const closeEnd = source.indexOf('\n};',closeStart)+3;
  vm.runInContext(source.slice(closeStart,closeEnd),context);
  let release;
  context.window.pywebview.api.save_state=()=>new Promise(resolve=>{release=resolve;});
  const closing=context.window.aplusFlushAndClose();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(closed,false);
  release({revision:context.bridgeRevision+1}); await closing;
  assert.equal(closed,true);
  vm.runInContext('closingWorkspace=false',context); closed=false;
  context.window.pywebview.api.save_state=async()=>{throw new Error('revision conflict');};
  await context.window.aplusFlushAndClose();
  assert.equal(closed,false); assert.equal(cancelled,true);
  console.log(JSON.stringify({ok:true,saves:saves.length,states:saves,dates:series.daily.map(row=>row.date)}));
}
run().catch(error=>{console.error(error);process.exitCode=1;});
