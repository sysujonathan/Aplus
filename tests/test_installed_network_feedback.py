"""A transport failure must not look like a successful old-version update."""
from urllib.error import HTTPError, URLError
import pytest
from workbench.installed_update import check_update, network_failure_message


def limited(reset='3600', remaining='0', code=403):
    return HTTPError('https://api.github.com/repos/sysujonathan/Aplus/releases/latest',
                     code,'rate limit exceeded',{'X-RateLimit-Remaining':remaining,
                     'X-RateLimit-Reset':reset},None)


def test_rate_limit_shows_server_reset_time_in_china_timezone():
    text=network_failure_message(limited(),now=0)
    assert '09:00:00' in text and '反复点击' in text


@pytest.mark.parametrize('reset',['bad','-1','999999999999999999999','nan','inf'])
def test_invalid_or_unreasonable_reset_never_invents_recovery_time(reset):
    text=network_failure_message(limited(reset),now=0)
    assert 'GitHub' in text and '预计' not in text


def test_non_rate_403_is_not_misreported_as_quota():
    assert network_failure_message(limited(remaining='10')) is None


@pytest.mark.parametrize('error',[TimeoutError('read timed out'),URLError(TimeoutError('read timed out'))])
def test_real_timeout_is_explained_without_exposing_english_transport_error(error):
    assert '超时' in network_failure_message(error)


def test_checking_does_not_call_failure_latest_or_available():
    calls=[]
    def api(path):calls.append(path);raise limited()
    result=check_update({'version':'2026.10.31.2'},api=api)
    assert result['state']=='unavailable' and '暂时限制' in result['message']
    assert '当前版本可继续使用' in result['message'] and 'target' not in result
    assert calls==['/releases/latest']  # No retry storm or relaxed verification.
