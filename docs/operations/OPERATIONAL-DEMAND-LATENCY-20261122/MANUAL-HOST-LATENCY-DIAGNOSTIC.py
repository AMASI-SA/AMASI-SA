"""Bounded read-only metadata and sanitized timing samples from the actual host.

No endpoint calls, demand/worker wake, build, index creation or data writes.
Host observations alone do not prove that a local database/log is Production.
"""
import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path('/app')
EXPECTED_HEAD = '24270ebdfdebb83282389308a2349cdb27e2b8e4'
MAX_LOG_BYTES = 2 * 1024 * 1024
NUMBERS = frozenset({
    'pid', 'wall_ms', 'build_ms', 'materialize_ms', 'check_ms', 'check_calls',
    'authorize_ms', 'process_cpu_ms', 'peak_rss_kib', 'queue_ms',
    'mongo_driver_command_p50_ms', 'mongo_driver_command_p95_ms',
    'mongo_driver_command_p99_ms', 'mongo_driver_checkout_p95_ms',
    'mongo_driver_command_timeouts', 'mongo_driver_checkout_timeouts',
})
FAMILIES = frozenset({
    'operational_home', 'operational_ledger', 'operational_register',
    'operational_statement', 'operational_history', 'operational_directory',
    'operational_selection', 'operational_options', 'operational_position',
    'operational_resource', 'operational_report', 'dashboard_card', 'assembly_board',
})
OUTCOMES = frozenset({'ready', 'failed', 'paused', 'cancelled', 'claim_lost'})


def show(label, value):
    print(label, json.dumps(value, allow_nan=False, sort_keys=True), flush=True)


def safe_metric(value):
    if (not isinstance(value, dict) or not isinstance(value.get('family'), str)
            or value['family'] not in FAMILIES or not isinstance(value.get('outcome'), str)
            or value['outcome'] not in OUTCOMES
            or not set(value) <= NUMBERS | {'family', 'outcome'}):
        return None
    for key, number in value.items():
        if key in NUMBERS and (type(number) not in (int, float)
                or not math.isfinite(number) or not 0 <= number <= 10 ** 12):
            return None
    return value


def timestamp(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def elapsed_ms(end, start):
    end, start = timestamp(end), timestamp(start)
    return round((end - start).total_seconds() * 1000, 1) if end and start and end >= start else None


def metadata(row, now):
    if (not isinstance(row.get('family'), str) or row['family'] not in FAMILIES
            or not isinstance(row.get('status'), str)
            or row['status'] not in {'ready', 'running', 'queued', 'failed'}):
        return None
    result = {key: row[key] for key in ('family', 'status')}
    attempts = row.get('attempts')
    if type(attempts) is int and 0 <= attempts <= 1000:
        result['attempts'] = attempts
    requested, started, finished = (timestamp(row.get(key)) for key in (
        'requested_at', 'started_at', 'finished_at'))
    current_start = bool(requested and started and started >= requested and row['status'] != 'queued')
    current_ready = bool(current_start and row['status'] == 'ready' and finished and finished >= started)
    fields = ['requested_at']
    if current_start:
        fields.append('started_at')
    if current_ready:
        fields.extend(('finished_at', 'ready_until'))
    for key in fields:
        value = timestamp(row.get(key))
        if value:
            result[key] = value.isoformat()
    # A requeued document can retain timestamps of an older generation.
    result['queue_ms'] = elapsed_ms(started, requested) if current_start else None
    result['start_to_ready_ms'] = elapsed_ms(finished, started) if current_ready else None
    result['requested_age_ms'] = elapsed_ms(now, row.get('requested_at'))
    result['active_consumers'] = sum(
        1 for item in row.get('consumers', [])
        if isinstance(item, dict) and timestamp(item.get('until'))
        and timestamp(item['until']) > now
    )
    return result


def inspect_logs():
    samples = []
    available = 0
    # Conventional host paths are candidates, not asserted platform evidence.
    for path in (Path('/var/log/supervisor/backend.out.log'),
                 Path('/var/log/supervisor/backend.err.log')):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                available += 1
                stream.seek(max(0, info.st_size - MAX_LOG_BYTES))
                data = stream.read(MAX_LOG_BYTES).decode('utf-8', errors='replace')
        except OSError:
            continue
        for line in data.splitlines():
            for marker in ('demand_read_sample ', 'mezan_demand_read_metrics '):
                if marker not in line:
                    continue
                raw = line.split(marker, 1)[1]
                if len(raw) > 2048:
                    continue
                try:
                    value = safe_metric(json.loads(raw))
                except (ValueError, TypeError, OverflowError):
                    value = None
                if value is not None:
                    samples.append(value)
    show('HOST_LOG_SAMPLES', {'files_available': available, 'samples': samples[-24:],
         'production_scope_verified': False, 'sample_times_not_included': True})


def inspect_database():
    from dotenv import dotenv_values
    from pymongo import MongoClient
    values = dotenv_values(ROOT / 'backend/.env')
    uri = os.environ.get('MONGO_URL') or values.get('MONGO_URL')
    name = os.environ.get('DB_NAME') or values.get('DB_NAME')
    if not uri or not name:
        show('HOST_JOB_METADATA', {'available': False, 'reason': 'connection_settings_missing'})
        return
    client = None
    try:
        client = MongoClient(uri, maxPoolSize=1, minPoolSize=0,
            serverSelectionTimeoutMS=3000, connectTimeoutMS=2000,
            socketTimeoutMS=3000, appname='mezan-read-latency-diagnostic')
        jobs = client[name]['mezan_demand_read_jobs_v1']
        projection = {'_id': 0, 'family': 1, 'status': 1, 'attempts': 1,
            'requested_at': 1, 'started_at': 1, 'finished_at': 1,
            'ready_until': 1, 'consumers.until': 1}
        now = datetime.now(timezone.utc)
        results = []
        for state in ('ready', 'running', 'queued', 'failed'):
            cursor = jobs.find({'code_version': 'demand-read-v1', 'status': state}, projection)
            cursor = cursor.hint('demand_read_pending').sort('requested_at', -1).limit(6).max_time_ms(1500)
            try:
                for row in cursor:
                    value = metadata(row, now)
                    if value is not None:
                        results.append(value)
            finally:
                cursor.close()
        show('HOST_JOB_METADATA', {'available': True, 'observed_at': now.isoformat(),
             'production_scope_verified': False, 'maximum_rows': 24, 'rows': results})
    except Exception as error:
        # Never print database names, connection strings, payloads or error text.
        show('HOST_JOB_METADATA', {'available': False, 'error_type': type(error).__name__})
    finally:
        if client is not None:
            client.close()


def main():
    if any(key in os.environ for key in (
        'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
        'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    )):
        raise SystemExit('STOP: unexpected CI or Git routing environment')
    head = subprocess.run(['git', '--no-optional-locks', '-C', str(ROOT), 'rev-parse', 'HEAD'],
        text=True, capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
    if head.returncode or head.stdout.strip() != EXPECTED_HEAD:
        raise SystemExit('STOP: shared source differs from the reviewed release base')
    # The original guard uses an operation lock. Bound its read in a child;
    # never read around that lock or print the lease/actor payload.
    check = """
import json
from pathlib import Path
from scripts import production_release_guard as guard
if guard.REPO_ROOT.resolve() != Path('/app'):
    raise SystemExit(2)
state = guard.status()
result = {'active': state.get('active'),
          'incomplete': state.get('incomplete_lease_directory', False)}
if not all(type(value) is bool for value in result.values()):
    raise SystemExit(3)
print(json.dumps(result))
"""
    try:
        checked = subprocess.run([sys.executable, '-B', '-c', check], cwd=ROOT,
            text=True, capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit('STOP: original guard status unavailable within five seconds') from None
    if checked.returncode:
        raise SystemExit('STOP: original shared guard status could not be validated')
    try:
        state = json.loads(checked.stdout)
    except (ValueError, TypeError):
        raise SystemExit('STOP: original shared guard status is invalid') from None
    if (not isinstance(state, dict) or set(state) != {'active', 'incomplete'}
            or not all(type(value) is bool for value in state.values())):
        raise SystemExit('STOP: original shared guard status is invalid')
    show('READ_ONLY_DIAGNOSTIC', {'deployment_git_sha': EXPECTED_HEAD,
         'guard_active': state['active'], 'guard_incomplete': state['incomplete'],
         'production_scope_verified': False})
    inspect_logs()
    inspect_database()
    print('DONE: read-only metadata; no application request, job, source, index, lease or financial write', flush=True)


if __name__ == '__main__':
    main()
