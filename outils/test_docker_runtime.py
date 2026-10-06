"""Test a real local image with no network or published ports; never use a default Docker endpoint.

python outils/test_docker_runtime.py --host unix:///explicit/local/socket --image IMAGE --platform linux/arm64 --output DIR
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--config')
    parser.add_argument('--image', required=True)
    parser.add_argument('--platform', required=True, choices=['linux/amd64', 'linux/arm64'])
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not args.host.startswith('unix:///'):
        parser.error('Only an explicit local Unix socket is allowed')
    docker = ['docker', '--host', args.host]
    if args.config:
        docker += ['--config', args.config]
    prefix = 'kc-audit-' + uuid.uuid4().hex[:10]
    volume, container = prefix + '-data', prefix + '-app'
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    checks = []

    def run(*command, script=None, check=True):
        result = subprocess.run(docker + list(command), input=script, text=True, capture_output=True, timeout=180)
        if check and result.returncode:
            raise RuntimeError(' '.join(command) + '\n' + result.stderr + result.stdout)
        return result.stdout.strip()

    def python(script):
        return run('exec', '-i', container, 'python', '-', script=script)

    def passed(name):
        checks.append(name); print('OK', name, flush=True)

    def start(zone='Asia/Kathmandu'):
        run('run', '-d', '--name', container, '--platform', args.platform, '--network', 'none', '--read-only',
            '--tmpfs', '/tmp:rw,nosuid,nodev', '--mount', 'type=volume,src='+volume+',dst=/config',
            '-e', 'KAMCINE_TIMEZONE='+zone, '-e', 'PUID=1234', '-e', 'PGID=1234',
            '--health-interval', '1s', '--health-start-period', '1s', args.image)
        for _ in range(60):
            state = json.loads(run('inspect', container))[0]['State']
            if not state['Running']:
                raise RuntimeError(run('logs', container))
            if state.get('Health', {}).get('Status') == 'healthy':
                return
            time.sleep(1)
        raise RuntimeError('Unhealthy container: ' + run('logs', container))

    request_helpers = '''
import json, pathlib, requests
s = requests.Session()
base = 'http://127.0.0.1:8765'
def get(path):
    r=s.get(base+path,timeout=10);r.raise_for_status();return r
'''
    try:
        run('volume', 'create', volume)
        start()
        info = json.loads(run('image', 'inspect', args.image))[0]
        assert info['Architecture'] == args.platform.split('/')[1]
        assert info['Config']['Labels']['org.opencontainers.image.licenses'] == 'GPL-3.0-only'
        passed('architecture, label GPL et HEALTHCHECK réel')
        python(request_helpers + '''
from cryptography.hazmat.backends.openssl.backend import backend
assert 'OpenSSL 4.0.3' in backend.openssl_version_text(), backend.openssl_version_text()
assert get('/health').json()=={'ok':True,'version':'2.7.10'}
assert get('/auth/etat').json()['etat']=='nouvelle'

for route, name, mime, signature in [
    ('/splash.jpg?v=2.7.10', 'splash.jpg', 'image/jpeg', bytes.fromhex('ffd8')),
    ('/icon.png?style=sombre', 'icon_sombre.png', 'image/png', bytes.fromhex('89504e470d0a1a0a')),
    ('/icon.png?style=clair', 'icon_clair.png', 'image/png', bytes.fromhex('89504e470d0a1a0a')),
]:
    response = get(route)
    assert response.headers['content-type'].split(';')[0] == mime, route
    assert response.content.startswith(signature), route
    assert response.content == pathlib.Path('/app/app', name).read_bytes(), route

assert 'content="Asia/Kathmandu"' in get('/').text
r=s.post(base+'/auth/inscription',json={'nom':'Test','identifiant':'test','mot_de_passe':'local-test-2026'},timeout=15)
r.raise_for_status();assert r.json()['compte']['admin']
assert not any(x['configuree'] for x in get('/installation/etat').json()['services'].values())
assert get('/apropos').json()['fuseau']=='Asia/Kathmandu'
assert get('/planning').json()['fuseau']=='Asia/Kathmandu'
assert get('/logos/tmdb.svg').status_code==200
for name in ['imdb.svg','infuse.png','sofatime.png','overseerr.svg','radarr.svg','sonarr.svg','metacritic.svg','rt_fresh.svg']:
    assert s.get(base+'/logos/'+name).status_code==404,name
r=s.post(base+'/reglages',json={'nb_trailers':'2'},timeout=10);r.raise_for_status()
assert r.json()['nb_trailers']=='2'
assert pathlib.Path('/config/kamcine.db').is_file()
assert not pathlib.Path('/config/creds.env').exists()
assert set(p.name for p in pathlib.Path('/app/app/logos').iterdir())=={'tmdb.svg'}
status=pathlib.Path('/proc/1/status').read_text()
assert 'Uid:\\t1234\\t1234\\t1234\\t1234' in status,status
''')
        passed('installation vierge, compte, services absents, fuseau, logos et utilisateur non root')
        run('exec', container, 'python', '-m', 'pip', 'check')
        run('cp', container+':/usr/share/kamcine/licenses', str(output/'licenses'))
        inventory = json.loads((output/'licenses/inventory.json').read_text())
        assert all(p['notices'] for p in inventory['python'])
        assert all(p['notice'] for p in inventory['debian'])
        passed('dépendances cohérentes, notices Python et Debian présentes')
        before = run('exec', container, 'sh', '-c', 'find /app -type f -exec sha256sum {} + | sort')
        run('stop', '--time', '20', container)
        state = json.loads(run('inspect', container))[0]['State']
        assert state['ExitCode']==0, state
        run('rm', container)
        start('America/Montreal')
        python(request_helpers + '''
assert get('/auth/etat').json()['etat']=='connexion'
r=s.post(base+'/auth/connexion',json={'identifiant':'test','mot_de_passe':'local-test-2026'},timeout=10)
r.raise_for_status()
assert get('/reglages').json()['nb_trailers']=='2'
assert get('/apropos').json()['fuseau']=='America/Montreal'
''')
        after = run('exec', container, 'sh', '-c', 'find /app -type f -exec sha256sum {} + | sort')
        assert before == after
        passed('arrêt SIGTERM propre, recréation, persistance et code en lecture seule')
        run('stop', '--time', '20', container);run('rm', container)
        run('run', '-d', '--name', container, '--platform', args.platform, '--network', 'none',
            '--tmpfs', '/config', '-e', 'KAMCINE_TIMEZONE=Invalid/Zone', args.image)
        for _ in range(30):
            state = json.loads(run('inspect', container))[0]['State']
            if not state['Running']:break
            time.sleep(1)
        assert not state['Running'] and state['ExitCode'] != 0
        result = subprocess.run(docker+['logs',container],capture_output=True,text=True)
        assert 'Fuseau IANA invalide' in result.stdout+result.stderr
        passed('fuseau invalide refusé explicitement')
        (output/'result.json').write_text(json.dumps({'image':args.image,'id':info['Id'],'architecture':info['Architecture'],
                    'checks':checks,'network':'none','read_only':True,'version':'2.7.10'},indent=2)+'\n')
    finally:
        run('rm', '-f', '-v', container,check=False)
        run('volume', 'rm', volume,check=False)


if __name__ == '__main__':
    main()
