/* Independent analysis-only position arithmetic. No account or execution APIs. */
(function(root){
  function metrics(object, rows, limit = rows.length-1, tick = .01) {
    const entry=Number(object.a.value),target=Number(object.b.value),stop=Number(object.stop);
    const direction=object.type==='short'?-1:1;
    const risk=(entry-stop)*direction,reward=(target-entry)*direction;
    const account=Number(object.account_size??1000),lot=Number(object.lot_size??1),point=Number(object.point_value??1);
    const leverage=Number(object.leverage??10000);
    const riskSize=object.risk_value===undefined ? Number(object.risk_budget??1000)
      : Number(object.risk_value)*(object.risk_unit==='percent'?account/100:1);
    const quantityRisk=risk>0?riskSize/(risk*point*lot):0;
    const quantityLeverage=account*leverage/entry*point/lot;
    const precision=object.qty_precision===undefined||object.qty_precision==='default'?0:Number(object.qty_precision);
    const factor=10**precision;
    const quantity=Math.floor(Math.max(0,Math.min(quantityRisk,quantityLeverage))*factor+1e-9)/factor;
    const result={entry,target,stop,risk,reward,riskSize,quantity,
      ratio:risk>0?reward/risk:null,targetTicks:reward/tick,stopTicks:risk/tick,
      targetPercent:reward/entry*100,stopPercent:risk/entry*100,
      targetPnl:reward*quantity*point*lot,stopPnl:-risk*quantity*point*lot,
      targetAmount:account+reward*quantity*point*lot,stopAmount:account-risk*quantity*point*lot,
      status:'open',pnl:null};
    const first=Math.max(0,Math.ceil(Math.min(object.a.index,object.b.index)));
    const last=Math.min(limit,rows.length-1,Math.floor(Math.max(object.a.index,object.b.index)));
    let entered=false;
    if(risk<=0||reward<=0){result.status='invalid';return result;}
    for(let index=first;index<=last;index++){
      const row=rows[index];
      if(!entered && Number(row.low)<=entry && Number(row.high)>=entry)entered=true;
      if(!entered)continue;
      const take=direction>0?row.high>=target:row.low<=target;
      const stopped=direction>0?row.low<=stop:row.high>=stop;
      if(take&&stopped){result.status='ambiguous';return result;}
      if(take||stopped){result.status='closed';result.pnl=take?reward:-risk;return result;}
    }
    const close=rows[Math.max(0,last)]?.close ?? rows[Math.max(0,Math.min(limit,rows.length-1))]?.close;
    if(Number.isFinite(Number(close)))result.pnl=(Number(close)-entry)*direction;
    return result;
  }
  root.AplusPositionMetrics={metrics};
})(globalThis);
