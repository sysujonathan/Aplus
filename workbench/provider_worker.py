"""Private worker: network and parsing only; never writes application data."""
import contextlib
import json
import sys


def main():
    from .market import DirectBaoStock
    vendor = sys.argv[1] if len(sys.argv)>1 else 'baostock'
    if vendor == 'tickflow':
        from .tickflow import TickFlowHTTP
        provider = TickFlowHTTP()
    elif vendor == 'tencent':
        from .tencent import TencentHTTP
        provider = TencentHTTP()
    else:
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
                elif vendor == 'tickflow' and operation in {'batch','instruments'}:
                    result = getattr(provider,operation)(*request['args'])
                elif vendor == 'tencent' and operation in {'history_page','directory_count','directory_page'}:
                    result = getattr(provider,operation)(*request['args'])
                elif operation in {'fetch','universe','calendar','basics'} or (vendor == 'baostock' and operation == 'trading_status'):
                    frame = getattr(provider,operation)(*request['args'])
                    result = frame.to_dict(orient='records')
                    if operation == 'fetch':
                        result = {'rows': result, 'evidence': frame.attrs}
                else:
                    raise ValueError('Unknown provider operation')
            response = {'data':result}
        except Exception as exc:
            from .provider_guard import ProviderError
            response = {'error': exc.detail() if isinstance(exc,ProviderError) else
                        {'source':vendor,'operation':operation,'error':str(exc)}}
        sys.stdout.write(json.dumps(response,ensure_ascii=True)+'\n')
        sys.stdout.flush()
        if operation == 'logout':
            break


if __name__ == '__main__':
    main()
