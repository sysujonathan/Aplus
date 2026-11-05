/* Render only the growth page: account/local scope survives login transitions. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx=vm.createContext({window:{addEventListener(){}},document:{querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}},console});
const source=fs.readFileSync(require('node:path').join(__dirname,'../gui/pa_growth/growth.js'),'utf8');
const end=source.lastIndexOf('\n  navigation();');
vm.runInContext(source.slice(0,end)+'window.scopeTest={S,growth};})();',ctx);
const {S,growth}=ctx.window.scopeTest;
S.boot={shared_status:{connected:true}};S.stats={notes:1,trials:1};
S.records=[{text:'账号内笔记',created:'2026-11-05'}];S.trials=[];
let html=growth();
assert(html.includes('当前 Google 账号 · 本机历史记录仍在私密案例页'));
assert(html.includes('账号内笔记'));
S.boot.shared_status.connected=false;S.records=[{text:'本机旧笔记',created:'2026-11-04'}];
html=growth();
assert(html.includes('本机记录 · 账号研究请连接原 Google 账号查看'));
assert(html.includes('本机旧笔记')&&!html.includes('账号内笔记'));
console.log('growth record scope: connected and disconnected render correctly');
