/* Exercise the production UI acknowledgement and draft guards without a browser. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../gui/pa_growth/growth.js'), 'utf8');
const events = [], calls = [], elements = new Map();
const element = selector => {
  if (selector === '#capture-form') return null;
  if (!elements.has(selector)) elements.set(selector, {innerHTML:'', scrollTop:0});
  return elements.get(selector);
};
const bridge = {};
const context = vm.createContext({
  window: {pywebview:{api:bridge},addEventListener(){},confirm:()=>true},
  document: {querySelector:element,querySelectorAll:()=>[],addEventListener(){},activeElement:null},
  setTimeout,clearTimeout,requestAnimationFrame:fn=>fn(),console,
});
const cut = source.lastIndexOf('\n  navigation();');
assert(cut > 0);
vm.runInContext(source.slice(0, cut) + `
  render = () => {};
  renderCapture = () => {};
  refreshData = async () => {};
  search = async () => {};
  toast = (message,error=false) => window.__events.push({message,error});
  window.__test = {S,saveCapture,canLeaveDraft,studyPane,imageUrl,esc,setRefresh:fn=>{refreshData=fn;}};
})();`, context);
context.window.__events = events;
const {S,saveCapture,canLeaveDraft,studyPane,imageUrl,esc} = context.window.__test;
function reset() {
  Object.assign(S,{pending:[{token:'image-1',name:'chart.png',duplicate_cases:[]}],
    saving:0,staging:0,captureError:'',captureMetadata:{memory:'ETHUSD microDB'},
    captureCaseId:null,captureGroup:false,duplicateAllowed:false,page:'library',
    overlay:'capture',draftText:{},dirtyMetadata:false,trialThought:'',trialReview:{},
    judgment:'',trialRecord:null,marks:[]});
  events.length = 0; calls.length = 0;
}
async function run() {
  reset();
  let acknowledge;
  bridge.save_capture = (...args) => {calls.push(args);return new Promise(resolve=>{acknowledge=resolve;});};
  const failed = saveCapture();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(S.saving,1);
  assert.equal(canLeaveDraft(),false);
  assert.equal(events.length,0,'no success before Store acknowledges');
  acknowledge({ok:false,error:'磁盘写入失败'});
  await failed;
  assert.equal(S.pending[0].token,'image-1','failed persistence keeps original staging');
  assert.equal(S.captureMetadata.memory,'ETHUSD microDB','failed persistence keeps text');
  assert.match(S.captureError,/磁盘写入失败/);
  assert.equal(events.length,0,'failure cannot claim a saved case');

  reset();
  bridge.save_capture = (...args) => {calls.push(args);return new Promise(resolve=>{acknowledge=resolve;});};
  const success = saveCapture();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(events.length,0);
  acknowledge({ok:true,cases:[{id:'saved'}]});
  await success;
  assert.equal(S.pending.length,0);
  assert.equal(S.saving,0);
  assert.equal(canLeaveDraft(),true);
  assert.equal(events.length,1);
  assert.match(events[0].message,/已保存 1 张原图/);
  assert.equal(calls[0][2].allow_duplicates,false);
  assert.equal(calls[0][2].group,false,'batch never merges implicitly');

  reset();
  context.window.__test.setRefresh(async()=>{throw new Error('列表暂时不可读');});
  bridge.save_capture = async()=>({ok:true,cases:[{id:'saved'}]});
  await saveCapture();
  assert.equal(S.pending.length,0,'a committed save is not offered for duplicate retry');
  assert.equal(S.captureError,'');
  assert.match(events[0].message,/已保存 1 张原图/);
  assert.match(events[1].message,/案例已保存，列表暂时无法刷新/);
  context.window.__test.setRefresh(async()=>{});

  reset();
  bridge.save_capture = async () => ({ok:true,cases:[],duplicates:[{id:'existing',title:'同图案例'}]});
  await saveCapture();
  assert.equal(S.pending.length,1);
  assert.equal(S.pending[0].duplicate_cases[0].id,'existing','late duplicate becomes visible confirmation');
  assert.equal(events.length,0);
  assert.equal(canLeaveDraft(),false);
  bridge.save_capture = async (...args) => {calls.push(args);return {ok:true,cases:[{id:'new'}]};};
  await saveCapture();
  assert.equal(calls.length,0,'duplicate requires explicit confirmation');
  S.duplicateAllowed = true;
  await saveCapture();
  assert.equal(calls[0][2].allow_duplicates,true);
  assert.equal(S.pending.length,0);

  reset();
  S.pending=[];
  S.draftText['case-note:case-a']='只属于案例 A 的看法';
  S.studyPane='notes';
  assert.match(studyPane({id:'case-a',notes:[]},[]),/只属于案例 A/);
  assert.doesNotMatch(studyPane({id:'case-b',notes:[]},[]),/只属于案例 A/,'draft notes are scoped per case');
  assert.equal(canLeaveDraft(),false);
  S.draftText={};S.judgment='wait';
  assert.equal(canLeaveDraft(),false,'unsubmitted judgment is not discarded silently');
  S.judgment='';S.staging=1;
  assert.equal(canLeaveDraft(),false,'pending clipboard staging blocks close');

  assert.equal(imageUrl({data_url:'https://private.example/chart.png'}),'','images are never fetched from third parties');
  assert.equal(imageUrl({data_url:'javascript:alert(1)'}),'');
  assert.equal(imageUrl({data_url:'data:image/svg+xml;base64,PHN2Zz4='}),'','active SVG content is not admitted');
  assert.match(imageUrl({data_url:'data:image/png;base64,AA=='}),/^data:image\/png/);
  assert.equal(esc('<img onerror="x">'),'&lt;img onerror=&quot;x&quot;&gt;');
  console.log(JSON.stringify({ok:true,checks:'durable-acknowledgement, failure-retention, duplicate-confirmation, independent-cases, draft-scope, close-guard, image-origin, escaping'}));
}
run().catch(error=>{console.error(error);process.exitCode=1;});
