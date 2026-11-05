"""Host-side cgroup-v2 evidence; no docker exec memory is added to services."""
import json
from pathlib import Path
import subprocess
import sys
import time

output, *names = sys.argv[1:]
rows = {}
while not Path(output + '.stop').exists():
    states = json.loads(subprocess.check_output(['docker', 'inspect', *names]))
    for state in states:
        name = state['Name'].lstrip('/')
        row = rows.setdefault(name, {'samples': 0, 'current_bytes': 0, 'peak_bytes': 0,
            'oom': 0, 'oom_kill': 0, 'restarts_max': 0, 'cpu_usage_usec': 0, 'sample_total_bytes': 0, 'cpu_throttled_usec': 0})
        row['restarts_max'] = max(row['restarts_max'], state['RestartCount'])
        row['docker_oom_seen'] = row.get('docker_oom_seen', False) or state['State']['OOMKilled']
        row['memory_limit_bytes'] = state['HostConfig']['Memory']
        row['memory_swap_bytes'] = state['HostConfig']['MemorySwap']
        row['nano_cpus'] = state['HostConfig']['NanoCpus']
        pid = state['State']['Pid']
        if not pid:
            continue
        try:
            relative = Path(f'/proc/{pid}/cgroup').read_text().strip().split('0::')[1]
            group = Path('/sys/fs/cgroup') / relative.lstrip('/')
            current = int((group / 'memory.current').read_text())
            peak = int((group / 'memory.peak').read_text())
            events = dict(line.split() for line in (group / 'memory.events').read_text().splitlines())
            cpu = dict(line.split() for line in (group / 'cpu.stat').read_text().splitlines())
            row.update(current_bytes=current, samples=row['samples'] + 1)
            row['sample_total_bytes'] += current
            row['sample_mean_bytes'] = row['sample_total_bytes'] // row['samples']
            row['cpu_throttled_usec'] = max(row['cpu_throttled_usec'], int(cpu.get('throttled_usec', 0)))
            row['peak_bytes'] = max(row['peak_bytes'], peak)
            row['oom'] = max(row['oom'], int(events.get('oom', 0)))
            row['oom_kill'] = max(row['oom_kill'], int(events.get('oom_kill', 0)))
            row['cpu_usage_usec'] = max(row['cpu_usage_usec'], int(cpu['usage_usec']))
        except (FileNotFoundError, ProcessLookupError):
            pass  # A deliberate restart can remove its former cgroup mid-read.
    Path(output).write_text(json.dumps({'kind': 'cgroup_v2_no_swap', 'services': rows}, indent=2))
    time.sleep(.5)
