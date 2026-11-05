"""Generate reviewable files only. Never installs units or changes a firewall."""
import argparse
import ipaddress
from pathlib import Path
from config import load, require

def firewall(config):
    rules = []
    for item in config['admin_cidrs']:
        network = ipaddress.ip_network(item)
        family = 'ip' if network.version == 4 else 'ip6'
        rules.append(f'    {family} saddr {network} tcp dport {config["ssh_port"]} accept')
    return '''# Generated SYNTHETIC VPS rules. Review/KVM/rollback before any application.
# Only this table is owned here; do not flush Docker or other host tables.
add table inet mezan_trial
flush table inet mezan_trial
table inet mezan_trial {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    ct state invalid drop
    iifname "lo" accept
    meta l4proto { icmp, ipv6-icmp } accept
''' + '\n'.join(rules) + '''
    tcp dport { 80, 443 } accept
  }
  chain docker_ingress {
    type filter hook forward priority -10; policy accept;
    ct state established,related accept
    iifname "''' + config['public_interface'] + '''" ct state new drop
  }
}
'''

def caddy(config, *, ci=False):
    domains = config['hostname'] + (', 127.0.0.1' if ci else '')
    issuer = ('    tls internal {\n        protocols tls1.2 tls1.3\n    }\n' if ci else '    tls {\n        protocols tls1.2 tls1.3\n    }\n')
    return '''{
    admin off
    servers {
        protocols h1 h2
    }
}
''' + domains + ''' {
''' + issuer + '''
    encode gzip
    header X-Content-Type-Options nosniff
    header Referrer-Policy same-origin
    reverse_proxy 127.0.0.1:10000 {
        header_up X-Forwarded-Proto https
        transport http {
            response_header_timeout 65s
        }
    }
}
'''

def units(config):
    folder = config['checkout'] + '/packaging/exit2d/ovh_host'
    conf = '/etc/mezan-vps/host.json'
    compose = '/usr/bin/docker -H unix:///var/run/docker.sock compose --env-file /etc/mezan-vps/compose.env -p ' + config['project'] + ' -f ' + config['checkout'] + '/packaging/exit2d/vps/compose.yaml'
    return {
      'mezan-stack.service': f'''[Unit]
Description=Mezan synthetic five-service Compose stack
Requires=docker.service
After=docker.service network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={config['checkout']}
ExecStart={compose} up -d --no-build --wait --wait-timeout 240
ExecStop={compose} stop --timeout 30
TimeoutStartSec=300
TimeoutStopSec=180
[Install]
WantedBy=multi-user.target
''',
      'mezan-tls.service': f'''[Unit]
Description=Mezan synthetic HTTPS edge
After=network-online.target docker.service
[Service]
User=caddy
Group=caddy
Environment=XDG_DATA_HOME=/var/lib/mezan-caddy
Environment=XDG_CONFIG_HOME=/var/lib/mezan-caddy
StateDirectory=mezan-caddy
ExecStart=/usr/bin/caddy run --config /etc/mezan-vps/Caddyfile --adapter caddyfile
Restart=on-failure
MemoryMax=192M
CPUQuota=20%
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
''',
      'mezan-monitor.service': f'''[Unit]
Description=Mezan sanitized private host snapshot
After=docker.service
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/bin/python3 {folder}/monitor.py --config {conf}
MemoryMax=96M
CPUQuota=5%
NoNewPrivileges=true
''',
      'mezan-monitor.timer': '''[Unit]
Description=Mezan minute resource/backup checks
[Timer]
OnBootSec=90
OnUnitInactiveSec=60
[Install]
WantedBy=timers.target
''',
      'mezan-backup.service': f'''[Unit]
Description=Mezan approved synthetic offsite backup
After=docker.service network-online.target
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/bin/python3 {folder}/backup.py --config {conf} --execute --approval /etc/mezan-vps/operations-approval.json
MemoryMax=768M
CPUQuota=100%
TimeoutStartSec=900
''',
      'mezan-backup.timer': '''[Unit]
Description=Mezan synthetic daily backup maintenance window
[Timer]
OnCalendar=*-*-* 02:30:00 Asia/Riyadh
Persistent=true
[Install]
WantedBy=timers.target
'''}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--isolated-ci', action='store_true')
    a = p.parse_args(); c = load(a.config, ci=a.isolated_ci)
    target = a.output.resolve()
    require(not any(target == Path(x) or Path(x) in target.parents for x in ('/etc', '/usr', '/run')), 'generate to a review directory only')
    target.mkdir(parents=True, exist_ok=False)
    env = 'MEZAN_SOURCE_SHA=' + c['source_sha'] + '\nMEZAN_SECRETS_DIR=' + c['secrets_dir'] + '\nMEZAN_STAGING_ORIGIN=' + c['origin'] + '\nMEZAN_STAGING_HOST=' + c['hostname'] + '\nMEZAN_CGROUP_PARENT=mezan-trial.slice\nEXIT2D_ECONOMY_CI=0\n'
    files = {'Caddyfile': caddy(c, ci=a.isolated_ci), 'firewall.nft': firewall(c), 'compose.env':env,
             'mezan-trial.slice': '[Unit]\nDescription=Mezan synthetic collective cap\n[Slice]\nCPUQuota=400%\nMemoryMax=8G\nMemorySwapMax=0\n', **units(c)}
    for name, value in files.items():
        (target/name).write_text(value)
    print('Generated review artifacts only; no installation, firewall application, certificate order or timer enabled')

if __name__ == '__main__': main()
