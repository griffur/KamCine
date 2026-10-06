"""Rapproche images locales, snapshot public et archives tierces par contenu et version."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid
import verifier_image


def digest(file):
    h=hashlib.sha256()
    with open(file,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser()
    for name in ('host','snapshot','sources','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--image',action='append',required=True)
    a=p.parse_args()
    if not a.host.startswith('unix:///'):p.error('Socket local explicite requis')
    source=Path(a.sources);snapshot=Path(a.snapshot);docker=['docker','--host',a.host]
    def run(*cmd):return subprocess.check_output(docker+list(cmd))
    python_records=json.loads((source/'python/sources.json').read_text())
    source_packages=set()
    for x in python_records:
        file=source/'python'/x['file'];assert digest(file)==x['sha256'],file
        with tarfile.open(file) as archive:
            info=sorted([m for m in archive.getmembers() if m.name.endswith('/PKG-INFO')],key=lambda m:len(m.name))
            if info:
                text=archive.extractfile(info[0]).read().decode()
                name=re.search(r'^Name: (.+)$',text,re.M).group(1).strip()
                version=re.search(r'^Version: (.+)$',text,re.M).group(1).strip()
                source_packages.add((re.sub(r'[-_.]+','-',name.lower()),version))
    debian=json.loads((source/'debian/sources.json').read_text())
    for item in debian:
        for x in item['files']:assert digest(source/'debian'/x['file'])==x['sha256'],x['file']
        dscs=[source/'debian'/x['file'] for x in item['files'] if x['file'].endswith('.dsc')]
        assert len(dscs)==1,item['source']
        dsc=dscs[0];description=dsc.read_text()
        assert re.search(r'^Source: (.+)$',description,re.M).group(1)==item['source']
        assert re.search(r'^Version: (.+)$',description,re.M).group(1)==item['version']
        checks=re.search(r'Checksums-Sha256:\n((?: .+\n)+)',description).group(1)
        for line in checks.splitlines():
            sha,size,name=line.split()
            archive=dsc.parent/name
            assert archive.stat().st_size==int(size) and digest(archive)==sha,name
    rust=json.loads((source/'rust/sources.json').read_text())
    for x in rust:assert digest(source/'rust'/x['file'])==x['sha256'],x['file']
    assert digest(source/'openssl-4.0.3.tar.gz')==(source/'openssl.sha256').read_text().split()[0]
    signature=json.loads((source/'python.sigstore').read_text())
    assert base64.b64decode(signature['messageSignature']['messageDigest']['digest']).hex()==digest(source/'python/Python-3.12.14.tar.xz')
    records=[]
    for image in a.image:
        name='kc-source-check-'+uuid.uuid4().hex[:10]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);app=root/'app';licenses=root/'licenses'
            try:
                run('create','--name',name,'--entrypoint','true',image)
                run('cp',name+':/app',str(app));run('cp',name+':/usr/share/kamcine/licenses',str(licenses))
                run('cp',name+':/usr/local/bin/kamcine-entrypoint',str(root/'entrypoint'))
                assert digest(root/'entrypoint')==digest(snapshot/'docker/entrypoint.sh')
                inventory=json.loads((licenses/'inventory.json').read_text())
                info=json.loads(run('image','inspect',image))[0]
                files={f:digest(snapshot/f) for f in verifier_image.contexte(str(snapshot))}
                for f,sha in files.items():assert digest(app/f)==sha,f
                actual={str(f.relative_to(app)) for f in app.rglob('*') if f.is_file() and f.suffix!='.pyc'}
                assert actual==set(files),actual.symmetric_difference(files)
                if (snapshot/'.git').exists():
                    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=snapshot,text=True).strip()
                    assert info['Config']['Labels']['org.opencontainers.image.revision']==revision
                assert all((re.sub(r'[-_.]+','-',x['name'].lower()),x['version']) in source_packages for x in inventory['python'])
                pairs={(x['source'],x['source_version']) for x in inventory['debian']}
                assert pairs=={(x['source'],x['version']) for x in debian}
                assert inventory['python_version']=='3.12.14'
                assert json.loads((licenses/'RUST-CRATES.json').read_text())==json.loads((source/'rust-licenses.json').read_text())
                records.append({'image':image,'id':info['Id'],'architecture':info['Architecture'],
                    'runtime_files':files,'python_packages':len(inventory['python']),'debian_sources':len(pairs),
                    'rust_sources':len(rust),'inventory_sha256':digest(licenses/'inventory.json')})
            finally:subprocess.run(docker+['rm','-f',name],capture_output=True)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'version':'2.7.10','checks':'runtime bytes, archives et versions correspondants','images':records},indent=2)+'\n')
    print('OK sources correspondantes pour',len(records),'images,',len(rust),'sources Rust')


if __name__=='__main__':main()
