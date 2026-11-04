"""Desktop Google consent and Windows-user protected credentials (never in Store)."""
from __future__ import annotations

import base64
import ctypes
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
import webbrowser

SCOPE = 'https://www.googleapis.com/auth/drive'
_UNSET = object()
_SESSION_LOCKS = {}
_SESSION_LOCKS_GUARD = threading.Lock()


class SessionChanged(ValueError):
    def __init__(self):
        super().__init__('账号已在其他窗口切换或退出，请重新打开当前内容')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a bearer token to a supplied redirect URL.


def request(url, *, data=None, headers=None, method=None, limit=1024 * 1024):
    try:
        with build_opener(NoRedirect()).open(Request(url, data=data, headers=headers or {}, method=method), timeout=30) as response:
            value = response.read(limit + 1)
            if len(value) > limit:
                raise ValueError('云端响应超过安全上限')
            return value, dict(response.headers)
    except HTTPError as exc:
        # Do not expose server bodies/authorization URLs or credentials to UI logs.
        raise DriveError(exc.code) from None
    except (URLError, TimeoutError, OSError):
        raise ConnectionError('无法连接 Google Drive，请检查网络后重试；尚未确认同步成功') from None


class DriveError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__({401: 'Google 授权已失效，请重新连接', 403: 'Google Drive 权限不足，请检查目录共享及 API 授权',
                          404: '云端文件不存在或已失去访问权限', 409: '云端记录已存在，正在核对原提交'}.get(status, f'Google Drive 请求失败（{status}），请重试'))


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Google 凭证与个人学习记录目前仅支持 Windows 安全存储')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), 'Aplus PA', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ValueError('Windows 安全存储无法读取或写入；请重新登录，不会覆盖原个人记录')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def atomic_secret(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class GoogleSession:
    def __init__(self, profile=None):
        profile_id = hashlib.sha256(os.path.normcase(str(Path(profile).resolve()) if profile else 'default').encode()).hexdigest()[:24]
        self.path = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Aplus' / 'PA-Google' / profile_id / 'session.dpapi'
        self.value = None
        self._operation = threading.local()

    @contextmanager
    def locked(self):
        # One short credential-file critical section across windows/processes.
        # Never hold this lock while waiting for Google or browser consent.
        key = os.path.normcase(str(self.path.resolve()))
        with _SESSION_LOCKS_GUARD:
            lock = _SESSION_LOCKS.setdefault(key, threading.RLock())
        with lock:
            import msvcrt
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix('.lock').open('a+b') as stream:
                if stream.seek(0, 2) == 0:
                    stream.write(b'0')
                    stream.flush()
                deadline = time.monotonic() + 10
                while True:
                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ValueError('登录记录正被其他窗口使用，请稍后重试') from None
                        time.sleep(.02)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def _read(self):
        if not self.path.exists():
            return None, None
        if self.path.stat().st_size > 128 * 1024:
            raise ValueError('Google 登录记录损坏，请重新连接')
        raw = self.path.read_bytes()
        return json.loads(protect(raw, True)), hashlib.sha256(raw).hexdigest()

    @staticmethod
    def generation(value):
        if not value:
            return None
        # Legacy credentials have no generation; token renewal must not look
        # like a new login. Never expose the refresh token itself.
        return value.get('session_id') or hashlib.sha256(json.dumps(
            [value.get('user'), value.get('client_id'), value.get('refresh_token')], sort_keys=True).encode()).hexdigest()

    def _check(self, value):
        expected = getattr(self._operation, 'generation', _UNSET)
        if expected is not _UNSET and expected != self.generation(value):
            raise SessionChanged()

    @contextmanager
    def operation(self):
        nested = hasattr(self._operation, 'generation')
        if not nested:
            self._operation.generation = self.generation(self.load())
        try:
            yield
            self.load()  # Discard in-flight results from a superseded login.
        finally:
            if not nested:
                del self._operation.generation

    def load(self):
        with self.locked():
            value, _ = self._read()
            self._check(value)
            self.value = value
        return value

    def save(self, value, expected=_UNSET):
        with self.locked():
            current, revision = self._read()
            if expected is not _UNSET and revision != expected:
                raise SessionChanged()
            self._check(current)
            if expected is _UNSET:
                value = dict(value, session_id=secrets.token_hex(24))
            atomic_secret(self.path, protect(json.dumps(value).encode()))
            self.value = value

    def logout(self):
        with self.locked():
            self.path.unlink(missing_ok=True)
            self.value = None

    def access_token(self):
        with self.locked():
            value, revision = self._read()
            self._check(value)
        if not value:
            raise ValueError('请先连接 Google Drive')
        if value.get('expires', 0) < time.time() + 60:
            try:
                raw, _ = request('https://oauth2.googleapis.com/token', data=urlencode({
                    'client_id': value['client_id'], 'client_secret': value['client_secret'],
                    'refresh_token': value['refresh_token'], 'grant_type': 'refresh_token'}).encode())
            except DriveError as exc:
                if exc.status in (400, 401):
                    raise DriveError(401) from None
                raise
            token = json.loads(raw)
            value = dict(value, access_token=token['access_token'], expires=time.time() + token['expires_in'])
            try:
                self.save(value, expected=revision)
            except SessionChanged:
                latest = self.load()
                if (self.generation(latest) != self.generation(value)
                        or not latest or latest.get('expires', 0) < time.time() + 60):
                    raise
                # Another window renewed the same login first. Reuse its
                # committed token instead of overwriting it or clearing UI.
                return latest['access_token']
        else:
            # Even cached tokens are tied to the current on-disk session.
            with self.locked():
                current, latest = self._read()
                self._check(current)
                if latest != revision:
                    raise SessionChanged()
        return value['access_token']

    def login(self, client_path, *, consent_timeout=180):
        with self.locked():
            _, revision = self._read()
        path = Path(client_path)
        if path.stat().st_size > 128 * 1024:
            raise ValueError('OAuth 客户端文件无效')
        config = json.loads(path.read_text(encoding='utf-8-sig')).get('installed', {})
        if not config.get('client_id', '').endswith('.apps.googleusercontent.com') or not config.get('client_secret'):
            raise ValueError('请选择 Google Cloud 下载的桌面应用 OAuth JSON')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        outcome = {}
        class CallbackServer(HTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                # An unrelated loopback connection must not hold the consent
                # thread forever without sending an HTTP request.
                connection.settimeout(2)
                return connection, address
        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                parsed = urlparse(self.path)
                args = parse_qs(parsed.query)
                if parsed.path != '/callback' or args.get('state') != [state]:
                    self.send_error(400)
                    return
                outcome.update(code=(args.get('code') or [''])[0], error=bool(args.get('error')))
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write('授权已返回 Aplus，可以关闭此页。'.encode())
        with CallbackServer(('127.0.0.1', 0), Callback) as server:
            server.timeout = 1
            redirect = f'http://127.0.0.1:{server.server_port}/callback'
            url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
                'client_id': config['client_id'], 'redirect_uri': redirect, 'response_type': 'code', 'scope': SCOPE,
                'access_type': 'offline', 'prompt': 'consent select_account', 'state': state,
                'code_challenge': challenge, 'code_challenge_method': 'S256'})
            if not webbrowser.open(url):
                raise ValueError('无法打开系统浏览器，请检查默认浏览器后重试')
            deadline = time.monotonic() + consent_timeout
            while not outcome and time.monotonic() < deadline:
                server.handle_request()
        if not outcome.get('code') or outcome.get('error'):
            raise ValueError('Google 授权已取消或超时')
        raw, _ = request('https://oauth2.googleapis.com/token', data=urlencode({
            'client_id': config['client_id'], 'client_secret': config['client_secret'],
            'code': outcome['code'], 'code_verifier': verifier, 'redirect_uri': redirect,
            'grant_type': 'authorization_code'}).encode())
        token = json.loads(raw)
        if not token.get('refresh_token') or SCOPE not in token.get('scope', '').split():
            raise ValueError('Google 未授予所需 Drive 访问，请重新授权')
        # Identity is established by Drive about.get, never by browser-supplied names.
        value = dict(client_id=config['client_id'], client_secret=config['client_secret'],
                     refresh_token=token['refresh_token'], access_token=token['access_token'],
                     expires=time.time() + token['expires_in'])
        raw, _ = request('https://www.googleapis.com/drive/v3/about?fields=user',
                         headers={'Authorization': 'Bearer ' + token['access_token']})
        user = json.loads(raw)['user']
        if not user.get('permissionId'):
            raise ValueError('无法确认 Google 用户身份')
        value['user'] = {'id': user['permissionId'], 'name': user.get('displayName', 'Google user')}
        value['session_id'] = secrets.token_hex(24)
        self.save(value, expected=revision)
        return value['user']
