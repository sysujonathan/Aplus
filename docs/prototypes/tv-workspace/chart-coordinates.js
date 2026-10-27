/* Dates/prices are canonical; indexes belong only to the current display series. */
window.AplusChartCoordinates = (() => {
  let axisDates = {};
  const extensionCache = new Map();
  const configure = dates => { axisDates = dates || {}; extensionCache.clear(); };
  const dayOf = value => String(value || '').slice(0, 10);
  // Intraday units are keyed by minute, not by day: two 5-minute bars on the
  // same session must never collapse to one coordinate.
  const intraday = timeframe => /^m\d+$/.test(String(timeframe || ''));
  function bucket(day, timeframe) {
    if (intraday(timeframe)) {
      const match = String(day || '').match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})/);
      return match ? `${match[1]}T${match[2]}:${match[3]}` : null;
    }
    const value = dayOf(day);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const stamp = new Date(`${value}T00:00:00Z`);
    if (!Number.isFinite(stamp.getTime()) || stamp.toISOString().slice(0, 10) !== value) return null;
    if (timeframe === 'monthly') return value.slice(0, 7);
    if (timeframe === 'weekly') {
      stamp.setUTCDate(stamp.getUTCDate() + (5 - stamp.getUTCDay() + 7) % 7);
      return stamp.toISOString().slice(0, 10);
    }
    return value;
  }
  function indexForDate(rows, day, timeframe) {
    const key = bucket(day, timeframe);
    return key === null ? -1 : rows.findIndex(row => bucket(row.date, timeframe) === key);
  }
  function extension(rows, timeframe) {
    const keys = axisDates[timeframe] || [];
    const first = bucket(rows[0]?.date, timeframe), last = bucket(rows.at(-1)?.date, timeframe);
    const key = `${timeframe}:${first}:${last}`;
    if (!extensionCache.has(key)) extensionCache.set(key, {
      before: keys.filter(day => bucket(day, timeframe) < first),
      after: keys.filter(day => bucket(day, timeframe) > last)});
    return extensionCache.get(key);
  }
  function bounds(rows, timeframe = 'daily') {
    const extra = extension(rows, timeframe);
    return {min: -extra.before.length, max: rows.length - 1 + extra.after.length};
  }
  function dateAt(rows, index, timeframe = 'daily') {
    const at = Math.round(index);
    if (rows[at]) return rows[at].date;
    const extra = extension(rows, timeframe);
    const date = at < 0 ? extra.before[extra.before.length + at] : extra.after[at - rows.length];
    if (!date) throw new Error('该位置超出已确认的交易日历，不能保存为最后一根K线');
    return date;
  }
  function anchor(point, rows, timeframe) {
    if (!point) return point;
    return { ...point, date: point.date || dateAt(rows, point.index, timeframe) };
  }
  function capture(object, rows, timeframe = 'daily') {
    object.a = anchor(object.a, rows, timeframe);
    if (object.b) object.b = anchor(object.b, rows, timeframe);
    if (object.c) object.c = anchor(object.c, rows, timeframe);
    if (object.points) object.points = object.points.map(point => anchor(point, rows, timeframe));
    object.createdAtDate ||= dateAt(rows, object.createdAtIndex ?? rows.length - 1, timeframe);
    return object;
  }
  function project(point, rows, timeframe) {
    if (!point) return point;
    let index = indexForDate(rows, point.date, timeframe);
    if (index < 0) {
      const key = bucket(point.date, timeframe), extra = extension(rows, timeframe);
      const before = extra.before.findIndex(day => bucket(day, timeframe) === key);
      const after = extra.after.findIndex(day => bucket(day, timeframe) === key);
      if (before >= 0) index = before - extra.before.length;
      else if (after >= 0) index = rows.length + after;
    }
    // A drawing on a holiday/period label uses the nearest visible bar, without
    // changing its date. Evidence deliberately uses indexForDate with no fallback.
    if (index < 0) {
      const stamp = new Date(`${dayOf(point.date)}T00:00:00Z`).getTime();
      index = rows.reduce((best, row, at) =>
        Math.abs(Date.parse(row.date) - stamp) < Math.abs(Date.parse(rows[best].date) - stamp) ? at : best, 0);
    }
    return { ...point, index };
  }
  function projectObject(object, rows, timeframe) {
    object.a = project(object.a, rows, timeframe);
    if (object.b) object.b = project(object.b, rows, timeframe);
    if (object.c) object.c = project(object.c, rows, timeframe);
    if (object.points) object.points = object.points.map(point => project(point, rows, timeframe));
    object.createdAtIndex = project({ date: object.createdAtDate }, rows, timeframe).index;
    return object;
  }
  function move(point, deltaIndex, deltaValue, rows, timeframe = 'daily') {
    const index = point.index + deltaIndex;
    const date = Math.round(index) === Math.round(point.index) && point.date
      ? point.date : dateAt(rows, index, timeframe);
    return { ...point, index, date, value: point.value + deltaValue };
  }
  return { configure, bounds, indexForDate, dateAt, capture, project, projectObject, move };
})();
