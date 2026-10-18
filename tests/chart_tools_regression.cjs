/* Real renderer checks: channel geometry, tool settings, price range, blind EMA. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../docs/prototypes/tv-workspace/prototype-interactions.js'),'utf8');
function extract(name){const start=source.indexOf(`function ${name}(`);assert.ok(start>=0,name);return source.slice(start,source.indexOf('\n}',start)+2);}
let current=[],paths=[],labels=[],handles=[],rectangles=[];
const ctx=new Proxy({
  beginPath(){current=[];},moveTo(x,y){current.push([x,y]);},lineTo(x,y){current.push([x,y]);},
  stroke(){paths.push({points:structuredClone(current),color:ctx.strokeStyle});},
  strokeRect(...args){rectangles.push(args);},
  fillText(text){labels.push(text);},measureText(text){return {width:String(text).length*6};},
},{get(target,key){return key in target?target[key]:(()=>{});}});
const elements=new Map();
function element(key){if(!elements.has(key))elements.set(key,{style:{},classList:{toggle(){}},textContent:''});return elements.get(key);}
const view={left:0,width:100,right:0,top:0,bottom:0,height:100,y:v=>v,xGlobal:i=>i};
const context=vm.createContext({structuredClone,console,window:{},ctx,testView:view,selectedId:null,hitRegions:[],
  onePointTools:new Set(['ray','up','down','text']),canvas:{_view:view},
  registerRect(){},registerSegment(){},registerHandle:(id,handle,x,y)=>handles.push({handle,x,y}),
  indicatorSettings:{ema_length:2,ema_source:'close'},ema:[],activeTimeframeUnit:'daily',
  replayIndex:2,replayActive:false,replayCutoffDate:null,chartBoot:{production:false,priceStep:.01},
  bars:[{date:'2026-10-12',open:39,high:41,low:38,close:40},
    {date:'2026-10-13',open:40,high:42,low:39,close:41},
    {date:'2026-10-14',open:41,high:10000,low:40,close:9999}],
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/prototypes/tv-workspace/chart-indicators.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/prototypes/tv-workspace/chart-position.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/prototypes/tv-workspace/drawing-settings.js'),'utf8'),context);
context.window={};
vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/prototypes/tv-workspace/chart-coordinates.js'),'utf8'),context);
context.chartCoordinates=context.window.AplusChartCoordinates;
context.chartCoordinates.configure({daily:['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16']});
Object.assign(context,{magnetEnabled:true,canvas:{_view:{...view,begin:0,candleWidth:10,high:50,low:0,plotHeight:100},
  getBoundingClientRect:()=>({left:0,top:0})}});
vm.runInContext(['clamp','eventPoint','normalizeDrawingPrice','snapDrawingPoint'].map(extract).join('\n'),context);
const blank=context.eventPoint({clientX:45,clientY:50});
assert.equal(blank.index,4,'the mouse coordinate must retain the blank future position');
assert.equal(context.snapDrawingPoint(blank).index,4,'future placement cannot magnetize to the last real candle');
context.replayActive=true;context.replayIndex=0;
const hidden=context.snapDrawingPoint({index:1,value:40.42,y:41});
assert.equal(hidden.value,40.42,'hidden future OHLC cannot participate in magnetic snapping');
assert.equal(hidden.index,1);
context.replayActive=false;context.replayIndex=2;context.canvas._view=view;
vm.runInContext(extract('moveObjectFromOriginal'),context);
const originalMove={type:'trend',a:{date:'2026-10-12',index:0,value:40.123456},
  b:{date:'2026-10-13',index:1,value:41.123456}};
const moved=structuredClone(originalMove);
context.moveObjectFromOriginal(moved,originalMove,{index:0,value:40},{index:0,value:40.127},null);
assert.ok(Math.abs(moved.a.value-40.253456)<1e-10,'vertical movement uses whole ticks without rounding the original price');
assert.equal(moved.a.date,originalMove.a.date);
assert.equal(moved.b.date,originalMove.b.date);
assert.ok(Math.abs((moved.b.value-moved.a.value)-1)<1e-10,'whole-object movement preserves its structure');
// Each supported tool keeps every structural anchor when the whole drawing is
// moved; editing an endpoint must leave all other anchors and the source intact.
context.magnetEnabled=false;
for(const type of ['fib','long','ray','trend','short','rectangle','up','down','arrow','path','channel']){
  const one=['ray','up','down'].includes(type);
  const drawing={id:type,type,a:{date:'2026-10-12',index:0,value:40},
    b:{date:one?'2026-10-12':'2026-10-13',index:one?0:1,value:one?40:42}};
  if(type==='channel')drawing.c={date:'2026-10-12',index:0,value:43};
  if(type==='path')drawing.points=[drawing.a,{date:'2026-10-12',index:0,value:41},drawing.b];
  if(['long','short'].includes(type))drawing.stop=type==='long'?39:43;
  const before=JSON.stringify(drawing), shifted=structuredClone(drawing);
  context.moveObjectFromOriginal(shifted,drawing,{index:0,value:40},{index:1,value:40.25},null);
  for(const key of ['a','b','c'])if(drawing[key]){
    assert.equal(shifted[key].index,drawing[key].index+1,type);
    assert.equal(shifted[key].value,drawing[key].value+.25,type);
  }
  if(drawing.points)assert.deepEqual(shifted.points.map(p=>[p.index,p.value]),[[1,40.25],[1,41.25],[2,42.25]]);
  if(drawing.stop)assert.equal(shifted.stop,drawing.stop+.25,type);
  const edited=structuredClone(drawing);
  context.moveObjectFromOriginal(edited,drawing,{}, {index:2,value:44.25},'a');
  assert.equal(edited.a.date,'2026-10-14',type);assert.equal(edited.a.value,44.25,type);
  assert.deepEqual(edited.b,drawing.b,type);
  if(drawing.c){context.moveObjectFromOriginal(edited,drawing,{}, {index:2,value:45},'c');assert.equal(edited.c.value,45);assert.deepEqual(edited.b,drawing.b);}
  if(drawing.points){context.moveObjectFromOriginal(edited,drawing,{}, {index:2,value:45},'point:1');assert.equal(edited.points[1].value,45);assert.deepEqual(edited.points[0],drawing.points[0]);}
  if(drawing.stop){context.moveObjectFromOriginal(edited,drawing,{}, {index:2,value:38.5},'stop');assert.equal(edited.stop,38.5);assert.deepEqual(edited.b,drawing.b);}
  if(['long','short'].includes(type)){
    const right=structuredClone(drawing);
    context.moveObjectFromOriginal(right,drawing,{}, {index:2,value:44.25},'right');
    assert.equal(right.b.date,'2026-10-14');assert.equal(right.b.value,drawing.b.value);
    assert.deepEqual(right.a,drawing.a);assert.equal(right.stop,drawing.stop);
    const target=structuredClone(drawing);
    context.moveObjectFromOriginal(target,drawing,{}, {index:2,value:44.25},'target');
    assert.equal(target.b.value,44.25);assert.equal(target.b.date,drawing.b.date);
    assert.deepEqual(target.a,drawing.a);assert.equal(target.stop,drawing.stop);
  }
  if(type==='rectangle')for(const handle of ['rect-ab','rect-ba','rect-a-x','rect-b-x','rect-a-y','rect-b-y']){
    const corner=structuredClone(drawing);
    context.moveObjectFromOriginal(corner,drawing,{}, {index:2,value:44.25},handle);
    if(handle==='rect-ab'){
      assert.equal(corner.a.date,'2026-10-14');assert.equal(corner.a.value,40);
      assert.equal(corner.b.date,'2026-10-13');assert.equal(corner.b.value,44.25);
    }else if(handle==='rect-ba'){
      assert.equal(corner.b.date,'2026-10-14');assert.equal(corner.b.value,42);
      assert.equal(corner.a.date,'2026-10-12');assert.equal(corner.a.value,44.25);
    }else{
      const [,key,axis]=handle.split('-'),other=key==='a'?'b':'a';
      assert.deepEqual(corner[other],drawing[other]);
      assert.equal(corner[key].date,axis==='x'?'2026-10-14':drawing[key].date);
      assert.equal(corner[key].value,axis==='y'?44.25:drawing[key].value);
    }
  }
  assert.equal(JSON.stringify(drawing),before,`${type}: the gesture cannot mutate its undo snapshot`);
}
context.magnetEnabled=true;
// A hidden drawing (including one excluded by replay or timeframe) must not
// leave its quick-settings toolbar on the chart. Use the renderer's visibility
// predicate so the graphic and its editing controls share the same boundary.
Object.assign(context,{drawingsVisible:true,objectVisibleAtTimeframe:()=>true});
vm.runInContext(extract('drawingVisibleInWorkspace'),context);
const visibilityObject={createdAtDate:'2026-10-13',visible:true};
assert.equal(context.drawingVisibleInWorkspace(visibilityObject),true);
context.drawingsVisible=false;
assert.equal(context.drawingVisibleInWorkspace(visibilityObject),false);
context.drawingsVisible=true;
assert.equal(context.drawingVisibleInWorkspace({...visibilityObject,visible:false}),false);
context.objectVisibleAtTimeframe=()=>false;
assert.equal(context.drawingVisibleInWorkspace(visibilityObject),false);
context.objectVisibleAtTimeframe=()=>true;context.replayActive=true;context.replayCutoffDate='2026-10-12';
assert.equal(context.drawingVisibleInWorkspace(visibilityObject),false);
context.replayCutoffDate='2026-10-13';
assert.equal(context.drawingVisibleInWorkspace(visibilityObject),true);
context.replayActive=false;context.replayCutoffDate=null;
assert.match(extract('renderWorkspace'),/classList\.toggle\('visible', visibleObjects\.some/);
// Quick settings must stay out of the price plot even when a selected point
// moves through the previous click position; it must not steal the next drag.
const selectionElements=new Map();
context.userObjects=[{id:'movable',type:'ray',color:'#fff',width:2}];
context.wrap={clientWidth:1000,clientHeight:600};
context.document={querySelector:key=>{if(!selectionElements.has(key))selectionElements.set(key,
  {style:{},classList:{add(){},remove(){}},textContent:''});return selectionElements.get(key);}};
context.updateObjectTree=()=>{};context.renderWorkspace=()=>{};
vm.runInContext(extract('selectObject'),context);
context.selectObject('movable',900,200);
const quickBar=selectionElements.get('#selectedObject');
assert.ok(Number.parseFloat(quickBar.style.top)+36<42,'quick controls must remain above the first plotted price');
const fixedToolbar=JSON.stringify(quickBar.style);
context.selectObject('movable',900,100);
assert.equal(JSON.stringify(quickBar.style),fixedToolbar,'moving the selected anchor cannot move editing controls into its path');
const positionMetrics=context.AplusPositionMetrics.metrics;
const model={type:'long',a:{index:0,value:10},b:{index:3,value:14},stop:8,
  account_size:5000,risk_value:10,risk_unit:'percent',lot_size:1,leverage:1};
let result=positionMetrics(model,[{open:10,high:11,low:9,close:10.5}]);
assert.equal(result.quantity,250);assert.equal(result.ratio,2);assert.equal(result.targetAmount,6000);
assert.equal(result.stopAmount,4500);assert.equal(result.pnl,.5);
assert.equal(positionMetrics({...model,leverage:.1},[]).quantity,50);
assert.equal(positionMetrics({...model,lot_size:10},[]).quantity,25);
const short=positionMetrics({...model,type:'short',b:{index:3,value:6},stop:12},[]);
assert.equal(short.quantity,250);assert.equal(short.ratio,2);assert.equal(short.targetAmount,6000);
result=positionMetrics(model,[{high:11,low:9,close:10.5},{high:15,low:7,close:12}],0);
assert.equal(result.status,'open');assert.equal(result.pnl,.5,'future bars cannot change replay PnL');
assert.equal(positionMetrics(model,[{high:15,low:7,close:12}]).status,'ambiguous');
const indicators=context.AplusChartIndicators;
assert.equal(indicators.source({open:8,high:14,low:6,close:12},'hl2'),10);
assert.equal(indicators.source({open:8,high:14,low:6,close:12},'hlc3'),32/3);
assert.equal(indicators.source({open:8,high:14,low:6,close:12},'ohlc4'),10);
assert.deepEqual(Array.from(indicators.average([10,20,30],3)),[10,15,22.5]);
const smoothRows=[{volume:1},{volume:2},{volume:3},{volume:4}];
assert.deepEqual(Array.from(indicators.smooth([10,20,30,40],smoothRows,'sma',3).values),[null,null,20,30]);
assert.deepEqual(Array.from(indicators.smooth([10,20,30,40],smoothRows,'rma',3).values),[null,null,20,80/3]);
assert.equal(indicators.smooth([10,20,30,40],smoothRows,'wma',3).values[2],140/6);
assert.equal(indicators.smooth([10,20,30,40],smoothRows,'vwma',3).values[3],290/9);
const bands=indicators.smooth([10,20,30],smoothRows,'sma_bb',3,2);
assert.ok(Math.abs(bands.upper[2]-(20+2*Math.sqrt(200/3)))<1e-12);
assert.ok(Math.abs(bands.lower[2]-(20-2*Math.sqrt(200/3)))<1e-12);
// Higher-timeframe EMA may appear only when its period is known, never on Monday
// using a Friday price. A developing value uses only the exposed daily prefix.
const mtfDaily=['2026-01-05','2026-01-06','2026-01-07','2026-01-08','2026-01-09','2026-01-12'].map((date,n)=>
  ({date,open:(n+1)*10,high:(n+1)*10+1,low:(n+1)*10-1,close:(n+1)*10,volume:100}));
const mtfWeekly=indicators.aggregate(mtfDaily,'weekly');
const mtfValues=indicators.average(mtfWeekly.map(row=>row.close),2);
const mtfCloses={weekly:{'2026-01-09':'2026-01-09','2026-01-16':'2026-01-16'}};
const mtfClosed=indicators.mergeTimeframe(mtfValues,mtfWeekly,mtfDaily,'weekly','daily',mtfCloses,'2026-01-12',true);
assert.deepEqual(Array.from(mtfClosed),[null,null,null,null,50,null]);
const mtfForming=indicators.mergeTimeframe(mtfValues,mtfWeekly,mtfDaily,'weekly','daily',mtfCloses,'2026-01-12',false);
assert.ok(Math.abs(mtfForming[5]-170/3)<1e-12);
const prefix=mtfDaily.slice(0,3),prefixWeekly=indicators.aggregate(prefix,'weekly');
const blindMtf=indicators.mergeTimeframe(indicators.average(prefixWeekly.map(row=>row.close),2),prefixWeekly,prefix,
  'weekly','daily',mtfCloses,'2026-01-07',false);
assert.deepEqual(Array.from(blindMtf),[null,null,30]);
vm.runInContext(['dashPattern','drawUserObject','drawSelectionHandles','arrowMarkerPoints','drawArrowHead','calculateEma','clamp','visibleWindow','objectVisibleAtTimeframe','timeAxisTicks'].map(extract).join('\n'),context);
context.channel={id:'channel',type:'channel',a:{index:0,value:10},b:{index:10,value:20},c:{index:5,value:22},color:'#fff',width:2,fill:false};
vm.runInContext('drawUserObject(ctx,testView,channel)',context);
assert.deepEqual(paths.slice(0,3).map(p=>p.points),[[[0,10],[10,20]],[[0,13.5],[10,23.5]],[[0,17],[10,27]]]);
paths=[];context.channel.c.value=25;context.selectedId='channel';
vm.runInContext('drawUserObject(ctx,testView,channel)',context);
assert.deepEqual(paths[2].points,[[0,20],[10,30]]);
assert.ok(handles.some(h=>h.handle==='c'&&h.x===5&&h.y===25));
handles=[];
context.drawSelectionHandles(ctx,{id:'rect-handles',type:'rectangle'},10,20,30,40);
assert.equal(handles.length,8,'rectangle must expose four corners and four edge controls');
handles=[];
context.drawSelectionHandles(ctx,{id:'risk-handles',type:'long',stop:5},10,20,30,40);
assert.deepEqual(handles.map(h=>h.handle),['a','right','target','stop']);
assert.deepEqual(handles.find(h=>h.handle==='right'),{handle:'right',x:30,y:20});
assert.deepEqual(handles.find(h=>h.handle==='target'),{handle:'target',x:10,y:40});
context.selectedId=null;
context.pathDrawing={id:'path',type:'path',a:{index:0,value:10},b:{index:10,value:15},
  points:[{index:0,value:10},{index:5,value:20},{index:10,value:15}],color:'#fff'};
paths=[];vm.runInContext('drawUserObject(ctx,testView,pathDrawing)',context);
assert.equal(paths.length,1,'legacy polylines cannot silently gain an arrow');
context.pathDrawing.path_version=1;
paths=[];vm.runInContext('drawUserObject(ctx,testView,pathDrawing)',context);
assert.equal(paths.length,2,'new Path drawings end in an arrow');
assert.deepEqual(paths[1].points[0],[10,15]);
context.pathDrawing.startStyle='arrow';context.pathDrawing.endStyle='none';
paths=[];vm.runInContext('drawUserObject(ctx,testView,pathDrawing)',context);
assert.equal(paths.length,2);assert.deepEqual(paths[1].points[0],[0,10]);
const pathFinish=vm.createContext({structuredClone,Math,Date,activeDrawingTool:'path',
  draftPoints:[{index:0,value:10},{index:1,value:11}],draftCursor:{index:2,value:999},
  bars:[{date:'2026-10-12'},{date:'2026-10-13'}],replayActive:false,replayIndex:1,
  userObjects:[],drawingTemplates:{path:{closed:true,fill:true}},
  rememberObjects(){},savePrototypeState(){},updateObjectTree(){},selectObject(){},
  exitDrawingMode(){},showToast(){},renderWorkspace(){},snapDrawingPoint:p=>p});
vm.runInContext(extract('finishPath'),pathFinish);
assert.equal(pathFinish.finishPath(),true,'Escape completes the placed path vertices');
assert.equal(pathFinish.userObjects[0].points.length,2,'Escape cannot add the unplaced hover point');
assert.equal(pathFinish.userObjects[0].endStyle,'arrow');
assert.equal(pathFinish.userObjects[0].fill,undefined,'a legacy polygon template cannot turn new Path drawings into polygons');
assert.equal(pathFinish.userObjects[0].closed,undefined);
// TV risk tools are placed with one click, with independent right/target/stop
// handles afterwards. New marker arrows differ from saved legacy line arrows.
const creation=vm.createContext({structuredClone,Math,Date,activeDrawingTool:null,
  draftAnchor:null,draftCursor:null,draftPoints:[],pendingDrawingText:null,
  onePointTools:new Set(['ray','up','down','text']),
  bars:[{date:'2026-10-12'}],replayActive:false,replayIndex:0,activeTimeframeUnit:'daily',
  chartBoot:{priceStep:.01},chartCoordinates:{bounds:()=>({min:0,max:100})},
  canvas:{_view:{high:30,low:10,plotHeight:400,candleWidth:5}},
  userObjects:[],drawingTemplates:{arrow:{arrow_marker_version:0}},
  rememberObjects(){},savePrototypeState(){},updateObjectTree(){},selectObject(){},
  exitDrawingMode(){this.activeDrawingTool=null;},showToast(){},renderWorkspace(){},
  toolNames:{long:'多头仓位',short:'空头仓位',arrow:'箭头标记',up:'向上箭头',down:'向下箭头'},
  snapDrawingPoint:p=>structuredClone(p)});
vm.runInContext(['clamp','normalizeDrawingPrice','placeDrawing'].map(extract).join('\n'),creation);
for(const type of ['long','short','up','down']){
  creation.activeDrawingTool=type;creation.draftAnchor=null;
  const before=creation.userObjects.length;
  creation.placeDrawing({index:8,value:20});
  assert.equal(creation.userObjects.length,before+1,`${type} needs only one click`);
  const object=creation.userObjects.at(-1);
  if(['long','short'].includes(type)){
    assert.equal(object.b.index,38);
    assert.equal(Math.abs(object.b.value-object.a.value),Math.abs(object.stop-object.a.value));
    assert.equal(type==='long'?object.b.value>20:object.b.value<20,true);
  }else assert.equal(object.fontSize,14);
}
creation.activeDrawingTool='arrow';creation.draftAnchor=null;
const beforeMarker=creation.userObjects.length;
creation.placeDrawing({index:4,value:19});assert.equal(creation.userObjects.length,beforeMarker);
creation.placeDrawing({index:12,value:23});
assert.equal(creation.userObjects.at(-1).arrow_marker_version,1);
assert.equal(creation.userObjects.at(-1).fontSize,16);
assert.equal(creation.userObjects.at(-1).color,'#f23645');
creation.canvas._view.plotHeight=428; // pixel distance is not an exact tick
creation.activeDrawingTool='long';creation.draftAnchor=null;creation.placeDrawing({index:8,value:5.21});
const tickRisk=creation.userObjects.at(-1);
assert.equal(Math.round((tickRisk.b.value-5.21)*100),Math.round((5.21-tickRisk.stop)*100));
const beforeInvalid=creation.userObjects.length;
creation.activeDrawingTool='short';creation.draftAnchor=null;creation.placeDrawing({index:8,value:.01});
assert.equal(creation.userObjects.length,beforeInvalid,'an impossible positive-price risk box cannot be persisted');
const markerPolygon=context.arrowMarkerPoints(0,0,200,0);
assert.equal(markerPolygon.length,6);assert.deepEqual(Array.from(markerPolygon[0]),[0,0]);
assert.deepEqual(Array.from(markerPolygon[3]),[200,0]);
assert.ok(markerPolygon.some(point=>point[1]>20)&&markerPolygon.some(point=>point[1]<-20));
assert.ok(context.arrowMarkerPoints(10,10,10,10).flat().every(Number.isFinite));
// Bulk removal protects locked analysis by default; explicit inclusion is a
// persisted preference, and the whole mutation keeps one undo snapshot.
const removalHistory=[];
const removal=vm.createContext({structuredClone,userObjects:[{id:'free'},{id:'locked',locked:true}],
  indicatorSettings:{ema:true,lock_drawings:false},selectedId:'free',
  document:{querySelector:()=>({classList:{remove(){}}})},
  rememberObjects(){removalHistory.push(structuredClone(removal.userObjects));},
  savePrototypeState(){},updateObjectTree(){},renderWorkspace(){}});
vm.runInContext(extract('removeChartObjects'),removal);
removal.removeChartObjects('drawings');
assert.deepEqual(removal.userObjects.map(o=>o.id),['locked']);assert.equal(removal.indicatorSettings.ema,true);
assert.equal(removalHistory[0].length,2);
removal.indicatorSettings.remove_locked=true;removal.removeChartObjects('all');
assert.equal(removal.userObjects.length,0);assert.equal(removal.indicatorSettings.ema,false);
removal.userObjects=[{id:'free'}];removal.indicatorSettings={ema:true,lock_drawings:true};
removal.removeChartObjects('all');assert.equal(removal.userObjects.length,1);
assert.equal(removal.indicatorSettings.ema,false);
// Rectangle extension is independent of the direction in which its anchors
// were drawn. Fill, border and midpoint use the same geometric boundaries.
context.selectedId=null;
for(const [extendLeft,extendRight,left,right] of [[false,false,20,80],[true,false,0,80],[false,true,20,100],[true,true,0,100]]){
  context.extendedRectangle={id:'extended',type:'rectangle',a:{index:80,value:20},b:{index:20,value:40},
    color:'#fff',fill:false,showMiddle:true,extendLeft,extendRight};
  rectangles=[];paths=[];
  vm.runInContext('drawUserObject(ctx,testView,extendedRectangle)',context);
  assert.deepEqual(rectangles,[[left,20,right-left,20]]);
  assert.deepEqual(paths.at(-1).points,[[left,30],[right,30]]);
  assert.equal(context.extendedRectangle.a.index,80,'extension must not rewrite canonical anchors');
}
context.channel.showMiddle=false;context.selectedId=null;paths=[];
vm.runInContext('drawUserObject(ctx,testView,channel)',context);assert.equal(paths.length,2);
context.channel.channelLevels=[{value:-.25,enabled:true,color:'#ff9800',width:3,dash:'dashed'},
  {value:0,enabled:true},{value:.25,enabled:false},{value:1,enabled:true},{value:1.25,enabled:true}];
paths=[];vm.runInContext('drawUserObject(ctx,testView,channel)',context);
assert.deepEqual(paths.map(p=>p.points),[[[0,7.5],[10,17.5]],[[0,10],[10,20]],[[0,20],[10,30]],[[0,22.5],[10,32.5]]]);
assert.equal(paths[0].color,'#ff9800');
vm.runInContext(extract('registerPolygon'),context);
context.channel.fill=true;context.hitRegions=[];
vm.runInContext('drawUserObject(ctx,testView,channel)',context);
assert.ok(context.hitRegions.some(region=>region.id==='channel'&&region.hit(5,20)), 'the shaded channel interior must select its own drawing');
assert.ok(!context.hitRegions.some(region=>region.hit(5,5)));
context.position={id:'position',type:'long',a:{index:0,value:10},b:{index:10,value:14},stop:8,risk_budget:1000,color:'#fff'};
vm.runInContext('drawUserObject(ctx,testView,position)',context);
assert.ok(labels.some(s=>s.includes('2.00'))&&labels.some(s=>s.includes('500')));
assert.ok(labels.some(s=>s.includes('非交易建议')));
context.ensureFibSettings=object=>object;
context.fib={id:'fib-text',type:'fib',a:{index:0,value:10},b:{index:10,value:14},color:'#4caf50',
  fibText:false,fibLevels:[{value:0,enabled:true,color:'#4caf50'},{value:.5,enabled:true,color:'#4caf50',text:'支撑区'},{value:1,enabled:true,color:'#4caf50'}]};
labels=[];vm.runInContext('drawUserObject(ctx,testView,fib)',context);
assert.ok(labels.some(text=>text.includes('0.5')&&text.includes('12.00')),'level/price labels are independent from custom text visibility');
assert.ok(!labels.includes('支撑区'));
context.fib.fibText=true;labels=[];vm.runInContext('drawUserObject(ctx,testView,fib)',context);
assert.ok(labels.includes('支撑区'));
assert.equal(vm.runInContext("objectVisibleAtTimeframe({type:'trend',visibility:{daily:false}})",context),false);
assert.equal(vm.runInContext("objectVisibleAtTimeframe({type:'fib',fibVisibility:{days:{enabled:true,min:2,max:7}}})",context),false);
assert.equal(vm.runInContext("objectVisibleAtTimeframe({type:'fib',fibVisibility:{days:{enabled:true,min:1,max:7}}})",context),true);
Object.assign(context,{wrap:{getBoundingClientRect:()=>({width:1000,height:600})},window:{devicePixelRatio:1},
  document:{querySelector:element},css:()=> '#0b0b0b',viewCount:2,viewOffset:0,replayIndex:1,replayActive:true,
  priceZoom:1,priceCenterOffset:0,RIGHT_MARGIN_BARS:12,userObjects:[],drawingsVisible:true,draftAnchor:null,
  chartBoot:{production:true},chartAlerts:[],xFor:(i,left,width)=>left+(i+.5)*width,
  drawStrategyEvidence(){},drawReplayBoundary(){},drawLastPrice(){},priceTag(){},
});
context.canvas.getContext=()=>ctx;
Object.assign(context.indicatorSettings,{ema:true,ema_daily:true,ema_width:1.5,ema_color:'#ff9800',ema_offset:-1});
vm.runInContext(extract('renderWorkspace'),context);paths=[];
vm.runInContext('calculateEma();renderWorkspace()',context);
assert.ok(context.canvas._view.high<50,'production scale must not use prototype or future prices');
assert.ok(context.canvas._view.low>35,'production scale must follow this symbol');
const averagePath=paths.find(p=>p.color==='#ff9800');
assert.equal(averagePath.points.length,1,'negative offset cannot reveal the next future EMA');
assert.equal(element('.ema-value').textContent,'—');
assert.ok(Number.parseFloat(element('#chartNavControls').style.bottom)-8>=context.canvas._view.bottom,
  'navigation including its animation must not cover the time-axis drag area');
context.wrap.getBoundingClientRect=()=>({width:1000,height:240});
vm.runInContext('renderWorkspace()',context);
assert.ok(Number.parseFloat(element('#chartNavControls').style.bottom)-8>=context.canvas._view.bottom,
  'compact grid slots must also keep navigation clear of the time axis');
context.wrap.getBoundingClientRect=()=>({width:1000,height:600});
vm.runInContext('renderWorkspace()',context);
context.indicatorSettings.chart_type='line';paths=[];
vm.runInContext('renderWorkspace()',context);
const closeLine=paths.find(p=>p.color==='#2962ff');
assert.equal(closeLine.points.length,2,'line view must show only the two exposed closes');
assert.ok(closeLine.points.every(point=>point[1]>=context.canvas._view.top),
  'a hidden future close cannot enter the displayed line');
context.indicatorSettings.chart_type='candles';
const axisContext=vm.createContext({Math,Number,String,viewCount:40,RIGHT_MARGIN_BARS:12,
  activeTimeframeUnit:'daily',bars:[],chartCoordinates:context.chartCoordinates});
axisContext.bars=Array.from({length:240},(_,i)=>({date:new Date(Date.UTC(2022,0,3+i)).toISOString().slice(0,10)}));
vm.runInContext(extract('timeAxisTicks'),axisContext);
const wideAxis={begin:0,candleWidth:24,width:1000,right:60,xGlobal:i=>(i+.5)*24};
const dayTicks=axisContext.timeAxisTicks(wideAxis);
assert.ok(dayTicks.some(tick=>/^\d{1,2}$/.test(tick.label)),'zoomed daily views must show day numbers');
axisContext.activeTimeframeUnit='weekly';axisContext.viewCount=204;
axisContext.bars=Array.from({length:240},(_,i)=>({date:new Date(Date.UTC(2022,0,3+i*7)).toISOString().slice(0,10)}));
const weeklyTicks=axisContext.timeAxisTicks({...wideAxis,candleWidth:4,xGlobal:i=>(i+.5)*4});
assert.ok(weeklyTicks.some(tick=>/^20\d{2}$/.test(tick.label)),'multi-year views need year labels');
assert.ok(weeklyTicks.every((tick,i)=>!i||tick.x-weeklyTicks[i-1].x>=60),'month labels cannot collide in weekly views');
axisContext.activeTimeframeUnit='monthly';
axisContext.bars=Array.from({length:240},(_,i)=>({date:new Date(Date.UTC(2010, i+1, 0)).toISOString().slice(0,10)}));
const monthTicks=axisContext.timeAxisTicks({...wideAxis,candleWidth:4,xGlobal:i=>(i+.5)*4});
assert.ok(monthTicks.every(tick=>/^20\d{2}$/.test(tick.label)),'compressed monthly views use readable year marks');
const objectDrawOrder=[],realDrawObject=context.drawUserObject;
context.drawUserObject=(_ctx,_view,object)=>objectDrawOrder.push(object.id);
context.userObjects=[{id:'chosen',type:'trend',a:{index:0,value:39},b:{index:1,value:41},createdAtIndex:0},
  {id:'cover',type:'rectangle',a:{index:0,value:39},b:{index:1,value:41},createdAtIndex:0}];
context.selectedId='chosen';vm.runInContext('renderWorkspace()',context);
assert.deepEqual(objectDrawOrder,['cover','chosen'],'selected structure handles must remain above overlapping drawings');
context.userObjects=[];context.selectedId=null;context.drawUserObject=realDrawObject;
// Cursor zoom includes the blank future margin in its coordinate equation.
context.bars=Array.from({length:300},(_,index)=>({date:new Date(Date.UTC(2025,0,index+1)).toISOString().slice(0,10),
  open:39,high:41,low:38,close:40,volume:100}));
Object.assign(context,{replayIndex:299,replayActive:false,viewCount:204,viewOffset:0,savePrototypeState(){}});
vm.runInContext(['zoomAround','panChart','panLimits'].map(extract).join('\n'),context);
vm.runInContext('calculateEma();renderWorkspace()',context);
for(const ratio of [.2,.5,.8]){
  const before=context.canvas._view;
  const anchor=before.begin+ratio*(context.viewCount+context.RIGHT_MARGIN_BARS)-.5;
  vm.runInContext(`zoomAround(.75,${ratio})`,context);
  const after=context.canvas._view;
  const projected=after.begin+ratio*(context.viewCount+context.RIGHT_MARGIN_BARS)-.5;
  assert.ok(Math.abs(projected-anchor)<.6,'zoom cannot lose the hovered bar through right-margin drift');
}
Object.assign(context,{replayIndex:49,replayActive:true,viewCount:18,viewOffset:0});
vm.runInContext('renderWorkspace();panChart(5)',context);
assert.equal(context.canvas._view.end,50);assert.equal(context.canvas._view.begin,27);
// Panning consumes blank space, not candle width, and can expose future time
// labels without adding market prices or revealing a replay's hidden candles.
context.chartCoordinates.configure({daily:Array.from({length:350},(_,n)=>new Date(Date.UTC(2025,0,n+1)).toISOString().slice(0,10))});
Object.assign(context,{replayIndex:299,replayActive:false,viewCount:204,viewOffset:0});
vm.runInContext('renderWorkspace()',context);
const panWidth=context.canvas._view.candleWidth,oldLastX=context.canvas._view.xGlobal(299);
vm.runInContext('panChart(-20)',context);
assert.equal(context.viewOffset,-20);assert.equal(context.canvas._view.candleWidth,panWidth);
assert.ok(Math.abs(context.canvas._view.xGlobal(299)-(oldLastX-20*panWidth))<1e-8);
assert.equal(context.bars.length,300,'future time space cannot create synthetic candles');
vm.runInContext('panChart(44)',context);
assert.equal(context.canvas._view.candleWidth,panWidth);
assert.equal(context.canvas._view.end,288);
Object.assign(context,{replayIndex:49,replayActive:true,viewCount:18,viewOffset:0});
vm.runInContext('renderWorkspace();panChart(-10)',context);
assert.ok(context.canvas._view.rows.every(row=>row.date<=context.bars[49].date));
// Hiding a layer is reversible and cannot disable the user's EMA configuration.
Object.assign(context,{rememberObjects(){},syncWorkspaceControls(){},savePrototypeState(){},
  renderWorkspace(){},updateObjectTree(){}});
vm.runInContext(extract('toggleChartVisibility'),context);
vm.runInContext("toggleChartVisibility('indicators')",context);
assert.equal(context.indicatorSettings.ema,true);
assert.equal(context.indicatorSettings.indicators_visible,false);
vm.runInContext("toggleChartVisibility('indicators')",context);
assert.equal(context.indicatorSettings.indicators_visible,true);
vm.runInContext("toggleChartVisibility('all')",context);
assert.equal(context.drawingsVisible,false);assert.equal(context.indicatorSettings.strategy_visible,false);
vm.runInContext("toggleChartVisibility('all')",context);
assert.equal(context.drawingsVisible,true);assert.equal(context.indicatorSettings.strategy_visible,true);
// Real wheel dispatch: ordinary zoom is right anchored; Ctrl focuses the cursor.
let wheelHandler,zoomCall,panCall;
const wheelContext=vm.createContext({Math,viewCount:200,clamp:(n,a,b)=>Math.max(a,Math.min(b,n)),
  zoomAround:(factor,ratio)=>{zoomCall={factor,ratio};},panChart:n=>{panCall=n;},
  canvas:{_view:{left:8,plotWidth:1000},getBoundingClientRect:()=>({left:20}),
    addEventListener:(_name,handler)=>{wheelHandler=handler;}}});
const wheelStart=source.indexOf("canvas.addEventListener('wheel', event => {");
const wheelEnd=source.indexOf('}, { passive: false });',wheelStart)+'}, { passive: false });'.length;
vm.runInContext(source.slice(wheelStart,wheelEnd),wheelContext);
const wheel={preventDefault(){},clientX:228,deltaY:-120};
wheelHandler(wheel);assert.equal(zoomCall.ratio,1);assert.ok(zoomCall.factor<1);
wheelHandler({...wheel,ctrlKey:true});assert.equal(zoomCall.ratio,.2);
wheelHandler({...wheel,shiftKey:true});assert.equal(panCall,-16);
// A persisted 1.0 alpha must open as 100%, not as 1%.
let alphaChange;
const alphaControl={type:'range',dataset:{setting:'alpha'},value:'100',
  addEventListener:(_event,handler)=>{alphaChange=handler;}};
const alphaHost={querySelectorAll:selector=>selector==='[data-setting]'?[alphaControl]:[]};
const alphaObject={alpha:1};context.bindDrawingStyle(alphaHost,alphaObject);
assert.equal(Number(alphaControl.value),100);
alphaControl.value='32';alphaChange();assert.equal(alphaObject.alpha,.32);
// Export carries the replay cutoff (not the future Friday label), and narrow
// chart slots put OHLC on a separate row instead of clipping it behind a title.
context.document={querySelector:element,createElement:()=>({getContext:()=>ctx})};
context.canvas={width:600,height:400};context.window={devicePixelRatio:1};
context.selectedId='keep-selection';context.renderWorkspace=()=>{};
context.replayActive=true;context.replayCutoffDate='2026-08-12';
context.chartBoot={source:'test-source',adjustment:'qfq'};
element('.instrument-title b').textContent='Test instrument';
element('.ohlc').textContent='OHLC';
context.indicatorSettings.ema=false;labels=[];
vm.runInContext(extract('cleanSnapshotCanvas'),context);
const exported=vm.runInContext('cleanSnapshotCanvas()',context);
assert.equal(exported.height,468);
assert.ok(labels.includes('回放截至 2026-08-12 · test-source · qfq'));
assert.equal(context.selectedId,'keep-selection','export cannot clear the selected drawing');
console.log(JSON.stringify({ok:true}));
