"""Only fixed counters leave the runtime; no sessions, request bodies or headers."""
import http.client
import json
import socket


def evidence():
    if {name for _, name in socket.if_nameindex()} != {'lo'}:
        raise RuntimeError('EVIDENCE_NETWORK_REJECTED')
    connection = http.client.HTTPConnection('127.0.0.1', 8093, timeout=2)
    try:
        connection.request('GET', '/__fixture__/counts')
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('EVIDENCE_HTTP_REJECTED')
        counters = json.loads(response.read(2048))
    finally:
        connection.close()
    keys = {'simulated_provider_calls', 'unexpected', 'status_writes', 'denied', 'shipping_attempted', 'shipping_failed'}
    if set(counters) != keys or any(type(v) is not int or not 0 <= v <= 1000 for v in counters.values()):
        raise RuntimeError('EVIDENCE_SCHEMA_REJECTED')
    # Runtime container is independently required to share network=none Mongo's
    # namespace; no external interface or published port exists.
    return {'live_provider_calls': 0, **counters}


def main():
    try:
        counters = evidence()
    except BaseException:
        # Fixed marker; neither traceback nor potentially hostile response data.
        print('SIMULATOR_EVIDENCE unavailable')
        return 1
    print('SIMULATOR_EVIDENCE ' + json.dumps(counters, sort_keys=True))
    if counters['unexpected']:
        print('FAIL SIMULATOR_EVIDENCE UNEXPECTED_REQUESTS')
        return 2  # Measured evidence, but never successful acceptance.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
