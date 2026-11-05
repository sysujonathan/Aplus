/* Local notification, cached DOM and late responses share one security epoch. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../gui/pa_growth/growth.js'),'utf8');
const elements=new Map(),timers=[];let renders=0;
const el=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',style:{}});return elements.get(key);};
const bridge={};
const ctx=vm.createContext({window:{pywebview:{api:bridge},addEventListener(){}},
 document:{querySelector:el,querySelectorAll:()=>[],addEventListener(){},activeElement:null},
 setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},requestAnimationFrame:fn=>fn(),console});
vm.runInContext(source.slice(0,source.lastIndexOf('\n  navigation();'))+`
 render=()=>window.renders++;
 boot=async()=>{};
 window.renders=0;window.test={S,api,pollSecurity,rememberOriginal,imageUrl,security:()=>security};})();`,ctx);
const {S,api,pollSecurity,rememberOriginal,imageUrl}=ctx.window.test;
async function run(){
 bridge.bootstrap=async()=>({ok:true,security:'account-a'});
 await api('bootstrap');
 Object.assign(S,{ready:true,page:'study',case:{id:'shared:a',notes:[{text:'private-a'}]},
  cases:[{id:'shared:a'}],records:[{text:'private-a'}],trials:[{id:'trial-a'}],draftText:{note:'draft-a'},
  pending:[{token:'local-image'}],trialCase:{id:'shared:a'},query:'private-a',original:{case:{id:'shared:a'}}});
 rememberOriginal({id:'image-a',data_url:'data:image/png;base64,YQ=='});
 let release;bridge.get_image=()=>new Promise(resolve=>release=resolve);
 const old=api('get_image','shared:a','image-a');await Promise.resolve();await Promise.resolve();
 bridge.security_state=async()=>({ok:true,security:'account-b'});
 await pollSecurity();
 assert.equal(S.case,null);assert.equal(S.original,null);assert.equal(S.trialCase,null);
 assert.equal(S.cases.length,0);assert.equal(S.records.length,0);assert.equal(S.trials.length,0);
 assert.equal(Object.keys(S.draftText).length,0);assert.equal(S.query,'');
 assert.equal(S.pending[0].token,'local-image','private unsaved images are retained');
 assert.equal(imageUrl({id:'image-a'}),'','original cache cleared');
 release({ok:true,security:'account-a',image:{id:'image-a',data_url:'data:image/png;base64,YQ=='}});
 await assert.rejects(old,/账号或访问权限已改变/);
 assert.equal(ctx.window.test.security(),'account-b','late response cannot restore previous identity');
 bridge.bootstrap=async()=>({ok:true,security:'account-b'});await api('bootstrap');
 Object.assign(S,{page:'study',case:{id:'shared:b'},records:[{text:'private-b'}]});
 bridge.get_image=async()=>({ok:false,error:'权限不足',error_kind:'security',security:'revoked-b'});
 await assert.rejects(api('get_image','shared:b','image-b'),/权限不足/);
 assert.equal(S.case,null);assert.equal(S.records.length,0);
 // An ordinary offline failure must leave the same authorized DOM intact.
 bridge.bootstrap=async()=>({ok:true,security:'revoked-b'});await api('bootstrap');
 S.case={id:'cached'};
 bridge.get_image=async()=>({ok:false,error:'断网',error_kind:'offline',security:'revoked-b'});
 await assert.rejects(api('get_image'),/断网/);assert.equal(S.case.id,'cached');
 console.log(JSON.stringify({ok:true,accountSwitchClearsDom:true,revocationClearsDom:true,lateReplyRejected:true,offlineCacheRetained:true,pendingPrivateImagesRetained:true}));
}
run().catch(e=>{console.error(e);process.exitCode=1;});
