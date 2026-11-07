"""Sanitized local resource receipt. No telemetry upload or notification recipient."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from common import containers, docker, write_json
from config import load

def evaluate(services, disk_fraction, backup_age):
    alerts = []
    for name, row in services.items():
        if not row['running'] or row['oom'] or row['health'] == 'unhealthy':
            alerts.append(name + ':unavailable')
        if row['memory_fraction'] >= .8:
            alerts.append(name + ':memory>=80%')
        if row['restarts']:
            alerts.append(name + ':restart-recorded')
    if disk_fraction >= .7:
        alerts.append('disk>=70%')
    if backup_age is None or backup_age > 26 * 3600:
        alerts.append('backup:missing-or-older-than-26h')
    return alerts

def snapshot(config):
    rows = containers(config)
    stats = docker('stats', '--no-stream', '--format', '{{json .}}', *[x['Id'] for x in rows.values()]).decode()
    usage = {x['ID']: x for x in map(json.loads, stats.splitlines())}
    services = {}
    for name, row in rows.items():
        stat = next((v for k, v in usage.items() if row['Id'].startswith(k)), None)
        if stat is None:
            raise ValueError('missing Docker resource sample')
        state = row['State']
        services[name] = {
            'running': state['Running'], 'oom': state.get('OOMKilled', False),
            'health': state.get('Health', {}).get('Status', 'no-healthcheck'),
            'restarts': row['RestartCount'], 'cpu_percent': float(stat['CPUPerc'].rstrip('%')),
            'memory_fraction': float(stat['MemPerc'].rstrip('%')) / 100,
            'memory_usage': stat['MemUsage'], 'memory_limit_bytes': row['HostConfig']['Memory'],
        }
        if state['Running']:
            relative = Path('/proc/%s/cgroup' % state['Pid']).read_text().split('0::', 1)[1].strip()
            group = Path('/sys/fs/cgroup')/relative.lstrip('/')
            events = dict(line.split() for line in (group/'memory.events').read_text().splitlines())
            cpu = dict(line.split() for line in (group/'cpu.stat').read_text().splitlines())
            services[name].update(memory_current_bytes=int((group/'memory.current').read_text()),
                                  memory_peak_bytes=int((group/'memory.peak').read_text()),
                                  oom_events=int(events['oom']), oom_kill_events=int(events['oom_kill']),
                                  cpu_usage_usec=int(cpu['usage_usec']), generation_started_at=state['StartedAt'])
            services[name]['oom'] |= int(events['oom']) > 0 or int(events['oom_kill']) > 0
    state = Path(config['state_dir'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    disk = shutil.disk_usage(state)
    mongo_mount = next(x for x in rows['mongo']['Mounts'] if x['Destination'] == '/data/db')
    mongo_disk = shutil.disk_usage(mongo_mount['Source'])
    receipt = state/'backup-latest.json'
    backup = json.loads(receipt.read_text()) if receipt.exists() else {}
    now = datetime.now(timezone.utc)
    age = (now - datetime.fromisoformat(backup['verified_at'])).total_seconds() if backup.get('success') else None
    alerts = evaluate(services, max(disk.used / disk.total, mongo_disk.used / mongo_disk.total), age)
    result = {'classification': 'synthetic-only', 'source_sha': config['source_sha'],
              'at': now.isoformat(), 'services': services, 'disk_total': disk.total,
              'disk_used': disk.used, 'mongo_disk_total': mongo_disk.total, 'mongo_disk_used': mongo_disk.used,
              'backup_age_seconds': age, 'alerts': alerts}
    # Bounded, minute receipts: seven days, no response bodies, environment or IDs.
    logs = state/'monitor'; logs.mkdir(exist_ok=True, mode=0o700)
    write_json(logs/(now.strftime('%Y%m%dT%H%M') + '.json'), result)
    for old in sorted(logs.glob('*.json'))[:-10080]:
        old.unlink()
    write_json(state/'monitor-latest.json', result)
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', required=True)
    args = parser.parse_args()
    result = snapshot(load(args.config))
    print(json.dumps(result))
    raise SystemExit(2 if result['alerts'] else 0)
