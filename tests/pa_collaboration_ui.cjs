/* Small render checks for this batch only; no WebView/OAuth/install replay. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),events={};
const el=k=>{if(!elements.has(k))elements.set(k,{innerHTML:'',style:{},scrollTop:0,focus(){}});return elements.get(k);};
let requestSerial=0;
const ctx=vm.createContext({setTimeout,clearTimeout,crypto:{randomUUID:()=>`request-${++requestSerial}`},window:{addEventListener(){}},document:{querySelector:el,querySelectorAll:()=>[],addEventListener:(name,fn)=>events[name]=fn},console});
const source=fs.readFileSync(require('node:path').join(__dirname,'../gui/pa_growth/growth.js'),'utf8');
vm.runInContext(source.slice(0,source.lastIndexOf('\n  navigation();'))+'window.test={S,navigation,library,study,studyPane,growth,saveComment,card,caseActions,caseCommand,saveMetadata,publishCase,closeOverlay,reloadCase,renderOriginal,dispatch,setQuickHooks:h=>{if(h.refresh)refreshData=h.refresh;if(h.search)search=h.search;if(h.form)readMetadata=h.form;if(h.viewer)renderOriginal=h.viewer;if(h.images)requestVisibleImage=h.images;},setApi:fn=>api=fn,setEffects:fn=>{render=fn||(()=>{});toast=()=>{}}};})();',ctx);
if (!process.argv.includes('--comment-retry')&&!process.argv.includes('--comment-draft')&&!process.argv.includes('--quick-actions')&&!process.argv.includes('--reload-session')) {
const {S,navigation,library,study,studyPane}=ctx.window.test;
assert.equal(S.page,'library');navigation();
const nav=el('#navigation').innerHTML;
assert(!nav.includes('首页'));
assert(nav.indexOf('案例研究')<nav.indexOf('PA 交易试炼')&&nav.indexOf('PA 交易试炼')<nav.indexOf('知识宇宙')&&nav.indexOf('知识宇宙')<nav.indexOf('个人成长'));
S.boot={recent_case:{id:'old',title:'Continue me'},shared_status:{admin:true,writable:true}};
let html=library();assert.equal((html.match(/id="search-input"/g)||[]).length,1);
assert(html.includes('继续研究 · Continue me')&&html.includes('Ctrl+V'));
S.filterOpen=true;html=library();assert(html.includes('已下架'));
const c={id:'shared:case',title:'Public',status:'shared',contributor:{name:'Contributor'},images:[{id:'image'}],comments:[{author:{name:'Member'},created:'2026-11-05',text:'<script>bad</script>'}],history:[],original_metadata:{}};
S.case=c;S.studyPane='memory';html=study();
assert(html.includes('公共留言')&&html.includes('个人笔记')&&html.includes('编辑资料')&&html.includes('下架公共案例'));
S.boot.shared_status.admin=false;assert(!study().includes('下架公共案例'));
assert(ctx.window.test.caseActions({...c,status:'private_draft'}).includes('从本机案例库移除'));
S.studyPane='comments';html=studyPane(c,[]);assert(html.includes('&lt;script&gt;bad&lt;/script&gt;')&&!html.includes('<script>bad'));
S.studyPane='notes';html=studyPane(c,[]);assert(html.includes('仅本人可见')&&!html.includes('bad'));
S.studyPane='history';assert(studyPane(c,[]).includes('Contributor'));
console.log('PA collaboration UI: default navigation, single search, resume, roles and escaped comments passed');

} else if (process.argv.includes('--comment-retry')) {
const {S,saveComment,setApi,setEffects}=ctx.window.test;
setEffects();
el('#public-comment').value='Same public text';
S.case={id:'shared:case'};
const calls=[];
setApi(async(...args)=>{calls.push(args);if(calls.length===1)throw Error('lost ack');return {case:{id:'shared:case'}};});
(async()=>{
  await saveComment();await saveComment();
  assert.equal(calls[0][0],'comment_public_case');
  assert.equal(calls[0][3],calls[1][3],'one pending submission keeps its request identity');
  assert.equal(S.draftText['public-comment:shared:case'],'');
  await saveComment();
  assert.notEqual(calls[1][3],calls[2][3],'a new successful intent may repeat the same text');
  console.log('public comment lost-ack retry: stable request, then fresh intent');
})().catch(e=>{console.error(e);process.exitCode=1;});

}

if(process.argv.includes('--comment-draft')) {
const {S,saveComment,studyPane,setApi,setEffects}=ctx.window.test;
const key='public-comment:shared:case',field=el('#public-comment');
Object.assign(field,{id:'public-comment',dataset:{draft:key},hasAttribute:()=>false,closest:()=>null});
S.case={id:'shared:case',comments:[]};S.studyPane='comments';S.boot={shared_status:{writable:true}};
let release,rendered='';const calls=[];
setEffects(()=>{rendered=studyPane(S.case,[]);});
setApi((...args)=>{calls.push(args);return new Promise(resolve=>{release=resolve;});});
const type=text=>{field.value=text;events.input({target:field});};
(async()=>{
  type('  First submitted  ');
  const first=saveComment();
  type('Second draft typed during upload');
  release({case:{id:'shared:case',comments:[]}});await first;
  assert.equal(calls[0][2],'First submitted');
  assert.equal(S.draftText[key],'Second draft typed during upload','a late success must retain newer input');
  assert(rendered.includes('>Second draft typed during upload</textarea>'),'the newer draft survives repaint');
  const second=saveComment();
  assert.equal(calls[1][2],'Second draft typed during upload');
  assert.notEqual(calls[0][3],calls[1][3],'the retained draft is a new submission intent');
  release({case:{id:'shared:case',comments:[]}});await second;
  assert.equal(S.draftText[key],'','unchanged submitted draft clears after success');
  assert(rendered.includes('></textarea>'));
  console.log('public comment delayed reply: newer draft retained, unchanged submitted draft cleared');
})().catch(e=>{console.error(e);process.exitCode=1;});
}

if(process.argv.includes('--quick-actions')) {
const t=ctx.window.test,{S,card,caseActions,caseCommand,saveMetadata,publishCase,closeOverlay,dispatch,setApi,setEffects,setQuickHooks}=t;
const privateCase={id:'private-case',status:'private_draft',title:'Private case',memory:'Memory',keywords:[],topic_ids:[],tags:[],notes:[{text:'PRIVATE_NOTE'}],trials:[{text:'PRIVATE_JUDGMENT'}],images:[{id:'image',name:'Chart',data_url:'data:image/png;base64,YQ==',width:60,height:40}]};
const publicCase={...privateCase,id:'shared:public-case',status:'shared',contributor:{id:'owner',name:'Owner'},revision_version:'rev',visibility_version:'vis'};
const calls=[];let updated=privateCase;
S.boot={shared_status:{connected:true,writable:true,admin:false}};S.ready=true;S.scope='private';
setEffects();setQuickHooks({refresh:async()=>{},search:async()=>{},images:()=>{},form:()=>({title:'Renamed'})});
setApi(async(method,...args)=>{calls.push([method,...args]);if(method==='get_case')return {case:args[0]===publicCase.id?publicCase:updated};if(method==='update_case'){updated={...updated,...args[1]};return {case:updated};}if(method==='publish_case')return {published:true,case_id:publicCase.id};if(method==='delete_case')return {deleted:true};return {};});
ctx.window.confirm=()=>true;
(async()=>{
  const markup=card(privateCase);
  assert(markup.includes('aria-label="案例管理"')&&markup.includes('data-command="edit"')&&markup.includes('data-command="publish"')&&markup.includes('data-command="delete"'));
  const menu={hidden:true},host={querySelector:()=>menu},trigger={dataset:{action:'case-menu'},closest:()=>host,setAttribute(){}};
  let stopped=0;const before=calls.length;
  events.click({target:{closest:()=>trigger},preventDefault(){},stopPropagation(){stopped++;}});
  assert.equal(stopped,1);assert.equal(menu.hidden,false);assert.equal(calls.length,before,'card menu cannot open an image');
  S.page='library';S.overlay=null;S.case=null;S.original=null;S.cases=[privateCase];
  await caseCommand('edit',privateCase.id);
  assert.equal(S.page,'library');assert.equal(S.overlay,'metadata');assert(el('#overlay-root').innerHTML.includes('编辑案例资料'));
  closeOverlay(true);
  S.original={case:privateCase,index:0,zoom:2,x:17,y:-8,fromStudy:false,fromTrial:false};S.overlay='original';S.case=privateCase;
  t.renderOriginal();let viewer=el('#overlay-root').innerHTML;
  assert(viewer.includes('data-command="edit"')&&viewer.includes('案例管理')&&viewer.includes('研究此案例'));
  setQuickHooks({viewer:()=>{S.overlay='original';}});
  await caseCommand('edit',privateCase.id);await saveMetadata();
  assert.equal(S.page,'library');assert.equal(S.overlay,'original');assert.equal(S.original.zoom,2);assert.equal(S.original.x,17);
  assert.equal(S.original.case.title,'Renamed');assert.equal(S.cases[0].title,'Renamed');
  S.overlay=null;S.original=null;S.page='study';S.case=updated;
  assert(t.study().includes('案例管理')&&t.study().includes('data-command="edit"'));
  await caseCommand('edit',updated.id);assert.equal(S.page,'study');closeOverlay(true);
  S.boot.shared_status.admin=false;let shared=caseActions(publicCase);assert(shared.includes('data-command="edit"')&&!shared.includes('data-command="visibility"')&&!shared.includes('data-command="delete"'));
  S.boot.shared_status.admin=true;assert(caseActions(publicCase).includes('下架公共案例'));assert(caseActions({...publicCase,hidden:true}).includes('恢复案例'));
  S.boot.shared_status.writable=false;assert(/data-command="edit"[^>]+disabled/.test(caseActions(publicCase)));assert(/data-command="visibility"[^>]+disabled/.test(caseActions(publicCase)));
  await caseCommand('edit',publicCase.id);assert.notEqual(S.overlay,'metadata');
  S.boot.shared_status={connected:true,writable:true,admin:false};S.page='library';S.overlay=null;
  await caseCommand('publish',updated.id);await publishCase();
  const success=el('#overlay-root').innerHTML;assert(success.includes('已发布到公共案例')&&success.includes('view-published'));
  assert(success.includes('本机私密原件已保留')&&!success.includes('PRIVATE_NOTE')&&!success.includes('PRIVATE_JUDGMENT'));
  await dispatch('view-published',{},{});assert.equal(S.case.id,publicCase.id);assert.equal(S.overlay,'original');
  closeOverlay(true);S.original=null;S.overlay=null;S.scope='private';S.case=updated;
  S.cases=[updated];S.boot.recent_case=updated;S.trialCase=updated;S.trialRecord={id:'trial'};
  await caseCommand('delete',updated.id);
  assert.equal(S.cases.length,0);assert.equal(S.boot.recent_case,null);assert.equal(S.trialCase,null);assert.equal(S.case,null);
  S.records=[{case_id:updated.id,case_available:false,text:'Retained account history'}];S.trials=[];
  const history=t.growth();assert(history.includes('Retained account history')&&!history.includes(`data-id="${updated.id}"`));
  assert(!source.includes('PA 成长空间'));
  console.log('quick case actions: card/viewer/detail, in-place edit, roles, publication feedback and deleted references passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
}

if(process.argv.includes('--reload-session')) {
const {S,caseCommand,reloadCase,closeOverlay,setApi,setEffects,setQuickHooks}=ctx.window.test;
const a={id:'shared:A',status:'shared',title:'CASE_A',memory:'A',keywords:[],topic_ids:[],images:[],revision_version:'A1'};
const b={...a,id:'shared:B',title:'CASE_B',memory:'B',revision_version:'B1'};
S.boot={shared_status:{connected:true,writable:true,admin:false}};S.page='library';
setEffects();setQuickHooks({refresh:async()=>{},search:async()=>{}});ctx.window.confirm=()=>true;
(async()=>{
  for(const delayed of ['refresh_shared','get_case']) {
    let release,loading=false;const calls=[];
    setApi((method,id)=>{
      calls.push([method,id]);
      if(loading&&method===delayed&&(method!=='get_case'||id===a.id))return new Promise(r=>{release=r;});
      return Promise.resolve(method==='get_case'?{case:id===a.id?a:b}:{});
    });
    await caseCommand('edit',a.id);loading=true;
    const pending=reloadCase();await new Promise(r=>setImmediate(r));assert(release);
    closeOverlay(true);await caseCommand('edit',b.id);
    const context=S.actionContext,session=S.editSession,form=el('#overlay-root').innerHTML,version=S.editVersion;
    release(delayed==='get_case'?{case:{...a,title:'LATE_A',revision_version:'A_LATE'}}:{});await pending;
    assert.equal(S.case.id,b.id,`${delayed}: A must not replace B`);
    assert.equal(S.actionContext,context);assert.equal(S.editSession,session);
    assert.equal(S.editVersion,version);assert.equal(S.editVersion,'B1');
    assert.equal(el('#overlay-root').innerHTML,form);assert.equal(S.actionContext.updated,undefined);
    if(delayed==='refresh_shared')assert.equal(calls.filter(c=>c[0]==='get_case'&&c[1]===a.id).length,1,'stale refresh must not start the second request');
    closeOverlay(true);
  }
  let calls=0,fresh=false;
  setApi(async(method)=>{calls++;return method==='get_case'?{case:fresh?{...a,title:'FRESH_A',revision_version:'A2'}:a}:{};});
  await caseCommand('edit',a.id);S.dirtyMetadata=true;
  const oldSession=S.editSession,before=calls;
  ctx.window.confirm=()=>false;await reloadCase();
  assert.equal(calls,before,'declining discard keeps unsaved edits and sends no request');assert(S.dirtyMetadata);
  fresh=true;ctx.window.confirm=()=>true;await reloadCase();
  assert.equal(S.case.id,a.id);assert.equal(S.editVersion,'A2');assert.equal(S.actionContext.updated.title,'FRESH_A');
  assert.equal(S.overlay,'metadata');assert(!S.dirtyMetadata);assert.notEqual(S.editSession,oldSession);
  assert(el('#overlay-root').innerHTML.includes('FRESH_A'));
  console.log('editor reload: delayed refresh/get-case cannot replace B; active A reload and unsaved prompt retained');
})().catch(e=>{console.error(e);process.exitCode=1;});
}
