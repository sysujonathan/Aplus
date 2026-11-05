/* Execute the actual workspace adapter with a minimal DOM boundary. */
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const elements=new Map();
function element(id) {
  if(!elements.has(id))elements.set(id,{style:{setProperty(){}},classList:{add(){},remove(){},toggle(){}},
    listeners:{},children:[],setAttribute(){},append(child){this.children.push(child);},
    addEventListener(key,fn){this.listeners[key]=fn;},click(){this.clicked=true;}});
  return elements.get(id);
}
const frame=[{date:'2026-10-29',close:10},{date:'2026-10-30',close:11}];
const object={id:'own',type:'trend',a:{date:frame[0].date,index:0,value:10},b:{date:frame[1].date,index:1,value:11}};
const context=vm.createContext({console,structuredClone,window:{confirm:()=>true},
  crypto:{randomUUID:()=> 'copy'},document:{body:element('body'),createElement:()=>element('new'+elements.size),
    querySelector:element,getElementById:element,querySelectorAll:()=>[]},
  chartBoot:{workspace:{layout:'l1',pane:'0',defaultScope:'pane'},identity:'same-source-price-basis',source:'csv',adjustment:'不复权',
    series:{daily:frame,weekly:frame,m5:frame},symbol:'600000'},
  userObjects:[structuredClone(object)],selectedId:'own',undoStack:[],redoStack:[],
  objectAtSelection(){return context.userObjects.find(o=>o.id===context.selectedId);},
  rememberObjects(){context.undoStack.push(structuredClone(context.userObjects));},
  savePrototypeState(){context.window.aplusWorkspaceBeforeSave();},updateObjectTree(){},renderWorkspace(){},
  toolNames:{trend:'趋势线',channel:'通道'},startTool(value){context.lastTool=value;},
  closingWorkspace:false,replayActive:false,activeDrawingTool:null,activeTimeframeUnit:'daily',
  magnetEnabled:true,drawingsVisible:true,indicatorSettings:{lock_drawings:false},
  bars:structuredClone(frame),viewOffset:0,viewCount:2,bridgeSaveFailed:false,bridgeRevision:0,
  canvas:Object.assign(element('canvas'),{_view:null}),crosshair:element('crosshair'),
  chartPrice:String,switchProductionTimeframe(period){
    if(!context.chartBoot.series[period])return false;context.activeTimeframeUnit=period;return true;
  },chartCoordinates:{indexForDate:(bars,date)=>bars.findIndex(b=>b.date===date),projectObject(){}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/prototypes/tv-workspace/workspace-controls.js'),'utf8'),context);
const api=context.window;
assert.equal(context.userObjects[0].shareScope.kind,'global','legacy scope is preserved');
assert.equal(api.aplusWorkspaceCommand({kind:'tool',value:'channel'}),true);
assert.equal(context.lastTool,'channel');
assert.equal(api.aplusWorkspaceCommand({kind:'tool',value:'unknown'}),false);
api.aplusWorkspaceSaveStarted();
assert.equal(api.aplusWorkspaceState().dirty,true);
assert.equal(api.aplusWorkspaceCommand({kind:'sync',value:{timeframe:'weekly'}}),false);
assert.equal(api.aplusWorkspaceDrawings([],1),false,'remote changes cannot discard an in-flight local edit');
api.aplusWorkspaceSaveFinished();
assert.equal(api.aplusWorkspaceCommand({kind:'sync',value:{timeframe:'m5'}}),true);
assert.equal(context.activeTimeframeUnit,'m5');
assert.equal(api.aplusWorkspaceCommand({kind:'sync',value:{timeframe:'m60'}}),false,'no fabricated hourly candles');
const picker=element('#selectedObject').children[0];
picker.value='pane';picker.listeners.change();
assert.equal(context.userObjects.length,2);
assert.equal(context.userObjects[0].shareScope.kind,'global','narrowing never destroys the shared original');
assert.equal(context.userObjects[1].shareScope.kind,'pane');
assert.equal(context.userObjects[1].id,'copy');
context.undoStack=[structuredClone(context.userObjects)];
const remote=[...structuredClone(context.userObjects),{...object,id:'peer'}];
assert.equal(api.aplusWorkspaceDrawings(remote,7),true);
assert.ok(context.undoStack[0].some(o=>o.id==='peer'),'local undo retains peer work');
assert.equal(context.bridgeRevision,7);
context.replayActive=true;
assert.equal(api.aplusWorkspaceCommand({kind:'sync',value:{timeframe:'daily'}}),false);
assert.equal(api.aplusWorkspaceCommand({kind:'compare',value:{source:'other',adjustment:'不复权',series:{}}}),false);
console.log(JSON.stringify({ok:true,scopeCopy:true,pendingPreserved:true,peerUndoPreserved:true}));
