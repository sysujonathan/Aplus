"""TickFlow supplements trust locally without bypassing TLS or recovery rules."""
import json
import ssl
import urllib.error
import urllib.request
from types import SimpleNamespace

import certifi
import pytest

from tests.test_tickflow_recovery import clock, fake_service
from tests.test_workbench import store
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.tickflow import TickFlowHTTP
from workbench.tickflow_recovery import request_with_recovery


@pytest.mark.parametrize('system_trust', [False, True])
def test_client_adds_bundle_to_system_trust_and_keeps_strict_local_https(
        monkeypatch, system_trust):
    context = (ssl.create_default_context() if system_trust else
               ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    previous_roots = set(context.get_ca_certs(binary_form=True))
    bundled_roots = set(ssl.create_default_context(cafile=certifi.where()).get_ca_certs(binary_form=True))
    original_global_factory = ssl._create_default_https_context
    factory_calls, handlers = [], []
    build_opener = urllib.request.build_opener

    def default_context(*args, **kwargs):
        factory_calls.append((args, kwargs))
        return context

    def opener(*args):
        handlers.extend(args)
        return build_opener(*args)

    monkeypatch.setattr('workbench.tickflow.ssl.create_default_context', default_context)
    monkeypatch.setattr('workbench.tickflow.urllib.request.build_opener', opener)
    monkeypatch.setenv('https_proxy', 'http://proxy.invalid:1234')
    provider = TickFlowHTTP()
    assert factory_calls == [((), {})]
    assert isinstance(provider.opener, urllib.request.OpenerDirector)
    assert next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler)).proxies == {}
    assert next(h for h in handlers if isinstance(h, urllib.request.HTTPSHandler))._context is context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    current_roots = set(context.get_ca_certs(binary_form=True))
    assert bundled_roots and bundled_roots <= current_roots
    assert previous_roots <= current_roots
    assert ssl._create_default_https_context is original_global_factory


@pytest.mark.parametrize('bundle_kind', ['missing', 'malformed'])
def test_unusable_ca_bundle_fails_closed_without_creating_an_opener(
        tmp_path, monkeypatch, bundle_kind):
    path = tmp_path / 'cacert.pem'
    if bundle_kind == 'malformed':
        path.write_text('not a certificate', encoding='ascii')
    monkeypatch.setattr('workbench.tickflow.certifi.where', lambda: str(path))
    monkeypatch.setattr('workbench.tickflow.urllib.request.build_opener',
                        lambda *_: pytest.fail('No fallback opener may be created'))
    with pytest.raises((OSError, ssl.SSLError)):
        TickFlowHTTP()


@pytest.mark.parametrize('reason', [
    'unable to get local issuer certificate',
    'certificate has expired',
    'Hostname mismatch',
    'self-signed certificate',
])
def test_invalid_certificate_preserves_error_and_cooldown_without_retry(
        store, clock, monkeypatch, reason):
    provider, calls = TickFlowHTTP(), []
    error = urllib.error.URLError(ssl.SSLCertVerificationError(reason))

    def failed(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    provider.opener = SimpleNamespace(open=failed)
    monkeypatch.setattr('workbench.tickflow.time.sleep', lambda *_: None)
    service, messages = fake_service(store, clock)
    with pytest.raises(ProviderError, match=reason) as caught:
        request_with_recovery(service, 'job', lambda: provider.instruments('SH'),
                              {'used': False}, {})
    assert len(calls) == 1 and calls[0][1]['timeout'] == 15
    assert caught.value.__cause__ is error
    assert caught.value.source == 'tickflow' and not caught.value.transient_transport
    assert messages == [] and clock.elapsed == 0
    state = ProviderGuard(store, 'tickflow', clock.time).state()
    assert state['until'] > clock.time() and reason in state['last_error']['message']
    ProviderGuard(store, 'baostock', clock.time).check()


def test_verified_connection_keeps_request_and_response_contract(monkeypatch):
    provider = TickFlowHTTP()
    payload = [{'symbol': '600000.SH', 'name': 'sample'}]
    calls, waits = [], []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, limit):
            assert limit == 64 * 1024 * 1024 + 1
            return json.dumps({'data': payload}).encode()

    def opened(request, **kwargs):
        calls.append((request, kwargs))
        return Response()

    provider.opener = SimpleNamespace(open=opened)
    monkeypatch.setattr('workbench.tickflow.time.sleep', waits.append)
    assert provider.instruments('SH') == payload
    assert len(calls) == 1 and len(waits) == 1
    request, kwargs = calls[0]
    assert request.full_url == 'https://free-api.tickflow.org/v1/exchanges/SH/instruments?type=stock'
    assert kwargs == {'timeout': 15}
    assert request.get_header('Accept') == 'application/json'
