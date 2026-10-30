/* Execute production zoom, save and switch functions; view preferences are not edits. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const folder=path.join(__dirname,'../docs/prototypes/tv-workspace');
const source=fs.readFileSync(path.join(folder,'prototype-interactions.js'),'utf8');
const controls=fs.readFileSync(path.join(folder,'production-controls.js'),'utf8');
function extract(name){const start=source.indexOf(`function ${name}(`);assert.ok(start>=0,name);return source.slice(start,source.indexOf('\n}',start)+2);}
const storage=new Map(),calls=[],replaced=[];
const state={objects:[],view:{count:204},indicators:{}};
const element={style:{},append(){},innerHTML:'',textContent:''};
let timer=null;
const context=vm.createContext({structuredClone,console,window:{},
  document:{activeElement:{blur(){}},querySelector:()=>element,createElement:()=>({remove(){}})},
  localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)},
  location:{pathname:'/chart',replace:value=>replaced.push(value)},showToast(){},stopReplayTimer(){},
  chartBoot:{production:true,revision:7,state,sessionToken:1},tvStateKey:'own-symbol',
  userObjects:[],drawingTemplates:{},indicatorSettings:{},magnetEnabled:true,drawingsVisible:true,
  chartCoordinates:{capture(){}},chartAlerts:[],updateObjectTree(){},
  bridgeRevision:7,bridgeSaveChain:Promise.resolve(),bridgeSaveFailed:false,closingWorkspace:false,
  replayActive:false,replayIndex:299,viewCount:204,viewOffset:0,priceZoom:1,priceCenterOffset:0,
  activeTimeframeUnit:'daily',DEFAULT_VISIBLE_BARS:204,RIGHT_MARGIN_BARS:12,
  bars:Array.from({length:300},(_,i)=>({date:`bar-${i}`})),canvas:{_view:{high:20,low:10}},
  clamp:(v,a,b)=>Math.max(a,Math.min(b,v)),visibleWindow:()=>({begin:96}),panLimits:()=>({min:0,max:200}),
  scheduleWorkspaceRender(){},renderWorkspace(){},viewSaveTimer:null,
  clearTimeout(){timer=null;},setTimeout:fn=>{timer=fn;return 1;}});
context.window.pywebview={api:{
  save_state:async (value,revision)=>{calls.push(value);return {revision:revision+1};},
  activate_context:async()=>({token:2}),cancel_context_switch:async()=>{},retry_context_switch:async()=>{}}};
vm.runInContext(['loadPrototypeState','saveViewState','scheduleViewStateSave','savePrototypeState',
  'zoomAround','zoomPriceAround','panChart','resetChartView'].map(extract).join('\n'),context);
const start=controls.indexOf('window.aplusSwitchContext = async () => {');
vm.runInContext(controls.slice(start,controls.indexOf('\n};',start)+3),context);
async function run(){
  vm.runInContext('zoomAround(.8,.5,true)',context);
  assert.equal(typeof timer,'function');
  await context.window.aplusSwitchContext(); // Flush a pending zoom, without writing analysis.
  assert.equal(calls.length,0);assert.equal(replaced.length,1);assert.equal(context.bridgeRevision,7);
  const view=JSON.parse(storage.get('own-symbol-view'));
  assert.equal(view.view.count,163);assert.equal(view.revision,7);
  assert.equal(vm.runInContext('loadPrototypeState().view.count',context),163);
  context.chartBoot.revision=8;
  assert.equal(vm.runInContext('loadPrototypeState().view.count',context),204,'stale view cannot override a newer document');
  context.chartBoot.revision=7;
  // Immediate zoom/pan/reset and unavailable browser storage also never write Store.
  context.localStorage.setItem=()=>{throw new Error('browser storage unavailable');};
  vm.runInContext('closingWorkspace=false;zoomPriceAround(.8,.5);panChart(4);resetChartView()',context);
  await context.window.aplusSwitchContext();
  assert.equal(calls.length,0);assert.equal(replaced.length,2);assert.equal(context.bridgeSaveFailed,false);
  // A genuine drawing edit still waits for the real acknowledgement.
  let release;
  context.window.pywebview.api.save_state=()=>new Promise(resolve=>{release=resolve;});
  vm.runInContext('closingWorkspace=false;userObjects.push({id:"line"});savePrototypeState()',context);
  const switching=context.window.aplusSwitchContext();
  await Promise.resolve();await Promise.resolve();assert.equal(replaced.length,2);
  release({revision:8});await switching;assert.equal(replaced.length,3);
  // Failure of an actual analysis save still keeps the old chart and drawings.
  context.window.pywebview.api.save_state=async()=>{throw new Error('revision conflict');};
  vm.runInContext('closingWorkspace=false;savePrototypeState()',context);await context.bridgeSaveChain;
  await context.window.aplusSwitchContext();
  assert.equal(replaced.length,3);assert.equal(context.userObjects[0].id,'line');
  console.log(JSON.stringify({ok:true,viewDoesNotWriteAnalysis:true,realEditsStillGuarded:true}));
}
run().catch(error=>{console.error(error);process.exitCode=1;});
