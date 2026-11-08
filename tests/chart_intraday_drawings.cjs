/* Production drawing creation, calendar projection and immutable save snapshots. */
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const assets=path.resolve(__dirname,'../docs/prototypes/tv-workspace');
const source=fs.readFileSync(path.join(assets,'prototype-interactions.js'),'utf8');
function extract(name){const at=source.indexOf(`function ${name}(`);assert.ok(at>=0,name);return source.slice(at,source.indexOf('\n}',at)+2);}
const days=['2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-11-02','2026-11-03'];
const row=date=>({date,open:10,high:11,low:9,close:10,volume:100});
function minutes(day,span){const out=[];for(let n=span;n<=240;n+=span){const t=n<=120?570+n:780+n-120;out.push(row(`${day} ${String(Math.floor(t/60)).padStart(2,'0')}:${String(t%60).padStart(2,'0')}:00`));}return out;}
const series={daily:days.slice(0,10).map(row),weekly:['2026-10-23','2026-10-30'].map(row),monthly:['2026-10-31'].map(row)};
for(const [unit,span] of [['m5',5],['m60',60],['m240',120]])series[unit]=days.slice(5,10).flatMap(day=>minutes(day,span));
const originalMinutes=structuredClone(series.m5);
const elements=new Map(),element=key=>{if(!elements.has(key))elements.set(key,{style:{},textContent:'',innerHTML:'',classList:{add(){},remove(){},toggle(){}}});return elements.get(key);};
const states=[],context=vm.createContext({window:{},structuredClone,console,
 document:{querySelector:element,querySelectorAll:()=>[]},localStorage:{setItem(){}},
 chartBoot:{production:true,identity:'isolated',symbol:'sz.159570',name:'synthetic',timeframe:'daily',adjustment:'不复权',priceStep:.001,
   series,axisDates:{daily:days,weekly:series.weekly.map(r=>r.date),monthly:series.monthly.map(r=>r.date)},live:{source:'腾讯',adjustment:'不复权'}},
 bars:structuredClone(series.daily),userObjects:[],undoStack:[],redoStack:[],indicatorUndoStack:[],indicatorRedoStack:[],indicatorSettings:{},ema:[],
 activeTimeframeUnit:'daily',replayActive:false,replayCutoffDate:null,replayIndex:9,viewCount:10,viewOffset:0,priceZoom:1,priceCenterOffset:0,
 DEFAULT_VISIBLE_BARS:204,RIGHT_MARGIN_BARS:12,bridgeRevision:0,bridgeSaveChain:Promise.resolve(),bridgeSaveFailed:false,tvStateKey:'isolated',
 drawingTemplates:{},draftAnchor:null,draftCursor:null,draftPoints:[],pendingDrawingText:null,activeDrawingTool:null,magnetEnabled:false,
 objectSettingsSnapshot:null,objectSettingsHistory:null,drawingsVisible:true,gesture:null,
 onePointTools:new Set(['ray','up','down','text']),toolNames:{},fibLevelDefaults:[[0,true,'#fff'],[.5,true,'#fff'],[1,true,'#fff']],
 fibVisibilityDefaults:{minutes:{enabled:true,min:1,max:59},hours:{enabled:true,min:1,max:24},days:{enabled:true,min:1,max:366},weeks:{enabled:true,min:1,max:52},months:{enabled:true,min:1,max:12}},
 canvas:{_view:{high:12,low:8,plotHeight:500,candleWidth:8},classList:{remove(){}}},
 renderWorkspace(){},updateObjectTree(){},showToast(){},selectObject(){},updateUndoButtons(){},updateReadout(){},chartPrice:String,
 calculateEma(){},refreshSeriesTitle(){},setChartCursor(){}});
vm.runInContext(fs.readFileSync(path.join(assets,'chart-coordinates.js'),'utf8'),context);
context.chartCoordinates=context.window.AplusChartCoordinates;
context.chartCoordinates.configure(context.chartBoot.axisDates);
context.window.pywebview={api:{save_state:async(state,revision)=>{states.push(structuredClone(state));return{revision:revision+1};}}};
vm.runInContext(['clamp','visibleWindow','panLimits','periodLabel','savePrototypeState','rememberObjects','ensureFibSettings',
 'loadFibTemplate','objectVisibleAtTimeframe','drawingVisibleInWorkspace','normalizeDrawingPrice','snapDrawingPoint','exitDrawingMode',
 'placeDrawing','finishPath','moveObjectFromOriginal','switchProductionTimeframe','repaintCurrentSeries'].map(extract).join('\n'),context);
const c=context.chartCoordinates,call=code=>vm.runInContext(code,context);
const signatures=()=>JSON.stringify(context.userObjects.map(o=>({id:o.id,type:o.type,a:[o.a.date,o.a.value],b:[o.b.date,o.b.value],
 c:o.c&&[o.c.date,o.c.value],points:o.points?.map(p=>[p.date,p.value]),stop:o.stop,created:o.createdAtDate})));
async function run(){
 // Dates before the downloaded minute window stay distinct, outside the view.
 const a=c.project({date:days[0],value:10},series.m5,'m5'),b=c.project({date:days[4],value:11},series.m5,'m5');
 assert.equal(a.index,-240);assert.equal(b.index,-48);assert.notEqual(a.index,b.index);
 assert.equal(c.project({date:days[5],value:10},series.m5,'m5').index,0);
 assert.equal(c.project({date:'2026-10-26 13:05:00',value:10},series.m60,'m60').index,2);
 assert.equal(c.project({date:'2026-10-26 10:35:00',value:10},series.m240,'m240').index,0);
 assert.equal(c.dateAt(series.m5,48,'m5'),'2026-10-27 09:35:00');
 assert.equal(c.dateAt(series.m5,240,'m5'),'2026-11-02 09:35:00','verified calendar skips weekend');
 assert.equal(c.dateAt(series.m5,-1,'m5'),'2026-10-23 15:00:00');
 assert.throws(()=>c.dateAt(series.m5,336,'m5'),/交易日历/);
 // A daily channel with both anchors outside minute coverage still has a slope.
 context.userObjects.push({id:'daily-channel',type:'channel',a:{index:0,date:days[0],value:9.5},b:{index:4,date:days[4],value:9.6},
 c:{index:0,date:days[0],value:10},createdAtDate:days[4],createdAtIndex:4,extendRight:true});
 call("switchProductionTimeframe('m5',false)");
 assert.equal(context.userObjects[0].a.index,-240);assert.equal(context.userObjects[0].b.index,-48);
 assert.equal(call('drawingVisibleInWorkspace(userObjects[0])'),true);
 context.originalChannel=structuredClone(context.userObjects[0]);context.movedChannel=structuredClone(context.originalChannel);
 call('moveObjectFromOriginal(movedChannel,originalChannel,{index:1,value:10},{index:1,value:10.1},null)');
 assert.equal(context.movedChannel.a.date,days[0]);assert.equal(context.movedChannel.b.date,days[4]);
 call('moveObjectFromOriginal(movedChannel,originalChannel,{index:1,value:10},{index:2,value:10.1},null)');
 assert.equal(context.movedChannel.a.date,'2026-10-19 09:40:00');
 // Actually create every supported drawing through the production placement code.
 const kinds=['fib','long','ray','trend','short','rectangle','up','down','arrow','path','channel'];
 for(const kind of kinds){context.activeDrawingTool=kind;context.draftAnchor=null;context.draftPoints=[];
   call('placeDrawing({index:210,value:10,x:400,y:200})');
   if(kind==='path'){call('placeDrawing({index:216,value:10.2,x:450,y:190});finishPath()');}
   else if(!['long','short','ray','up','down'].includes(kind)){
     call('placeDrawing({index:222,value:10.4,x:500,y:180})');
     if(kind==='channel')call('placeDrawing({index:213,value:10.6,x:430,y:160})');
   }
   assert.equal(context.userObjects.at(-1).type,kind);
   assert.equal(call('drawingVisibleInWorkspace(userObjects.at(-1))'),true,kind);
 }
 await context.bridgeSaveChain;
 assert.equal(context.bridgeRevision,kinds.length,'rapidly queued edits use acknowledged revisions');
 const original=signatures();
 assert.equal(context.userObjects.length,12);
 assert.equal(context.userObjects[1].a.date,'2026-10-30 11:05:00');
 assert.equal(call("objectVisibleAtTimeframe({type:'trend',visibility:{m5:false}})"),false);
 assert.equal(call("objectVisibleAtTimeframe({type:'fib',fibVisibility:{minutes:{enabled:true,min:6,max:10}}})"),false);
 for(const unit of ['m60','m240','daily','weekly','monthly','m5']){
   call(`switchProductionTimeframe('${unit}')`);await context.bridgeSaveChain;
   assert.equal(signatures(),original,unit+' must not rewrite anchors or prices');
   assert.ok(context.userObjects.every(o=>call(`drawingVisibleInWorkspace(userObjects.find(o=>o.id===${JSON.stringify(o.id)}))`)));
 }
 // A live prefix insertion reprojects both drawings and undo, without resaving analysis.
 const beforeSaved=states.length,undoDates=JSON.stringify(context.undoStack.map(items=>items.map(o=>o.a.date)));
 context.chartBoot.series.m5=minutes('2026-10-23',5).concat(series.m5);
 call('repaintCurrentSeries()');
 assert.equal(signatures(),original);assert.equal(states.length,beforeSaved);
 assert.equal(JSON.stringify(context.undoStack.map(items=>items.map(o=>o.a.date))),undoDates);
 assert.equal(context.userObjects[1].a.index,258); // 210 + 48 older bars
 assert.equal(context.undoStack.at(-1)[1].a.index,258);
 // A quote refresh cannot change the time axis half way through placing a line.
 context.draftAnchor={index:3,value:10};context.chartBoot.series.m5=originalMinutes;
 const held=context.bars.length;call('repaintCurrentSeries()');assert.equal(context.bars.length,held);
 call('exitDrawingMode()');assert.equal(context.bars.length,240,'completion applies cached final refresh without another poll');
 // Switch cancels an incomplete drawing instead of combining two different axes.
 context.draftAnchor={index:1,value:10};context.activeDrawingTool='trend';
 call("switchProductionTimeframe('daily',false)");assert.equal(context.draftAnchor,null);
 // Keep prices at full precision and persist one canonical set across all views.
 process.stdout.write(JSON.stringify({ok:true,kinds,states,dates:series.daily.map(r=>r.date),minutes:originalMinutes,
   objects:context.userObjects}));
}
run().catch(error=>{console.error(error);process.exitCode=1;});
