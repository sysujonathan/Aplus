/* Production event handlers with real composition ordering and late replies. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const events={},elements=new Map(),timers=new Map();let timer=0,requests=0,release;
const input={id:'search-input',value:'锦',dataset:{},hasAttribute:()=>false,closest:()=>null};
elements.set('#search-input',input);
const el=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',style:{},scrollTop:0});return elements.get(key);};
const ctx=vm.createContext({window:{pywebview:{api:{search_cases:()=>{requests++;return new Promise(r=>release=r);}}},addEventListener(){}},
 document:{querySelector:el,querySelectorAll:()=>[],addEventListener:(name,fn)=>events[name]=fn,activeElement:input},
 setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),requestAnimationFrame:fn=>fn(),console});
const source=fs.readFileSync(require('node:path').join(__dirname,'../gui/pa_growth/growth.js'),'utf8');
const end=source.lastIndexOf('\n  navigation();');
vm.runInContext(source.slice(0,end)+`window.__ime={S,search};})();`,ctx);
const {S,search}=ctx.window.__ime;
async function flush(){for(const [id,fn] of [...timers]){timers.delete(id);fn();}await Promise.resolve();await Promise.resolve();}
async function run(){
 Object.assign(S,{page:'library',scope:'private',query:'锦'});
 const old=search();await Promise.resolve();await Promise.resolve();assert.equal(requests,1);
 events.compositionstart({target:input});
 input.value='锦和';events.input({target:input,isComposing:true});
 let prevented=false;events.keydown({target:input,key:'Enter',isComposing:true,keyCode:229,preventDefault(){prevented=true;}});
 await flush();assert.equal(requests,1);assert.equal(prevented,false,'IME confirmation is not intercepted');
 release({ok:true,cases:[{id:'stale'}]});await old;
 assert(!S.cases.some(c=>c.id==='stale'),'response before composition must not redraw active editing');
 input.value='锦和商业';events.compositionend({target:input});
 await flush();assert.equal(requests,2);
 const before=el('#main').innerHTML;
 release({ok:true,cases:[{id:'found',memory:'锦和商业'}]});await new Promise(r=>setImmediate(r));
 assert.equal(el('#search-input'),input);assert.equal(el('#main').innerHTML,before);
 assert.match(el('#search-results').innerHTML,/锦和商业/);
 assert.equal(input.value,'锦和商业');
 console.log(JSON.stringify({ok:true,compositionBoundary:true,stableInput:true,lateReplyRejected:true}));
}
run().catch(e=>{console.error(e);process.exitCode=1;});
