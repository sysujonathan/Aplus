"""Bounded freshness recovery and ephemeral quote transport, no Store writes."""
import json
from datetime import datetime,timedelta

import pytest

from workbench.live_minutes import (CHINA,IntradaySource,expected_minute_close,
    minute_refresh_phase,parse_minute_bars)
from tests.test_live_minutes import CODE,NOW,minute_frame,payload,row


@pytest.mark.parametrize('clock,expected',[
    ('09:00','previous'),('09:32','previous'),('10:53','10:50'),('11:30','11:30'),
    ('11:45','11:30'),('13:03','11:30'),('14:22','14:20'),('15:00','15:00'),('18:00','15:00')])
def test_expected_bar_respects_lunch_and_close(clock,expected):
    moment=datetime.fromisoformat('2026-10-30T'+clock).replace(tzinfo=CHINA)
    assert expected_minute_close(moment)==('2026-10-29 15:00' if expected=='previous' else '2026-10-30 '+expected)
    assert minute_refresh_phase(moment)[0]=='2026-10-30'


def test_weekend_does_not_invent_a_missing_trading_session():
    moment=datetime(2026,10,31,15,30,tzinfo=CHINA)
    assert expected_minute_close(moment)=='2026-10-30 15:00'
    assert minute_refresh_phase(moment)[1]=='closed'


def test_minute_response_carries_its_own_verified_quote_without_extra_http():
    value=json.loads(payload([row('202610261425'),row('202610261430')]))
    fields=['']*36
    for index,data in {2:'002912',3:'25.1',4:'25',5:'25',6:'100',30:'20261026143000',
                       33:'25.2',34:'24.9',35:'25.1/100/251000'}.items():fields[index]=data
    value['data']['sz002912']['qt']={'sz002912':fields}
    frame=parse_minute_bars(json.dumps(value),CODE,NOW)
    quote=frame.attrs['quote']
    assert quote['code']==CODE and quote['price']==25.1 and quote['previous_close']==25
    fields[2]='999999'
    assert 'quote' not in parse_minute_bars(json.dumps(value),CODE,NOW).attrs


def test_stale_m1_bypasses_cached_m5_once_without_request_loop():
    calls=[];stale=minute_frame();fresh=stale.copy()
    fresh['date']=['2026-10-26 14:25','2026-10-26 14:30','2026-10-26 14:35']
    def fetch(code,moment=None,*,period,count):
        calls.append(period)
        return (stale if calls.count('m5')==1 else fresh) if period=='m5' else stale
    source=IntradaySource(fetch=fetch)
    source(CODE,NOW)
    result=source(CODE,NOW+timedelta(minutes=5))
    assert calls==['m5','m1','m1','m5']
    assert result.date.iloc[-1]=='2026-10-26 14:35'


def test_stale_native_provider_is_not_retried_in_an_unbounded_loop():
    calls=[]
    def fetch(code,moment=None,*,period,count):
        calls.append(period);return minute_frame()
    source=IntradaySource(fetch=fetch)
    result=source(CODE,NOW+timedelta(minutes=30))
    assert len(result)==3 and calls==['m5','m1']
