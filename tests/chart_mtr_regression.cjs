/* Execute the production MTR Canvas branch; no browser or Store is required. */
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../docs/prototypes/tv-workspace/production-controls.js'),'utf8');
const fn=name=>{const start=source.indexOf(`function ${name}(`);assert(start>=0);return source.slice(start,source.indexOf('\n}',start)+2);};
const bars=Array.from({length:60},(_,i)=>({date:`d${i}`,high:14,low:9}));
const model={contract:'mtr-v1',availableDate:'d55',items:[
  ...[['H0',0,18],['L1',20,8],['H1',35,16],['TL',48,9],['S',55,11]].map(([text,i,value])=>
    ({kind:'label',a:{date:`d${i}`,value},text,color:'#CBD5E1',offset:[22,-24]})),
  {kind:'marker',a:{date:'d55',value:11},marker:'D',size:5,color:'#CBD5E1',pixelOffset:[0,6]},
  {kind:'marker',a:{date:'d5',value:10},marker:'D',size:3,color:'#CBD5E1',alpha:.45},
  ...[['Entry',12,'#089981',[2,4]],['SL',8.5,'#F23645',[6,4]],['TP1',19,'#D6B86A',[8,3,2,3]]].map(([text,value,color,dash])=>
    ({kind:'level',a:{date:'d55',value},text,color,dash}))
]};
function run(width,height,begin=0,replay=false,cutoff='d59'){
  const text=[],boxes=[],lines=[],hit=[];let from;
  const ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},arc(){},closePath(){},setLineDash(){},
    moveTo(x,y){from=[x,y];},lineTo(x,y){lines.push({from,to:[x,y],color:this.strokeStyle});},stroke(){},
    fillRect(x,y,w,h){boxes.push({left:x,right:x+w,top:y,bottom:y+h});},fillText(s,x,y){text.push(s);},
    measureText(s){return {width:s.length*6.5};}};
  const view={left:16,right:60,top:20,bottom:36,width,height,begin,
    rows:bars.slice(begin),candleWidth:5,xGlobal:i=>16+(i-begin)*(width-76)/60,
    y:v=>20+(20-v)*(height-56)/14};
  const context=vm.createContext({bars,replayActive:replay,chartPrice:v=>v.toFixed(2),
    activeTimeframeUnit:'daily',registerRect:(...a)=>hit.push(a),
    chartCoordinates:{indexForDate:(rows,date)=>rows.findIndex(r=>r.date===date)}});
  vm.runInContext(fn('drawMtrEvidence'),context);
  context.ctx=ctx;context.view=view;context.data=structuredClone(model);context.cutoff=cutoff;
  vm.runInContext('drawMtrEvidence(ctx,view,data,cutoff)',context);
  for(const box of boxes){assert(box.left>=view.left&&box.right<=width-view.right);assert(box.top>=view.top&&box.bottom<=height-view.bottom);}
  for(let i=0;i<boxes.length;i++)for(let j=0;j<i;j++){
    const a=boxes[i],b=boxes[j];assert(!(a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top),'label overlap');
  }
  return {text,boxes,lines,hit,view};
}
for(const [w,h] of [[640,480],[960,640],[1920,1080]]){
  const result=run(w,h);
  assert(result.text.includes('H0')&&result.text.includes('S'));
  assert(result.text.includes('Entry 12.00')&&result.text.includes('SL 8.50')&&result.text.includes('TP1 19.00'));
  const entry=result.lines.find(l=>l.color==='#089981');
  assert.equal(entry.from[0],result.view.xGlobal(55)); // Never extend into unrelated history.
  assert(!result.text.some(t=>/Fill|Trig|成交价/.test(t)));
}
const cropped=run(960,640,25);assert(!cropped.text.includes('H0'));assert(cropped.text.some(t=>t.includes('在窗口外')));
assert.equal(run(960,640,0,true,'d20').boxes.length,0); // No future structure in replay.
assert.equal(run(960,640,0,true,'d55').boxes.length,8);
assert.deepEqual(model.items[0].a,{date:'d0',value:18}); // Rendering has no coordinate writes.
console.log(JSON.stringify({ok:true,widths:[640,960,1920],checks:'bounds, collisions, scope, clipping, replay'}));
