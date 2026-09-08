"""Only fixed counters leave the runtime; no sessions, request bodies or headers."""
import http.client
import json
import socket
from salla_http_simulator import COUNTER_KEYS, CLASS_KEYS


CHECKPOINTS = ('BEFORE_REVIEW', 'AFTER_LOGIN', 'BEFORE_IMAGES', 'AFTER_IMAGES',
               'BEFORE_COMPLETE', 'AFTER_COMPLETE', 'AFTER_REVIEW')


def validate_counts(counters):
    if type(counters) is not dict or set(counters) != set(COUNTER_KEYS) | {'unexpected_by_class'}:
        raise RuntimeError('EVIDENCE_SCHEMA_REJECTED')
    if any(type(counters[k]) is not int or not 0 <= counters[k] <= 1000 for k in COUNTER_KEYS):
        raise RuntimeError('EVIDENCE_SCHEMA_REJECTED')
    classes = counters['unexpected_by_class']
    if (type(classes) is not dict or not set(classes) <= CLASS_KEYS
            or any(type(v) is not int or not 0 < v <= 1000 for v in classes.values())
            or sum(classes.values()) != counters['unexpected']):
        raise RuntimeError('EVIDENCE_SCHEMA_REJECTED')
    return counters


def protocol_lines(checkpoint, counters):
    if checkpoint not in CHECKPOINTS:
        return ['EVIDENCE AFTER_REVIEW unavailable']
    try:
        validate_counts(counters)
    except Exception:
        return ['EVIDENCE ' + checkpoint + ' unavailable']
    lines = ['EVIDENCE ' + checkpoint + ' ' + k + ' ' + str(counters[k]) for k in COUNTER_KEYS]
    lines.extend('EVIDENCE ' + checkpoint + ' ' + k.replace('|', ' ') + ' ' + str(v)
                 for k, v in sorted(counters['unexpected_by_class'].items()))
    return lines


def evidence():
    if {name for _, name in socket.if_nameindex()} != {'lo'}:
        raise RuntimeError('EVIDENCE_NETWORK_REJECTED')
    connection = http.client.HTTPConnection('127.0.0.1', 8093, timeout=2)
    try:
        connection.request('GET', '/__fixture__/counts')
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('EVIDENCE_HTTP_REJECTED')
        counters = json.loads(response.read(32768))
    finally:
        connection.close()
    validate_counts(counters)
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
