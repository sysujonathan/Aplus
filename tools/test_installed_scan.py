"""Real windowed EXE CPU-worker IPC; generated positive H2, no network/user data."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(application, folder):
    import pandas as pd
    from workbench.market import save_dataset
    from workbench.scan_parallel import CPUWorker, OrderedComputations
    from workbench.store import Store, dumps
    from workbench.strategies import catalog, signal_at_end

    folder.mkdir(parents=True, exist_ok=False)
    store = Store(folder / '中文样本 runtime')
    frame = pd.DataFrame(dict(date=pd.bdate_range('2025-01-01', periods=130).strftime('%Y-%m-%d'),
                              open=9., high=10., low=6., close=9., volume=1000.))
    for index, (high, low) in enumerate([(12., 10.8), (11.8, 10.6), (11.9, 10.7), (11.6, 10.5)], 126):
        frame.loc[index, ['open', 'high', 'low', 'close']] = [low + .1, high, low, high - .1]
    frame.attrs['code'] = 'sh.600000'
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    record = store.rows('SELECT * FROM datasets WHERE id=?', (did,))[0]
    spec = catalog(store)['STRATEGY_GAP_H2']
    request = dict(root=str(store.root), record=record, asof=frame.date.iloc[-1], timeframe='daily',
                   strategies=[dict(id=spec.id, version=spec.version, params=spec.params)])
    before = (store.root / 'workbench.sqlite3').read_bytes()
    with patch.dict(os.environ, LOCALAPPDATA=str(folder / 'local'),
                    APLUS_INSTALL_STATE=str(folder / 'state'), A_WORKBENCH_HOME=str(store.root)), \
            patch('workbench.scan_parallel.scan_command', lambda: [str(application), '--scan-worker']):
        worker = CPUWorker()
        try:
            answer = worker.compute(request, threading.Event())['answers'][spec.id]['signal']
            assert answer['pending_state'] == 'PENDING'
            assert dumps(answer) == dumps(signal_at_end(spec, frame))
            # Chinese business diagnostics prove UTF-8 windowed pipe recovery.
            request['strategies'][0]['version'] = 'invalid'
            try:
                worker.compute(request, threading.Event())
            except RuntimeError as exc:
                assert '策略版本或参数不一致' in str(exc)
            else:
                raise AssertionError('worker accepted changed strategy')
        finally:
            worker.close()
        request['strategies'][0]['version'] = spec.version
        pool = OrderedComputations([(index, request) for index in range(2)], 2, threading.Event())
        try:
            for index in range(2):
                assert dumps(pool.take(index)['answers'][spec.id]['signal']) == dumps(answer)
        finally:
            pool.close()
        assert len(pool.processes) == 2 and all(item.process.poll() is not None for item in pool.processes)
    assert (store.root / 'workbench.sqlite3').read_bytes() == before
    assert worker.process.poll() is not None
    proof = dict(windowed_exe_ipc=True, h2_pending_payload_equal=True, utf8_errors=True,
                 source_database_unchanged=True, child_terminated=True, parallel_exe_processes=2,
                 no_network=True)
    (folder / 'result.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('application', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    run(args.application.resolve(), (args.output or ROOT / '.test-installer' /
                                    ('scan-' + uuid.uuid4().hex[:8])).resolve())
