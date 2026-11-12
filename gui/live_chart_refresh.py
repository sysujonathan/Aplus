"""Latest-only preparation of display minutes, without touching Tk in a worker."""
import json

from .background_status import LatestRefresh


def prepare_minutes(request, check):
    from workbench.live_minutes import chart_minute_series, live_metadata, minute_rows
    identity, frame, hourly = request
    check()
    rows = minute_rows(frame)
    metadata = live_metadata(frame)
    check()
    payload = dict(series=chart_minute_series(frame, hourly), live=metadata)
    check()
    # Include older minute corrections and late hourly history, not only the tail.
    key = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return frame, rows[-1]['date'], payload, key


class LiveChartRefresh:
    def __init__(self, widget, deliver):
        self.widget, self.deliver = widget, deliver
        self.reader = LatestRefresh(prepare_minutes)
        self.timer = None

    def submit(self, identity, frame, hourly):
        self.reader.submit((identity, frame, hourly))
        if self.timer is None:
            self.timer = self.widget.after(50, self.poll)

    def poll(self):
        self.timer = None
        result = self.reader.poll()
        if result is not None:
            _, request, value, error = result
            self.deliver(request[0], value, error)
        if self.reader.running:
            self.timer = self.widget.after(50, self.poll)

    def invalidate(self):
        self.reader.invalidate()

    def close(self):
        self.reader.close()
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
