"""Launch the local-only UI, reusing a healthy instance on the chosen port."""
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from workbench.store import resolve_runtime_root

ROOT = Path(__file__).resolve().parent
PORT = 8517
URL = f'http://127.0.0.1:{PORT}'


def healthy():
    try:
        with urllib.request.urlopen(URL+'/_stcore/health',timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def main():
    runtime = resolve_runtime_root()
    runtime.mkdir(parents=True, exist_ok=True)
    if healthy():
        record = runtime/'server.json'
        if not record.exists() or json.loads(record.read_text(encoding='utf-8')).get('root') != str(ROOT):
            raise RuntimeError('Port 8517 is already in use by another instance. Stop that instance first.')
    else:
        flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        with (runtime/'startup.log').open('a',encoding='utf-8') as out:
            process = subprocess.Popen([sys.executable,'-m','streamlit','run',str(ROOT/'app.py'),
                                        '--server.address=127.0.0.1',f'--server.port={PORT}'],
                                       cwd=ROOT,stdout=out,stderr=out,creationflags=flags)
        (runtime/'server.json').write_text(json.dumps({'pid':process.pid,'root':str(ROOT),'port':PORT}),encoding='utf-8')
        for _ in range(40):
            if healthy():
                break
            if process.poll() is not None:
                raise RuntimeError('A failed to start. See runtime/startup.log; port 8517 may be occupied.')
            time.sleep(0.5)
        else:
            raise RuntimeError('A startup timed out. See runtime/startup.log.')
    if '--no-browser' not in sys.argv:
        webbrowser.open(URL)
    print(URL)


if __name__=='__main__':
    main()
