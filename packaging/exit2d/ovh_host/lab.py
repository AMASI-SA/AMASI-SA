"""Real isolated Docker TLS/firewall tests. Never use host networking/firewall."""
import json
import os
from pathlib import Path
import subprocess
import time
from config import validate
from generate import caddy, firewall

def command(*args, input=None, check=True):
    result = subprocess.run(args, input=input, capture_output=True, timeout=90)
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode()[:1000])
    return result

def docker(*args, **kwargs): return command('docker', *args, **kwargs)

def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('disposable GitHub Linux runner required')
    prefix = 'exit2dhost' + os.environ['GITHUB_RUN_ID']; names = []; networks = []
    def network(suffix):
        name = prefix + suffix; docker('network', 'create', '--internal', name); networks.append(name); return name
    def container(suffix, net, *options):
        name = prefix + suffix
        docker('run', '-d', '--name', name, '--network', net, *options)
        names.append(name); return name
    def address(name, net):
        return json.loads(docker('inspect', name).stdout)[0]['NetworkSettings']['Networks'][net]['IPAddress']
    def curl(client, url):
        return docker('exec', client, 'curl', '--silent', '--fail', '--max-time', '2', url, check=False).returncode == 0
    try:
        net = network('front'); backnet = network('back')
        image = 'mezan-host-lab:' + os.environ['GITHUB_SHA']
        router = container('router', net, '--cap-add=NET_ADMIN', '--sysctl', 'net.ipv4.ip_forward=1', image, 'python', '/lab/lab_server.py')
        docker('network', 'connect', backnet, router)
        backend = container('backend', backnet, image, 'python', '/lab/lab_server.py')
        admin = container('admin', net, image); outsider = container('outsider', net, image)
        rip = address(router, net); bip = address(backend, backnet)
        time.sleep(2)
        nat = 'table ip lab_nat { chain pre { type nat hook prerouting priority -100; tcp dport 18080 dnat to ' + bip + ':10000; } chain post { type nat hook postrouting priority 100; masquerade; } }'
        docker('exec', '-i', router, 'nft', '-f', '-', input=nat.encode())
        if not curl(outsider, 'http://' + rip + ':18080'):
            raise ValueError('DNAT baseline not reachable; firewall assertion would be invalid')
        conf = json.loads((Path(__file__).parent/'host.example.json').read_text())
        conf.update(public_interface='eth0', admin_cidrs=[address(admin, net)+'/32'])
        rules = firewall(validate(conf))
        docker('exec', '-i', router, 'nft', '-c', '-f', '-', input=rules.encode())
        docker('exec', '-i', router, 'nft', '-f', '-', input=rules.encode())
        cases = []
        for client in (admin, outsider):
            for port in (22, 80, 443, 10000, 27017, 18080):
                allowed = port in (80,443) or (client == admin and port == 22)
                actual = curl(client, 'http://' + rip + ':' + str(port))
                if actual != allowed: raise ValueError('firewall reachability differs from allowlist')
                cases.append({'client': 'admin' if client == admin else 'outsider', 'port': port, 'allowed': actual})
        if not curl(router, 'http://127.0.0.1:10000'):
            raise ValueError('loopback upstream blocked')
        print(json.dumps({'kind':'kernel_firewall_ipv4', 'dnat_baseline_proved':True, 'cases':cases, 'host_firewall_modified':False}))
        # TLS reverse proxy has a private namespace and a local fixture upstream.
        tlsnet = network('tls')
        # Separate one-port JSON fixture so Caddy owns 80/443; no application claims.
        server = "from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler;import json\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  b=json.dumps({'proto':self.headers.get('X-Forwarded-Proto')}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b)\n def log_message(self,*a):pass\nThreadingHTTPServer(('0.0.0.0',10000),H).serve_forever()"
        upstream = container('upstream', tlsnet, image, 'python', '-c', server)
        proxy = prefix+'caddy'; names.append(proxy)
        conf['hostname'] = 'staging.example.test'
        path = Path(os.environ['RUNNER_TEMP'])/'mezan-host-ci-Caddyfile'; path.write_text(caddy(conf, ci=True))
        docker('run', '-d', '--name', proxy, '--network', 'container:'+upstream,
               '-v', str(path)+':/etc/caddy/Caddyfile:ro', 'caddy:2.10.2')
        time.sleep(4)
        docker('exec', proxy, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
        ca = docker('exec', proxy, 'cat', '/data/caddy/pki/authorities/local/root.crt').stdout
        docker('exec', '-i', upstream, 'sh', '-c', 'cat > /tmp/ca.crt', input=ca)
        result = docker('exec', upstream, 'curl', '--fail', '--silent', '--cacert', '/tmp/ca.crt', 'https://127.0.0.1/').stdout
        if json.loads(result)['proto'] != 'https': raise ValueError('forwarded HTTPS header missing')
        # Untrusted CA must fail; no TLS verify bypass is permitted.
        if curl(upstream, 'https://127.0.0.1/'):
            raise ValueError('untrusted test CA unexpectedly accepted')
        print(json.dumps({'kind':'verified_tls_proxy_fixture', 'trusted_ca_pass':True, 'untrusted_ca_refused':True,
                          'forwarded_https':True, 'actual_application_auth_tested_here':False, 'public_acme_or_dns_used':False}))
    finally:
        for name in reversed(names): docker('rm', '-f', name, check=False)
        for name in reversed(networks): docker('network', 'rm', name, check=False)

if __name__ == '__main__': main()
