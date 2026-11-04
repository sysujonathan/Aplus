"""Private CPU worker. No Store instance, database access or vendor connection."""
import contextlib
import json
from pathlib import Path
import sys


class BuiltinsOnly:
    def rows(self, sql):
        if sql != 'SELECT * FROM registry ORDER BY created':
            raise ValueError('扫描计算进程不允许读取数据库')
        return []


def compute(request, entries):
    from .market import load_dataset
    from .strategies import prepare, prepare_indicators, signal_at_end

    # Reuse the original reader, including path, SHA and OHLCV validation;
    # this in-memory facade has no SQLite connection or write methods.
    class Snapshot:
        root = Path(request['root']).resolve()

        def read_artifact(self, relative, job=None):
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError('不允许越过运行目录读取文件')
            return path.read_bytes()

    record = request['record']
    frame, _ = load_dataset(Snapshot(), record['id'], record=record)
    frame = prepare(frame, request['timeframe'], request['asof'])
    frame.attrs['code'] = record['code']
    selected = []
    for item in request['strategies']:
        spec = entries[item['id']]
        if (spec.version != item['version'] or spec.params != item['params']
                or spec.state != 'active' or request['timeframe'] not in spec.timeframes):
            raise ValueError('扫描计算进程策略版本或参数不一致')
        selected.append(spec)
    result = dict(dataset=record['id'], sha256=record['sha256'], answers={})
    try:
        enriched = prepare_indicators(frame)
    except Exception as exc:
        result['prepare_error'] = str(exc)
        return result
    for spec in selected:
        try:
            result['answers'][spec.id] = dict(signal=signal_at_end(spec, frame, prepared=enriched))
        except Exception as exc:
            # Business errors stay per stock/strategy, in the parent's order.
            result['answers'][spec.id] = dict(error=str(exc))
    return result


def main():
    from .strategies import catalog

    with contextlib.redirect_stdout(sys.stderr):
        entries = catalog(BuiltinsOnly())  # Verify frozen bytes once per worker.
    for line in sys.stdin:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                response = dict(result=compute(json.loads(line), entries))
            encoded = json.dumps(response, ensure_ascii=True, allow_nan=False, default=str)
        except Exception as exc:
            encoded = json.dumps(dict(error=f'{type(exc).__name__}: {exc}'), ensure_ascii=True)
        sys.stdout.write(encoded + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
