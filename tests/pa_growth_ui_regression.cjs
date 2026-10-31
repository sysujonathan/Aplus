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
  const productionSearch = search;
  render = () => {};
  renderCapture = () => {};
  refreshData = async () => {};
  search = async () => {};
  toast = (message,error=false) => window.__events.push({message,error});
  window.__test = {S,saveCapture,canLeaveDraft,studyPane,imageUrl,esc,search:productionSearch,library,boot,universe,selectTopic,openTopic,dispatch,setRender:fn=>{render=fn;},setRefresh:fn=>{refreshData=fn;}};
})();`, context);
context.window.__events = events;
const {S,saveCapture,canLeaveDraft,studyPane,imageUrl,esc} = context.window.__test;
function reset() {
  Object.assign(S,{pending:[{token:'image-1',name:'chart.png',duplicate_cases:[]}],
    saving:0,staging:0,captureError:'',captureMetadata:{memory:'ETHUSD microDB'},
    captureCaseId:null,captureGroup:false,duplicateAllowed:false,page:'library',
    overlay:'capture',draftText:{},dirtyMetadata:false,trialThought:'',trialReview:{},
    judgment:'',trialRecord:null,marks:[],hasMore:false,searchBusy:false,searchSeq:0,
    searchError:'',query:'',filters:{symbol:'',timeframe:'',market:'',data_kind:''},cases:[],context:null,
    topicSeq:0,topicId:null,topicParent:null,topicCases:[],topicLoading:false,topics:[]});
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

  // A blank title/keyword is valid: older screenshots remain reachable by
  // browsing the next page, with the current query and filters preserved.
  const production = context.window.__test;
  reset();S.pending=[];S.cases=[{id:'recent'}];S.hasMore=true;
  S.query='';S.filters.market='A股';
  bridge.search_cases=async(...args)=>{calls.push(args);return {ok:true,cases:[{id:'older-unnamed',title:'',keywords:[]}],has_more:false,next_offset:2};};
  await production.search(false,true);
  assert.equal(calls[0][0],'');assert.equal(calls[0][1].market,'A股');assert.equal(calls[0][2],1);
  assert.deepEqual(Array.from(S.cases,item=>item.id),['recent','older-unnamed']);
  assert.equal(S.hasMore,false);
  assert.equal(S.query,'');assert.equal(S.filters.market,'A股');
  assert.doesNotMatch(production.library(),/data-action="load-more"/);

  reset();S.pending=[];S.cases=[{id:'keep-me'}];S.hasMore=true;
  const loaded=S.cases;
  bridge.search_cases=async()=>({ok:false,error:'下一页暂时无法读取'});
  await production.search(false,true);
  assert.equal(S.cases,loaded,'append failures preserve already loaded cases');
  assert.equal(S.hasMore,true,'failed next-page requests remain retryable');
  assert.match(S.searchError,/下一页暂时无法读取/);
  const retryHTML=production.library();
  assert.match(retryHTML,/data-id="keep-me"/);
  assert.match(retryHTML,/重试加载更多/);
  assert(retryHTML.indexOf('data-action="load-more"')>retryHTML.indexOf('data-id="keep-me"'),'load more follows the current grid');

  reset();S.pending=[];S.cases=[{id:'first-page'}];S.hasMore=true;
  let releasePage;
  bridge.search_cases=(...args)=>{calls.push(args);return new Promise(resolve=>{releasePage=resolve;});};
  const loading=production.search(false,true);
  await Promise.resolve();await Promise.resolve();
  assert.equal(S.searchBusy,true);
  assert.equal(element('.results-head > span').textContent,'正在加载…','append feedback is visible while the request is pending');
  await production.search(false,true);
  assert.equal(calls.length,1,'double clicking cannot fetch the same page twice');
  releasePage({ok:true,cases:[{id:'next-page'}],has_more:true,next_offset:2});
  await loading;
  assert.deepEqual(Array.from(S.cases,item=>item.id),['first-page','next-page']);
  assert.equal(S.searchBusy,false);

  let releaseOld,releaseNew;
  calls.length=0;
  bridge.search_cases=(...args)=>{calls.push(args);return new Promise(resolve=>{if(args[0]==='new query')releaseNew=resolve;else releaseOld=resolve;});};
  const oldPage=production.search(false,true);
  await Promise.resolve();await Promise.resolve();
  S.query='new query';S.filters.timeframe='daily';
  const newQuery=production.search(false);
  await Promise.resolve();await Promise.resolve();
  assert.equal(element('.results-head > span').textContent,'正在找图…','new query feedback updates without rebuilding the input');
  assert.equal(calls[0][2],2);assert.equal(calls[1][2],0,'new queries reset the page offset');
  releaseOld({ok:true,cases:[{id:'stale-old-page'}],has_more:true});
  await oldPage;
  assert.equal(S.searchBusy,true,'an old response cannot unlock a newer search');
  releaseNew({ok:true,cases:[{id:'new-query-result'}],has_more:false,next_offset:1});
  await newQuery;
  assert.deepEqual(Array.from(S.cases,item=>item.id),['new-query-result']);
  assert.equal(S.hasMore,false);assert.equal(S.searchBusy,false);

  reset();S.pending=[];S.page='home';
  bridge.bootstrap=async()=>({ok:true,cases:[{id:'bootstrap-first-page'}],has_more:true,topics:[],stats:{},trials:[],context:{symbol:'ETHUSD',timeframe:'daily'}});
  await production.boot();
  assert.equal(S.page,'library','chart comparison opens directly into case search');
  assert.equal(S.query,'','chart symbols do not silently restrict personal search');
  assert.equal(S.cases[0].id,'bootstrap-first-page');assert.equal(S.hasMore,true);

  reset();S.pending=[];S.page='universe';
  S.topics=[{id:'context',name:'市场背景',parent_id:null},{id:'continuation',name:'趋势延续',parent_id:null},{id:'cont-h2',name:'H2',parent_id:'continuation'}];
  const topicField=element('#topic-note');
  Object.assign(topicField,{dataset:{draft:'topic-note:context'},value:'背景主题原有草稿'});
  const topicRenders=[];
  production.setRender(()=>{
    const html=production.universe();
    topicRenders.push({id:S.topicId,html});
    topicField.dataset={draft:S.topicId ? 'topic-note:' + S.topicId : ''};
    topicField.value=S.topicId ? S.draftText['topic-note:' + S.topicId] || '' : '';
  });
  const releaseTopics=new Map();
  bridge.search_cases=(_query,filters)=>new Promise(resolve=>releaseTopics.set(filters.topic_id,resolve));
  const pendingH2=production.selectTopic('cont-h2');
  assert.equal(topicField.dataset.draft,'topic-note:cont-h2','new topic input is bound before the asynchronous lookup');
  assert.equal(topicField.value,'','previous topic text cannot appear under the new topic');
  assert.equal(S.draftText['topic-note:context'],'背景主题原有草稿');
  assert.equal(S.topicLoading,true);
  topicField.value='请求仍在等待时输入的 H2 研究';
  await Promise.resolve();await Promise.resolve();
  releaseTopics.get('cont-h2')({ok:true,cases:[{id:'h2-case'}]});
  await pendingH2;
  assert.equal(S.draftText['topic-note:cont-h2'],'请求仍在等待时输入的 H2 研究');
  assert.equal(topicField.value,'请求仍在等待时输入的 H2 研究');
  assert.match(topicRenders.at(-1).html,/请求仍在等待时输入的 H2 研究/);
  assert.equal(S.topicLoading,false);

  const oldTopic=production.selectTopic('context');
  await Promise.resolve();await Promise.resolve();
  const latestTopic=production.openTopic('cont-h2');
  await Promise.resolve();await Promise.resolve();
  topicField.value='只属于最新 H2 主题的研究';
  releaseTopics.get('cont-h2')({ok:true,cases:[{id:'latest-h2-case'}]});
  await latestTopic;
  const renderedLatest=topicRenders.length;
  releaseTopics.get('context')({ok:true,cases:[{id:'stale-context-case'}]});
  await oldTopic;
  assert.equal(S.topicId,'cont-h2');assert.equal(S.topicCases[0].id,'latest-h2-case');
  assert.equal(topicField.value,'只属于最新 H2 主题的研究');
  assert.equal(topicRenders.length,renderedLatest,'older topic responses cannot redraw the latest topic');

  const beforeRoot=production.selectTopic('context');
  await Promise.resolve();await Promise.resolve();
  await production.dispatch('universe-root',{},{});
  const renderedRoot=topicRenders.length;
  releaseTopics.get('context')({ok:true,cases:[{id:'stale-after-root'}]});
  await beforeRoot;
  assert.equal(S.topicId,null);assert.equal(S.topicCases.length,0);
  assert.equal(topicRenders.length,renderedRoot,'returning to the root invalidates an outstanding topic lookup');

  const beforePage=production.selectTopic('cont-h2');
  await Promise.resolve();await Promise.resolve();
  S.page='library';
  const renderedPage=topicRenders.length;
  releaseTopics.get('cont-h2')({ok:true,cases:[{id:'stale-on-other-page'}]});
  await beforePage;
  assert.equal(S.page,'library');assert.equal(topicRenders.length,renderedPage,'topic responses cannot replace another page');
  console.log(JSON.stringify({ok:true,checks:'durable-acknowledgement, failure-retention, duplicate-confirmation, independent-cases, draft-scope, close-guard, image-origin, escaping, pagination, append-failure-retention, load-guard, query-reset, comparison-search-entry, topic-draft-race, stale-topic-response, immediate-search-feedback'}));
}
run().catch(error=>{console.error(error);process.exitCode=1;});
