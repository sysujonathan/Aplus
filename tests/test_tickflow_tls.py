"""TickFlow uses a safe, isolated CA bundle without bypassing TLS or recovery rules."""
import hashlib
import json
import ssl
import urllib.error
import urllib.request
from types import SimpleNamespace
from pathlib import Path

import certifi
import pytest

from tests.test_tickflow_recovery import clock, fake_service
from tests.test_workbench import store
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.tickflow import TickFlow, TickFlowHTTP, UNSAFE_CA_SHA256
from workbench.tickflow_recovery import request_with_recovery


UNSAFE_ROOT = Path(__file__).parent / 'fixtures' / 'tls' / 'emsign-ecc-root-g3.pem'


@pytest.mark.parametrize('stale_system_trust', [False, True])
def test_client_uses_only_safe_bundle_even_with_stale_system_and_environment_trust(
        monkeypatch, stale_system_trust):
    real_factory = ssl.create_default_context
    stale_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if stale_system_trust:
        stale_context.load_verify_locations(cafile=str(UNSAFE_ROOT))
        assert {hashlib.sha256(d).hexdigest() for d in stale_context.get_ca_certs(binary_form=True)} == UNSAFE_CA_SHA256
    bundled_roots = set(real_factory(cafile=certifi.where()).get_ca_certs(binary_form=True))
    original_global_factory = ssl._create_default_https_context
    factory_calls, handlers = [], []
    build_opener = urllib.request.build_opener

    def default_context(*args, **kwargs):
        factory_calls.append((args, kwargs))
        return real_factory(*args, **kwargs) if kwargs.get('cafile') else stale_context

    def opener(*args):
        handlers.extend(args)
        return build_opener(*args)

    monkeypatch.setattr('workbench.tickflow.ssl.create_default_context', default_context)
    monkeypatch.setattr('workbench.tickflow.urllib.request.build_opener', opener)
    monkeypatch.setenv('https_proxy', 'http://proxy.invalid:1234')
    monkeypatch.setenv('SSL_CERT_FILE', str(UNSAFE_ROOT))
    provider = TickFlowHTTP()
    assert factory_calls == [((), {'cafile': certifi.where()})]
    assert isinstance(provider.opener, urllib.request.OpenerDirector)
    assert next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler)).proxies == {}
    context = next(h for h in handlers if isinstance(h, urllib.request.HTTPSHandler))._context
    assert context is not stale_context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    current_roots = set(context.get_ca_certs(binary_form=True))
    assert bundled_roots and current_roots == bundled_roots
    assert not {hashlib.sha256(d).hexdigest() for d in current_roots} & UNSAFE_CA_SHA256
    assert ssl._create_default_https_context is original_global_factory


@pytest.mark.parametrize('version', ['2026.07.22', '2026.10.18', '2026.10.19', 'unknown', None])
@pytest.mark.parametrize('client', [TickFlowHTTP, TickFlow])
def test_old_or_unknown_actual_dependency_is_rejected_before_network(monkeypatch, version, client):
    monkeypatch.setattr(certifi, '__version__', version)
    monkeypatch.setattr('workbench.tickflow.ssl.create_default_context',
                        lambda **_: pytest.fail('Old dependencies must not construct TLS'))
    with pytest.raises(ProviderError, match='certifi>=2026.10.23') as caught:
        client()
    assert caught.value.source == 'tickflow' and caught.value.operation == 'tls'


def test_contaminated_bundle_is_blocked_even_with_current_version(tmp_path, monkeypatch):
    # Public CA certificate only; no private key or compromised leaf is shipped.
    bad = UNSAFE_ROOT.read_text(encoding='ascii')
    assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(bad)).hexdigest() in UNSAFE_CA_SHA256
    bundle = tmp_path / 'contaminated.pem'
    bundle.write_text(certifi.contents() + '\n' + bad, encoding='ascii')
    monkeypatch.setattr(certifi, 'where', lambda: str(bundle))
    monkeypatch.setattr('workbench.tickflow.urllib.request.build_opener',
                        lambda *_: pytest.fail('Unsafe context must not reach a network opener'))
    with pytest.raises(ProviderError, match='已撤销信任'):
        TickFlowHTTP()


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
