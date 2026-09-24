"""Private worker: network and parsing only; never writes application data."""
import contextlib
import json
import sys


def main():
    from .market import DirectBaoStock
    provider = DirectBaoStock()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            operation = request['operation']
            with contextlib.redirect_stdout(sys.stderr):
                if operation == 'login':
                    provider.__enter__()
                    result = None
                elif operation == 'logout':
                    provider.__exit__()
                    result = None
                elif operation in {'fetch','universe'}:
                    result = getattr(provider,operation)(*request['args']).to_dict(orient='records')
                else:
                    raise ValueError('Unknown provider operation')
            response = {'data':result}
        except Exception as exc:
            response = {'error':str(exc)}
        sys.stdout.write(json.dumps(response,ensure_ascii=True)+'\n')
        sys.stdout.flush()
        if operation == 'logout':
            break


if __name__ == '__main__':
    main()
