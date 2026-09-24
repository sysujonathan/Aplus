"""Vendor connection in a killable subprocess; parent never waits on vendor sockets."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd


class BaoStock:
    login_timeout = 30
    query_timeout = 60
    logout_timeout = 3

    def __init__(self):
        self.process = None
        self.cancel_event = threading.Event()
        self.on_wait = lambda operation, seconds: None

    def __enter__(self):
        self._start()
        return self

    def _start(self):
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [sys.executable, '-u', '-m', 'workbench.provider_worker'],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        stream, messages = self.process.stdout, self.messages
        def read():
            try:
                for line in stream:
                    messages.put(json.loads(line))
            except Exception as exc:
                messages.put({'error':str(exc)})
            finally:
                messages.put({'error':'行情连接进程已退出'})
                stream.close()
        threading.Thread(target=read,daemon=True).start()
        self._request('login',[],self.login_timeout)

    def _stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdin:
            process.stdin.close()

    def _request(self, operation, args, timeout, cancellable=True):
        started = time.monotonic()
        last_notice = 0
        try:
            if cancellable and self.cancel_event.is_set():
                raise InterruptedError('已停止下载，已保存行情保留')
            self.process.stdin.write(json.dumps({'operation':operation,'args':args})+'\n')
            self.process.stdin.flush()
            while True:
                elapsed = time.monotonic()-started
                if cancellable and self.cancel_event.is_set():
                    raise InterruptedError('已停止下载，已保存行情保留')
                if elapsed >= timeout:
                    raise TimeoutError(f'行情{operation}超过 {timeout} 秒无完整响应，已关闭卡住连接')
                if cancellable and int(elapsed)//5 > last_notice:
                    last_notice = int(elapsed)//5
                    self.on_wait(operation,int(elapsed))
                try:
                    response = self.messages.get(timeout=min(.2,timeout-elapsed))
                except queue.Empty:
                    continue
                if 'error' in response:
                    raise RuntimeError(response['error'])
                return response.get('data')
        except BaseException:
            self._stop()
            raise

    def _query(self, operation, args):
        if self.process is None:
            self._start()
        # A single connection and a small pause avoid bursts against the free service.
        if self.cancel_event.wait(.3):
            self._stop()
            raise InterruptedError('已停止下载，已保存行情保留')
        return pd.DataFrame(self._request(operation,args,self.query_timeout))

    def fetch(self, code, start, end):
        return self._query('fetch',[code,start,end])

    def universe(self, date):
        return self._query('universe',[date])

    def __exit__(self,*exc):
        try:
            if self.process is not None and not self.cancel_event.is_set():
                self._request('logout',[],self.logout_timeout,cancellable=False)
        except Exception:
            pass
        finally:
            self._stop()
