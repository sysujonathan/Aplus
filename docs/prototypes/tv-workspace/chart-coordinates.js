/* Dates/prices are canonical; indexes belong only to the current display series. */
window.AplusChartCoordinates = (() => {
  let axisDates = {};
  const extensionCache = new Map();
  const minuteCache = new Map();
  const configure = dates => { axisDates = dates || {}; extensionCache.clear(); minuteCache.clear(); };
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
  function minuteAxis(rows, timeframe) {
    const key = `${timeframe}:${rows.map(row => row.date).join('|')}`;
    if (minuteCache.has(key)) return minuteCache.get(key);
    const size = Number(timeframe.slice(1));
    // m240 is the existing half-day display. Session breaks consume no bars.
    const span = size === 240 ? 120 : size;
    if (!Number.isFinite(span) || span <= 0) throw new Error('分钟周期无效');
    const clocks = [];
    if (size === 1) clocks.push('09:30:00');
    for (let end = span; end <= 240; end += span) {
      const minute = end <= 120 ? 570 + end : 780 + end - 120;
      clocks.push(`${String(Math.floor(minute / 60)).padStart(2,'0')}:${String(minute % 60).padStart(2,'0')}:00`);
    }
    const days = [...new Set([...(axisDates.daily || []).map(dayOf), ...rows.map(row => dayOf(row.date))])].sort();
    const ranks = new Map(days.map((day, index) => [day, index]));
    const position = date => {
      const day = dayOf(date), rank = ranks.get(day);
      if (rank === undefined) return null;
      const clock = String(date).match(/[T ](\d{2}):(\d{2})/);
      if (!clock) return rank * clocks.length; // a daily anchor maps to the first session bar
      const minute = Number(clock[1]) * 60 + Number(clock[2]);
      const elapsed = minute <= 690 ? minute - 570 : 120 + Math.max(0, minute - 780);
      if (size === 1) return rank * clocks.length + Math.max(0, Math.min(240, elapsed));
      return rank * clocks.length + Math.max(0, Math.min(clocks.length - 1, Math.ceil(elapsed / span) - 1));
    };
    const positions = rows.map(row => position(row.date));
    const stamp = ordinal => `${days[Math.floor(ordinal / clocks.length)]} ${clocks[ordinal % clocks.length]}`;
    const first = positions[0], last = positions.at(-1);
    // Reserve only two verified sessions on either side, not years of minute strings.
    const before = [], after = [];
    if (axisDates.daily?.length) {
      for (let at = Math.max(0, first - 2 * clocks.length); at < first; at++) before.push(stamp(at));
      for (let at = last + 1; at <= Math.min(days.length * clocks.length - 1, last + 2 * clocks.length); at++) after.push(stamp(at));
    }
    const limits = axisDates.daily?.length ? {min: -first,
      max: rows.length - 1 + days.length * clocks.length - 1 - last} : {min: 0, max: rows.length - 1};
    const dateForIndex = index => {
      const ordinal = index < 0 ? first + index : last + index - rows.length + 1;
      if (index < limits.min || index > limits.max) return null;
      return stamp(ordinal);
    };
    const result = { before, after, position, positions, limits, dateForIndex };
    minuteCache.clear(); minuteCache.set(key, result);
    return result;
  }
  function extension(rows, timeframe) {
    if (intraday(timeframe)) return minuteAxis(rows, timeframe);
    const keys = axisDates[timeframe] || [];
    const first = bucket(rows[0]?.date, timeframe), last = bucket(rows.at(-1)?.date, timeframe);
    const key = `${timeframe}:${first}:${last}`;
    if (!extensionCache.has(key)) extensionCache.set(key, {
      before: keys.filter(day => bucket(day, timeframe) < first),
      after: keys.filter(day => bucket(day, timeframe) > last)});
    return extensionCache.get(key);
  }
  function bounds(rows, timeframe = 'daily') {
    if (intraday(timeframe)) return minuteAxis(rows, timeframe).limits;
    const extra = extension(rows, timeframe);
    return {min: -extra.before.length, max: rows.length - 1 + extra.after.length};
  }
  function dateAt(rows, index, timeframe = 'daily') {
    const at = Math.round(index);
    if (rows[at]) return rows[at].date;
    if (intraday(timeframe)) {
      const date = minuteAxis(rows, timeframe).dateForIndex(at);
      if (!date) throw new Error('该位置超出已确认的交易日历，不能保存为最后一根K线');
      return date;
    }
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
      if (before >= 0) return { ...point, index: before - extra.before.length };
      if (after >= 0) return { ...point, index: rows.length + after };
      if (intraday(timeframe)) {
        const axis = minuteAxis(rows, timeframe), at = axis.position(point.date);
        if (at !== null) {
          const positions = axis.positions;
          if (at < positions[0]) return { ...point, index: at - positions[0] };
          if (at > positions.at(-1)) return { ...point, index: rows.length - 1 + at - positions.at(-1) };
          const next = positions.findIndex(value => value >= at);
          if (next >= 0) return { ...point, index: next === 0 || positions[next] === at ? next :
            next - 1 + (at - positions[next - 1]) / (positions[next] - positions[next - 1]) };
        }
      }
    }
    // A drawing on a holiday/period label uses the nearest visible bar, without
    // changing its date. Evidence deliberately uses indexForDate with no fallback.
    if (index < 0) {
      const date = String(point.date || '');
      const stamp = Date.parse(date.length === 10 ? `${date}T00:00:00Z` :
        date.replace(' ', 'T') + (/Z$|[+-]\d\d:\d\d$/.test(date) ? '' : 'Z'));
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
