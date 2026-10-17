/* Display indicators only. Never feed these settings into strategy detection. */
(function (root) {
  function source(row, name) {
    if (name === 'hl2') return (Number(row.high) + Number(row.low)) / 2;
    if (name === 'hlc3') return (Number(row.high) + Number(row.low) + Number(row.close)) / 3;
    if (name === 'ohlc4') return (Number(row.open) + Number(row.high) + Number(row.low) + Number(row.close)) / 4;
    return Number(row[name]);
  }
  function average(values, length, alpha = 2 / (length + 1)) {
    let previous;
    return values.map(value => {
      if (!Number.isFinite(value)) return null;
      previous = previous === undefined ? value : value * alpha + previous * (1 - alpha);
      return previous;
    });
  }
  function smooth(values, rows, method, length, deviation = 2) {
    length = Math.max(1, Number(length));
    if (method === 'none') return {values: [], upper: [], lower: []};
    if (method === 'ema') return {values: average(values, length), upper: [], lower: []};
    if (method === 'rma') {
      let previous;
      const output=values.map((value,index)=>{
        if(index+1<length)return null;
        if(previous===undefined){const seed=values.slice(index-length+1,index+1);
          if(seed.some(v=>!Number.isFinite(v)))return null;previous=seed.reduce((a,b)=>a+b,0)/length;}
        else if(Number.isFinite(value))previous=(value+previous*(length-1))/length;
        else return null;
        return previous;
      });
      return {values:output,upper:[],lower:[]};
    }
    const upper = [], lower = [];
    const output = values.map((value, index) => {
      if (index + 1 < length) {upper.push(null);lower.push(null);return null;}
      const window = values.slice(index - length + 1, index + 1);
      if (window.some(v => !Number.isFinite(v))) {upper.push(null);lower.push(null);return null;}
      const weights = window.map((v, n) => method === 'wma' ? n + 1 : method === 'vwma' ? Number(rows[index-length+1+n].volume) : 1);
      const total = weights.reduce((a, b) => a + b, 0);
      const mean = total ? window.reduce((sum, v, n) => sum + v * weights[n], 0) / total : null;
      const spread = mean === null ? null : Math.sqrt(window.reduce((sum, v) => sum + (v - mean) ** 2, 0) / length) * deviation;
      upper.push(method === 'sma_bb' && spread !== null ? mean + spread : null);
      lower.push(method === 'sma_bb' && spread !== null ? mean - spread : null);
      return mean;
    });
    return {values: output, upper, lower};
  }
  function periodKey(day, unit) {
    if(unit==='daily')return day;
    if(unit==='monthly')return day.slice(0,7);
    const stamp=new Date(`${day}T00:00:00Z`);
    stamp.setUTCDate(stamp.getUTCDate()+(5-stamp.getUTCDay()+7)%7);
    return stamp.toISOString().slice(0,10);
  }
  function aggregate(rows, unit) {
    if(unit==='daily')return rows;
    const groups=new Map();
    rows.forEach(row=>{
      const key=periodKey(row.date,unit);
      const date=unit==='monthly'?new Date(Date.UTC(Number(key.slice(0,4)),Number(key.slice(5,7)),0)).toISOString().slice(0,10):key;
      const old=groups.get(key);
      if(!old)groups.set(key,{...row,date});
      else {old.high=Math.max(old.high,row.high);old.low=Math.min(old.low,row.low);old.close=row.close;old.volume+=row.volume;}
    });return [...groups.values()];
  }
  function mergeTimeframe(values, calculationRows, chartRows, calculationUnit, chartUnit, closeDates, cutoff, wait) {
    const rank={daily:0,weekly:1,monthly:2};
    if(calculationUnit===chartUnit)return values;
    const closes=closeDates[calculationUnit]||{},chartCloses=closeDates[chartUnit]||{};
    let cursor=-1;
    return chartRows.map((row,index)=>{
      const end=chartCloses[row.date]||row.date;
      while(cursor+1<calculationRows.length && (closes[calculationRows[cursor+1].date]||calculationRows[cursor+1].date)<=end &&
        (closes[calculationRows[cursor+1].date]||calculationRows[cursor+1].date)<=cutoff)cursor++;
      if(rank[calculationUnit]>rank[chartUnit]){
        const closed=cursor>=0?(closes[calculationRows[cursor].date]||calculationRows[cursor].date):null;
        if(wait && closed!==end)return null;
        // Only the currently exposed chart bar may use a forming aggregate.
        // calculationRows was built from the visible daily prefix, not future OHLC.
        const next=calculationRows[cursor+1];
        if(!wait && index===chartRows.length-1 && next && periodKey(row.date,calculationUnit)===periodKey(next.date,calculationUnit))return values[cursor+1];
      }
      return cursor<0?null:values[cursor];
    });
  }
  function replayFrame(series, unit, cutoff, closeDates = {}) {
    const complete = series[unit] || [];
    const daily = series.daily || [];
    if (!daily.length) {
      const index = complete.findLastIndex(row => (closeDates[unit]?.[row.date] || row.date) <= cutoff);
      return {rows: structuredClone(complete), index};
    }
    // Rebuild the exposed, possibly forming week/month from the daily prefix.
    // Retain future date slots solely for canonical drawing coordinates.
    const exposed = aggregate(daily.filter(row => row.date <= cutoff), unit);
    const last = exposed.at(-1)?.date;
    return {rows: [...exposed, ...complete.filter(row => !last || row.date > last)].map(row => ({...row})),
      index: exposed.length - 1};
  }
  function nextReplayDate(series, unit, cutoff, closeDates = {}) {
    const daily = series.daily || [];
    if (!daily.length) return (series[unit] || []).map(row => closeDates[unit]?.[row.date] || row.date)
      .find(date => date > cutoff) || null;
    const ends = new Map();
    daily.forEach(row => ends.set(periodKey(row.date, unit), row.date));
    return [...ends.values()].find(date => date > cutoff) || null;
  }
  root.AplusChartIndicators = {source, average, smooth, aggregate, mergeTimeframe, replayFrame, nextReplayDate};
})(globalThis);
