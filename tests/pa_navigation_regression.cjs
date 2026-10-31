/* Deterministic delayed-search race: run real navigation with controlled timers. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict');
const timers=new Map(),elements=new Map();let next=0,searches=0;
const element=key=>{if(!elements.has(key))elements.set(key,{scrollTop:0,innerHTML:'',focus(){}});return elements.get(key);};
const context=vm.createContext({window:{pywebview:{api:{get_case:async id=>({ok:true,case:{id}})}},addEventListener(){}},
  document:{querySelector:element,querySelectorAll:()=>[],addEventListener(){}},
  setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),
  requestAnimationFrame:fn=>fn(),console});
const source=fs.readFileSync(path.join(__dirname,'../gui/pa_growth/growth.js'),'utf8');
const end=source.lastIndexOf('\n  navigation();');assert(end>0);
vm.runInContext(source.slice(0,end)+`
render=()=>{};refreshData=async()=>{};search=async()=>{window.__searched();};
window.__navigation={S,scheduleSearch,navigate,openCase};})();`,context);
context.window.__searched=()=>searches++;
const production=context.window.__navigation;
async function run(){
  for(const destination of ['home','universe','study']){
    Object.assign(production.S,{page:'library',overlay:null,saving:0,searchBusy:false});
    timers.clear();searches=0;
    production.scheduleSearch();
    if(destination==='study')await production.openCase('synthetic-case');
    else await production.navigate(destination);
    for(const callback of [...timers.values()])callback();
    await Promise.resolve();
    assert.equal(production.S.page,destination,'an old search timer must not steal explicit navigation');
    assert.equal(searches,0,'an abandoned query must not request another page');
  }
  console.log(JSON.stringify({ok:true,controlledTimers:true,explicitNavigationRetained:true}));
}
run().catch(error=>{console.error(error);process.exitCode=1;});
