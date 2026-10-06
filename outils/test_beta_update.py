"""Mise à jour et rollback réels, uniquement sur socket Docker local explicite."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    p = argparse.ArgumentParser()
    for key in ("host", "old", "new", "platform", "output"):
        p.add_argument("--" + key, required=True)
    p.add_argument("--old-version", default="2.7.5")
    a = p.parse_args()
    if not a.host.startswith("unix:///"):
        p.error("Socket local explicite requis")
    docker = ["docker", "--host", a.host]
    name = "kc-beta-update-" + uuid.uuid4().hex[:10]
    volume = name + "-data"
    output = Path(a.output); output.mkdir(parents=True, exist_ok=True)
    def call(*cmd, data=None):
        r = subprocess.run(docker + list(cmd), input=data, capture_output=True, timeout=180)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors="replace"))
        return r.stdout
    def py(code):
        return call("exec", "-i", name, "python", "-", data=code.replace("'2.7.5'", repr(a.old_version)).encode())
    def start(image):
        call("run", "-d", "--name", name, "--network", "none", "--platform", a.platform,
             "--mount", "type=volume,src=" + volume + ",dst=/config", "-e", "PUID=1234", "-e", "PGID=1234",
             "--health-interval", "1s", "--health-start-period", "1s", image)
        for _ in range(60):
            state = json.loads(call("inspect", name))[0]["State"]
            if state.get("Health", {}).get("Status") == "healthy": return
            if not state["Running"]: raise RuntimeError(call("logs", name).decode())
            time.sleep(1)
        raise RuntimeError("Santé non atteinte")
    def stop():
        call("stop", "--time", "20", name); call("rm", name)
    def oneoff(code, data=None):
        return call("run", "--rm", "-i", "--network", "none", "--platform", a.platform,
                    "--mount", "type=volume,src=" + volume + ",dst=/config", "--entrypoint", "sh", a.new,
                    "-c", code, data=data)
    helpers = """
import requests, pathlib, json
s=requests.Session();base='http://127.0.0.1:8765'
def login():
    r=s.post(base+'/auth/connexion',json={'identifiant':'test','mot_de_passe':'beta-local-2026'},timeout=10)
    r.raise_for_status()
"""
    try:
        call("volume", "create", volume)
        start(a.old)
        py(helpers + """
r=s.post(base+'/auth/inscription',json={'nom':'Test','identifiant':'test','mot_de_passe':'beta-local-2026'},timeout=10)
r.raise_for_status()
r=s.post(base+'/reglages',json={'nb_trailers':'2'},timeout=10);r.raise_for_status()
pathlib.Path('/config/backup-probe').write_bytes(b'volume complet')
pathlib.Path('/config/secrets.json').write_text(json.dumps({'beta_probe':'fake-local-only'}))
assert s.get(base+'/health').json()['version']=='2.7.5'
""")
        stop()
        archive = oneoff("tar -cf - -C /config .")
        (output / "config-before.tar").write_bytes(archive)
        before = oneoff("find /config -type f -exec sha256sum {} + | sort")
        start(a.new)
        py(helpers + """
login()
assert s.get(base+'/health').json()['version']=='2.7.10'
assert s.get(base+'/reglages').json()['nb_trailers']=='2'
assert pathlib.Path('/config/backup-probe').read_bytes()==b'volume complet'
assert json.loads(pathlib.Path('/config/secrets.json').read_text())['beta_probe']=='fake-local-only'
r=s.post(base+'/reglages',json={'nb_trailers':'3'},timeout=10);r.raise_for_status()
import json_atomique
p=pathlib.Path('/config/secrets.json');old=p.read_bytes()
try: json_atomique.ecrire(p, {'invalid':object()})
except TypeError: pass
else: raise AssertionError('Erreur de sérialisation attendue')
assert p.read_bytes()==old
""")
        stop()
        # Effacement limité au volume temporaire créé ci dessus, puis restauration de son archive.
        oneoff("find /config -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xf - -C /config", archive)
        assert oneoff("find /config -type f -exec sha256sum {} + | sort") == before
        start(a.old)
        py(helpers + """
login()
assert s.get(base+'/health').json()['version']=='2.7.5'
assert s.get(base+'/reglages').json()['nb_trailers']=='2'
assert pathlib.Path('/config/backup-probe').read_bytes()==b'volume complet'
""")
        stop()
        result = {'platform':a.platform,'old':a.old,'new':a.new,'backup_sha256':hashlib.sha256(archive).hexdigest(),
                  'checks':['update '+a.old_version+' vers 2.7.10','compte et réglages conservés','secrets et fichiers conservés',
                            'écriture interrompue intacte','restauration exacte du volume complet','rollback '+a.old_version+' et reconnexion']}
        (output / 'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print('OK', a.platform, 'update et rollback avec sauvegarde complète', flush=True)
    finally:
        subprocess.run(docker + ['rm','-f',name],capture_output=True)
        subprocess.run(docker + ['volume','rm',volume],capture_output=True)


if __name__ == '__main__':
    main()
