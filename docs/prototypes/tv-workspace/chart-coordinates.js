/* Dates/prices are canonical; indexes belong only to the current display series. */
window.AplusChartCoordinates = (() => {
  const dayOf = value => String(value || '').slice(0, 10);
  function bucket(day, timeframe) {
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
  function dateAt(rows, index) {
    return rows[Math.max(0, Math.min(rows.length - 1, Math.round(index)))].date;
  }
  function anchor(point, rows) {
    if (!point) return point;
    return { ...point, date: point.date || dateAt(rows, point.index) };
  }
  function capture(object, rows) {
    object.a = anchor(object.a, rows);
    if (object.b) object.b = anchor(object.b, rows);
    if (object.c) object.c = anchor(object.c, rows);
    if (object.points) object.points = object.points.map(point => anchor(point, rows));
    object.createdAtDate ||= dateAt(rows, object.createdAtIndex ?? rows.length - 1);
    return object;
  }
  function project(point, rows, timeframe) {
    if (!point) return point;
    let index = indexForDate(rows, point.date, timeframe);
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
  function move(point, deltaIndex, deltaValue, rows) {
    const index = point.index + deltaIndex;
    const date = Math.round(index) === Math.round(point.index) && point.date
      ? point.date : dateAt(rows, index);
    return { ...point, index, date, value: point.value + deltaValue };
  }
  return { indexForDate, dateAt, capture, projectObject, move };
})();
