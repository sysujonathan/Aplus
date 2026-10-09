from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from workbench.tencent_quotes import CHINA, MAX_BODY, parse_tencent_quotes, fetch_tencent_quotes
from workbench.holding_quotes import fetch_holding_quotes, QuoteFailures, select_quotes

NOW=datetime(2026,10,9,10,0,tzinfo=CHINA)


def payload(symbol='sh600000', **changes):
    f=['0']*57
    for k,v in {2:symbol[2:],3:'10',4:'9.5',5:'9.8',6:'123',30:'20261009100000',
                33:'10.2',34:'9.7',35:'10/123/121234.56',**changes}.items():
        f[int(k)]=v
    return f'v_{symbol}="'+ '~'.join(f)+'";'


def response(body):
    r=Mock();r.read.return_value=body
    r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False)
    return r


def test_raw_snapshot_identity_ohlc_and_volume_amount_units():
    text=payload()+payload('bj920002')
    q=parse_tencent_quotes(text,['sh.600000','bj.920002'],NOW)
    assert set(q)=={'sh.600000','bj.920002'}
    a=q['sh.600000']
    assert (a['open'],a['high'],a['low'],a['price'])==(9.8,10.2,9.7,10)
    assert a['volume']==12300 and a['amount']==121234.56
    assert a['source']=='tencent' and a['kind']=='snapshot' and a['adjustment']=='不复权'
    assert not parse_tencent_quotes(payload('sz600000'),['sh.600000'],NOW)
    assert parse_tencent_quotes(payload('sh688981'),['sh.688981'],NOW)['sh.688981']['volume']==123


@pytest.mark.parametrize('changes',[{'2':'600001'},{'3':'nan'},{'4':'0'},{'6':'-1'},
    {'33':'9'},{'34':'11'},{'35':'10/123/inf'},{'35':'10/124/50'},
    {'30':'20261009100200'},{'30':'bad'}])
def test_bad_snapshot_never_becomes_a_price(changes):
    assert not parse_tencent_quotes(payload(**changes),['sh.600000'],NOW)


def test_duplicate_partial_and_unrequested_replies_are_not_fabricated():
    assert not parse_tencent_quotes(payload()+payload(),['sh.600000'],NOW)
    assert not parse_tencent_quotes('v_sh600000="x";', ['sh.600000'],NOW)
    assert set(parse_tencent_quotes(payload()+payload('sz000001'),['sh.600000'],NOW))=={'sh.600000'}


def test_batch_limit_and_no_network_retry(monkeypatch):
    codes=[f'sh.{600000+i}' for i in range(101)]
    opener=Mock();opener.open.side_effect=[response(payload().encode('gbk')),TimeoutError('offline')]
    sleeps=[];monkeypatch.setattr('workbench.tencent_quotes.time.sleep',sleeps.append)
    quotes,errors=fetch_tencent_quotes(codes,moment=NOW,opener=opener)
    assert len(opener.open.call_args_list)==2 and len(errors)==100 and len(quotes)==1
    assert len(opener.open.call_args_list[0].args[0].full_url.split('q=')[1].split(','))==100
    assert sleeps==[.5] and opener.open.call_args.kwargs['timeout']==8
    opener.open.reset_mock();opener.open.side_effect=TimeoutError('offline')
    quotes,errors=fetch_tencent_quotes(codes,moment=NOW,opener=opener)
    assert opener.open.call_count==1 and len(errors)==101 and not quotes


def test_oversized_and_cancelled_batch_is_bounded():
    opener=Mock();opener.open.return_value=response(b'x'*(MAX_BODY+1))
    assert fetch_tencent_quotes(['sh.600000'],moment=NOW,opener=opener)[1]
    import threading
    event=threading.Event();event.set();opener.open.reset_mock()
    with pytest.raises(InterruptedError):
        fetch_tencent_quotes(['sh.600000'],opener=opener,cancel_event=event)
    opener.open.assert_not_called()


def test_holding_fallback_only_requests_missing_codes(monkeypatch):
    q=parse_tencent_quotes(payload(),['sh.600000'],NOW)
    monkeypatch.setattr('workbench.tencent_quotes.fetch_tencent_quotes',lambda codes:(q,{'sz.000001':'missing'}))
    monkeypatch.setattr('workbench.holding_quotes.china_now',lambda:NOW)
    calls=[]
    def sina(codes):
        calls.extend(codes)
        return {'sz.000001':{**q['sh.600000'],'source':'sina'}},QuoteFailures()
    monkeypatch.setattr('workbench.holding_quotes.fetch_quotes',sina)
    quotes,failed=fetch_holding_quotes(['sh.600000','sz.000001'])
    assert calls==['sz.000001'] and not failed
    assert quotes['sh.600000']['source']=='tencent' and quotes['sz.000001']['source']=='sina'


def test_stale_tencent_does_not_overwrite_newer_sina_and_failure_keeps_price(monkeypatch):
    old=parse_tencent_quotes(payload(**{'30':'20261009095000'}),['sh.600000'],NOW)
    monkeypatch.setattr('workbench.tencent_quotes.fetch_tencent_quotes',lambda codes:(old,{}))
    monkeypatch.setattr('workbench.holding_quotes.china_now',lambda:NOW)
    fresh={**old['sh.600000'],'source':'sina','quote_time':NOW.isoformat()}
    monkeypatch.setattr('workbench.holding_quotes.fetch_quotes',lambda codes:({'sh.600000':fresh},QuoteFailures()))
    assert fetch_holding_quotes(['sh.600000'])[0]['sh.600000']['source']=='sina'
    monkeypatch.setattr('workbench.holding_quotes.fetch_quotes',lambda codes:({},QuoteFailures(codes)))
    quotes,failed=fetch_holding_quotes(['sh.600000'])
    assert quotes==old and not failed
    selected=select_quotes({},quotes,['sh.600000'],failed,NOW,None)
    assert selected['sh.600000']['state']=='行情滞后'


def test_both_sources_fail_preserves_both_reasons(monkeypatch):
    monkeypatch.setattr('workbench.tencent_quotes.fetch_tencent_quotes',lambda codes:({}, {'sh.600000':'timeout'}))
    monkeypatch.setattr('workbench.holding_quotes.fetch_quotes',lambda codes:({},QuoteFailures(codes,{'sh.600000':'HTTP 403'})))
    quotes,failed=fetch_holding_quotes(['sh.600000'])
    assert not quotes and failed=={'sh.600000'}
    assert 'timeout' in failed.details['sh.600000'] and '403' in failed.details['sh.600000']
