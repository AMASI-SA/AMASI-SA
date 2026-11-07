"""Non-secret synthetic-host configuration. No account, DB or environment discovery."""
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

def require(condition, message='invalid synthetic host configuration'):
    if not condition:
        raise ValueError(message)

PRODUCTION_DOMAINS = ('mezansalla.com', 'amasi-sa.com')

def validate(data, *, ci=False):
    data = dict(data)
    require(data.get('schema') == 1)
    require(data.get('classification') == 'synthetic-only')
    require(re.fullmatch(r'[a-f0-9]{40}', data.get('source_sha', '')))
    require(re.fullmatch(r'(?:mezan-vps-|exit2dhost)[a-z0-9-]+', data.get('project', '')))
    origin = urlsplit(data['origin'])
    host = origin.hostname or ''
    require(origin.scheme == 'https' and origin.port in (None, 443))
    require(not origin.username and not origin.password and not origin.query and not origin.fragment)
    require(origin.path in ('', '/'))
    require(host and not any(host == x or host.endswith('.' + x) for x in PRODUCTION_DOMAINS))
    require(re.fullmatch(r'[a-z0-9.-]+', host))
    require(ci or not ipaddress_like(host), 'public trial requires an explicit staging hostname')
    require(all(PurePosixPath(data[k]).is_absolute() and '..' not in PurePosixPath(data[k]).parts and re.fullmatch(r'[A-Za-z0-9/._-]+', data[k]) for k in ('checkout', 'secrets_dir', 'state_dir')))
    require(re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', data['public_interface']))
    require(1 <= int(data.get('ssh_port', 22)) <= 65535)
    networks = [ipaddress.ip_network(x, strict=True) for x in data['admin_cidrs']]
    require(networks and all(x.prefixlen > 0 for x in networks), 'no world-wide SSH allowlist')
    require(data['public_interface'] != 'lo')
    data['hostname'] = host
    data['ssh_port'] = int(data.get('ssh_port', 22))
    data['admin_cidrs'] = [str(x) for x in networks]
    forbidden = [k for k in data if any(x in k.lower() for x in ('password', 'token', 'api_key')) and not k.endswith('_file')]
    require(not forbidden, 'configuration must contain paths, never secret values')
    require(set(data.get('backup', {})) <= {'repository', 'password_file', 'access_key_file', 'secret_key_file', 'max_archive_bytes', 'max_repository_bytes'}, 'unknown backup key/value forbidden')
    backup = data.get('backup', {})
    require(0 < backup.get('max_archive_bytes', 0) <= 512 * 1024 * 1024, 'synthetic archive cap must be <=512MiB')
    require(backup['max_archive_bytes'] < backup.get('max_repository_bytes', 0) <= 8_000_000_000, 'reviewed repository soft cap must be <=8GB')
    return data

def ipaddress_like(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False

def load(path, *, ci=False):
    return validate(json.loads(Path(path).read_text()), ci=ci)
