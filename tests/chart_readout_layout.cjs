/* Exercise production display functions: canonical prices stay unchanged. */
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const path = require('node:path');
const assets = path.resolve(__dirname, '../docs/prototypes/tv-workspace');
const extract = (file, name) => {
  const source = fs.readFileSync(path.join(assets, file), 'utf8');
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
};
const elements = new Map();
const element = key => {
  if (!elements.has(key)) elements.set(key, {textContent:'', title:''});
  return elements.get(key);
};
let tags = [], segments = [];
const context = vm.createContext({
  document:{querySelector:element},
  bars:[{date:'2026-06-15', open:15.13, high:15.13, low:14.68, close:14.86},
        {date:'2026-11-04', open:17.5, high:17.6, low:17.3, close:17.4}],
  replayIndex:1, replayActive:false, activeTimeframeUnit:'daily', drawingsVisible:false,
  chartBoot:{name:'合成标的', source:'tickflow', adjustment:'前复权',
    dailyReference:{quote_time:'2026-11-04T16:14:51+08:00'}},
  indicatorSettings:{ema:true, ema_offset:0, ema_color:'#ff9800'}, ema:[15,17.32],
  ctx:{save(){}, restore(){}, setLineDash(){}, beginPath(){}, moveTo(x,y){segments.push([x,y]);},
       lineTo(x,y){segments.push([x,y]);}, stroke(){}},
  priceTag:(ctx,text,y,color,width,right,subtitle)=>tags.push({text,y,color,subtitle}),
});
vm.runInContext([
  extract('drawing-settings.js','chartPrice'),
  extract('drawing-settings.js','barCloseCountdown'),
  extract('drawing-settings.js','currentPriceCountdown'),
  extract('drawing-settings.js','layoutAxisPriceTags'),
  extract('drawing-settings.js','drawAxisPriceTags'),
  extract('prototype.js','updateReadout'),
  extract('prototype.js','drawLastPrice'),
  extract('prototype-interactions.js','periodLabel'),
  extract('prototype-interactions.js','refreshSeriesTitle'),
].join('\n'), context);
vm.runInContext('updateReadout(bars[0]);refreshSeriesTitle()',context);
assert.equal(element('#readoutDate').textContent,'选中K线 2026-06-15');
assert.equal(element('#closeValue').textContent,'14.86');
assert.match(element('.instrument-title b').textContent,/最新参考报价时间 2026-11-04T16:14:51/);
vm.runInContext('updateReadout(bars[1])',context);
assert.equal(element('#readoutDate').textContent,'末根K线 2026-11-04');
assert.equal(element('#closeValue').textContent,'17.40');
context.view = {rows:[{close:17.4}],low:14,high:20,top:20,bottom:50,height:600,
  width:900,right:61,y:value=>300-(value-17.4)*10};
vm.runInContext('drawAxisPriceTags(ctx,view)',context);
assert.deepEqual(tags.map(t=>t.text).sort(),['17.32','17.40']);
assert.ok(Math.abs(tags[0].y-tags[1].y)>=22,'EMA and close tags must not overlap');
assert.ok(segments.length>0,'displaced labels retain a connector to their actual price');
// The real close line stays at the actual coordinate; no second price tag is drawn.
tags=[];segments=[];
vm.runInContext('drawLastPrice(ctx,17.4,view.y,900,61,false)',context);
assert.equal(tags.length,0);assert.equal(segments[0][1],300);
for (const ys of [[20,20.1],[540,541],[10,300,1000],[100,100,100,100,100]]) {
  context.input=ys.map((py,i)=>({py,value:i}));
  const result=vm.runInContext('layoutAxisPriceTags(input,20,550)',context);
  assert.ok(result.every(t=>t.labelY>=30 && t.labelY<=540));
  for(let i=1;i<result.length;i++)assert.ok(result[i].labelY-result[i-1].labelY>=22);
}

const market = {sessions:[[570,690],[780,900]],days:{
  '2026-11-06':true, '2026-11-07':false, '2026-11-30':true,
  '2026-09-24':true, '2026-09-25':false,
}};
const countdown = (unit, stamp, now) =>
  context.barCloseCountdown(unit,stamp,market,Date.parse(now.replace(' ','T')+'+08:00'));
for (const [unit, previous, expected] of [
  ['m1','10:01','00:40'], ['m5','10:00','03:40'], ['m15','10:00','13:40'],
  ['m30','10:00','28:40'], ['m60','10:30','28:40'],
]) {
  assert.equal(countdown(unit,`2026-11-06 ${previous}`,'2026-11-06 10:01:20'),expected);
  assert.equal(countdown(unit,'2026-11-06 11:30','2026-11-06 11:42:29'),'');
}
assert.equal(countdown('m5','2026-11-06 13:05','2026-11-06 13:00:00'),'05:00');
assert.equal(countdown('m5','2026-11-06 14:55','2026-11-06 15:00:00'),'');
assert.equal(countdown('m1','2026-11-06 09:55','2026-11-06 10:01:20'),'');
assert.equal(countdown('daily','2026-11-06','2026-11-06 11:42:29'),'03:17:31');
assert.equal(countdown('daily','2026-11-06','2026-11-06 15:00:00'),'');
assert.equal(countdown('weekly','2026-09-25','2026-09-24 10:00:00'),'05:00:00');
assert.equal(countdown('monthly','2026-11-30','2026-11-07 10:00:00'),'23d 5h');
assert.equal(countdown('monthly','2026-09-30','2026-09-24 10:00:00'),'');

context.chartBoot.barClock = market;
context.bars[1].date = '2026-11-06';
context.view.rows[0].date = '2026-11-06';
const now = Date.parse('2026-11-06T11:42:29+08:00');
assert.equal(context.currentPriceCountdown(context.view,now),'03:17:31');
context.replayActive = true;
assert.equal(context.currentPriceCountdown(context.view,now),'');
context.replayActive = false;
context.view.rows[0].date = '2026-11-05';
assert.equal(context.currentPriceCountdown(context.view,now),'');
tags = [];
vm.runInContext("drawAxisPriceTags(ctx,view,'03:17:31')",context);
assert.equal(tags.find(tag=>tag.text==='17.40').subtitle,'03:17:31');
assert.equal(tags.find(tag=>tag.text==='17.32').subtitle,'');
assert.ok(tags[1].y-tags[0].y>=36,'two-line price tag reserves room beneath it');

context.chartBoot.priceStep = 0.001;
context.activeTimeframeUnit = 'm5';
context.bars = [
  {date:'2026-10-12 11:20', open:2.141, high:2.143, low:2.139, close:2.142},
  {date:'2026-10-12 11:25', open:2.142, high:2.142, low:2.138, close:2.139},
];
const ohlc = () => ['#openValue','#highValue','#lowValue','#closeValue'].map(id => element(id).textContent);
vm.runInContext('updateReadout(bars[0])',context);
assert.deepEqual(ohlc(),['2.141','2.143','2.139','2.142']);
vm.runInContext('updateReadout(bars[1])',context);
assert.deepEqual(ohlc(),['2.142','2.142','2.138','2.139']);
assert.ok([...elements.values()].some(node => node.textContent === '-0.003 (-0.14%)'));

console.log(JSON.stringify({ok:true, historicalAndLatestReadouts:true, axisLabelsSeparated:true, etfOhlc:true, countdown:true}));
