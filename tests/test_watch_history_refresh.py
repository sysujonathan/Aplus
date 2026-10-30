"""Delayed daily reads must not replace the minute page or its IPC generation."""
import json
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from gui.chart_embed import EmbeddedChartHost
from gui.chart_panel import ChartPanel
from gui.watch_chart import WatchChartController
from workbench.store import Store


@pytest.mark.parametrize('refresh_succeeds',[True,False])
def test_late_history_reply_keeps_page_token_and_minutes(tmp_path,monkeypatch,refresh_succeeds):
    store=Store(tmp_path/'runtime')
    frame=pd.DataFrame(dict(date=['2026-10-28','2026-10-29'],open=1.2,high=1.3,low=1.1,close=1.25,volume=100))
    panel=SimpleNamespace(store=store,_interactive=True,_image=None,_intraday_sent=None,
        _intraday_code='sz.159570',_code='sz.159570',_intraday_frame=None,
        _title_var=Mock(),_analysis_btn=Mock(),readout_frame=Mock(),readout=Mock(),
        _publish_intraday=Mock(),_fit_image=Mock(),chart_label=Mock())
    host=EmbeddedChartHost.__new__(EmbeddedChartHost)
    host.store=store;host.parent=Mock();host.on_layout=None;host.token=0;host.closing=False
    host.process=Mock();host.process.poll.return_value=None;host._timer=None
    for field in ('control','session','history','live'):
        setattr(host,field,tmp_path/(field+'.json'))
    panel._embedded=host
    panel._show_interactive=MethodType(ChartPanel._show_interactive,panel)
    monkeypatch.setattr(EmbeddedChartHost,'available',staticmethod(lambda:True))
    controller=WatchChartController.__new__(WatchChartController)
    controller.panel=panel;controller.item=SimpleNamespace(code='sz.159570',name='ETF')
    controller.reader=Mock();controller.timeframe='daily';controller.closed=False;controller.shown=False
    controller.refresh=Mock()
    record=dict(source='baostock',adjustment='不复权',end='2026-10-29')
    value=dict(frame=frame,record=record,image=Mock(),refresh=True)
    controller.reader.poll.return_value=(1,'local',value,None)
    controller._poll()
    assert host.token==1 and controller.refresh.called
    host.push_live({'series':{'m5':[{'date':'2026-10-30 15:00'}]}})
    control=host.control.read_bytes();minutes=host.live.read_bytes()
    panel._intraday_sent='already delivered'
    fresh=frame.copy();fresh.loc[len(fresh)]=['2026-10-30',1.2,1.3,1.1,1.25,100]
    controller.reader.poll.return_value=(2,'fetch',dict(value,frame=fresh,
        record=dict(record,end='2026-10-30'),refresh=False),None) if refresh_succeeds else (2,'fetch',None,TimeoutError('offline'))
    controller._poll()
    assert host.token==1 and host.control.read_bytes()==control
    assert host.live.read_bytes()==minutes and panel._intraday_sent=='already delivered'
    if refresh_succeeds:
        payload=json.loads(host.history.read_text(encoding='utf-8'))
        assert payload['token']==1 and payload['boot']['latest']['date']=='2026-10-30'
        assert 'm5' not in payload['boot']['series']
    else:
        assert not host.history.exists()
    assert not store.rows('SELECT * FROM observations') and not store.rows('SELECT * FROM plans')


def test_new_page_for_same_code_replays_cached_minutes_without_network(monkeypatch):
    monkeypatch.setattr(EmbeddedChartHost,'available',staticmethod(lambda:True))
    frame=object()
    panel=SimpleNamespace(_interactive=True,store=True,_embedded=Mock(),_workspace_context={'code':'sz.159570'},
        readout_frame=Mock(),readout=Mock(),_intraday_sent='old-token',_intraday_code='sz.159570',
        _code='sz.159570',_intraday_frame=frame,_publish_intraday=Mock())
    ChartPanel._show_interactive(panel)
    assert panel._intraday_sent is None
    panel._publish_intraday.assert_called_once_with(frame)


@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows WebView2')
def test_native_webview_delayed_history_preserves_minute_view(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('watch_refresh_probe.py')),str(tmp_path/'probe')],
                          capture_output=True,text=True,timeout=50)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads((tmp_path/'probe/result.json').read_text())['real_history_ipc']
