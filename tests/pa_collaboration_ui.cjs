/* Small render checks for this batch only; no WebView/OAuth/install replay. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
const el=k=>{if(!elements.has(k))elements.set(k,{innerHTML:''});return elements.get(k);};
let requestSerial=0;
const ctx=vm.createContext({crypto:{randomUUID:()=>`request-${++requestSerial}`},window:{addEventListener(){}},document:{querySelector:el,querySelectorAll:()=>[],addEventListener(){}},console});
const source=fs.readFileSync(require('node:path').join(__dirname,'../gui/pa_growth/growth.js'),'utf8');
vm.runInContext(source.slice(0,source.lastIndexOf('\n  navigation();'))+'window.test={S,navigation,library,study,studyPane,saveComment,setApi:fn=>api=fn,setEffects:()=>{render=()=>{};toast=()=>{}}};})();',ctx);
if (!process.argv.includes('--comment-retry')) {
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
assert(html.includes('公共留言')&&html.includes('个人笔记')&&html.includes('编辑资料')&&html.includes('下架案例'));
S.boot.shared_status.admin=false;assert(!study().includes('下架案例'));
S.studyPane='comments';html=studyPane(c,[]);assert(html.includes('&lt;script&gt;bad&lt;/script&gt;')&&!html.includes('<script>bad'));
S.studyPane='notes';html=studyPane(c,[]);assert(html.includes('仅本人可见')&&!html.includes('bad'));
S.studyPane='history';assert(studyPane(c,[]).includes('Contributor'));
console.log('PA collaboration UI: default navigation, single search, resume, roles and escaped comments passed');

} else {
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
