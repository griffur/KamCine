"""Collecte APT des sources exactes de l’inventaire, dans un conteneur local jetable."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid


def main():
    p=argparse.ArgumentParser()
    for name in ('host','image','inventory','output'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    if not a.host.startswith('unix:///'):p.error('Socket local explicite requis')
    inventory=json.loads(Path(a.inventory).read_text())
    packages=sorted({(x['source'],x['source_version']) for x in inventory['debian']})
    name='kc-beta-sources-'+uuid.uuid4().hex[:10]
    docker=['docker','--host',a.host]
    def call(*cmd,data=None):
        r=subprocess.run(docker+list(cmd),input=data,text=True,capture_output=True,timeout=900)
        if r.returncode:raise RuntimeError(r.stdout+r.stderr)
        return r.stdout
    script = r'''
import concurrent.futures,hashlib,json,pathlib,re,subprocess,urllib.request,urllib.parse,time
packages=PACKAGES
repo=pathlib.Path('/etc/apt/sources.list.d/debian.sources')
repo.write_text(repo.read_text().replace('Types: deb','Types: deb deb-src'))
subprocess.run(['apt-get','update'],check=True,stdout=subprocess.DEVNULL)
root=pathlib.Path('/sources');root.mkdir()
def fetch(item):
    package,version=item
    d=root/package;d.mkdir()
    r=subprocess.run(['apt-get','source','--download-only',package+'='+version],cwd=d,text=True,capture_output=True)
    if r.returncode:
        def get(url):
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(url,timeout=90) as response:return response.read()
                except Exception:
                    if attempt==3:raise
                    time.sleep(2)
        base='https://snapshot.debian.org'
        meta=json.loads(get(base+'/mr/package/'+package+'/'+urllib.parse.quote(version,safe='')+'/srcfiles'))
        if meta['version']!=version:raise RuntimeError('Version archive incorrecte')
        for item in meta['result']:
            digest=item['hash'];info=json.loads(get(base+'/mr/file/'+digest+'/info'))
            filename=info['result'][0]['name'];data=get(base+'/file/'+digest)
            if hashlib.sha1(data).hexdigest()!=digest:raise RuntimeError('Archive snapshot corrompue')
            (d/filename).write_bytes(data)
    dsc=next(d.glob('*.dsc'));text=dsc.read_text()
    match=re.search(r'^Checksums-Sha256:\n((?: [^\n]+\n)+)',text,re.M)
    if not match:raise RuntimeError('Empreintes absentes '+package)
    files=[]
    for line in match.group(1).splitlines():
        digest,size,filename=line.split();file=d/filename
        if file.stat().st_size!=int(size) or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Source corrompue '+filename)
        files.append({'file':package+'/'+filename,'sha256':digest})
    files.append({'file':package+'/'+dsc.name,'sha256':hashlib.sha256(dsc.read_bytes()).hexdigest()})
    return {'source':package,'version':version,'files':files}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:records=list(pool.map(fetch,packages))
(root/'sources.json').write_text(json.dumps(records,indent=2)+'\n')
print('Sources Debian vérifiées',len(records))
'''.replace('PACKAGES',repr(packages))
    try:
        call('run','-d','--name',name,'--entrypoint','sh',a.image,'-c','sleep 86400')
        print(call('exec','-i',name,'python','-',data=script),flush=True)
        output=Path(a.output);output.mkdir(parents=True,exist_ok=True)
        call('cp',name+':/sources/.',str(output))
    finally:
        subprocess.run(docker+['rm','-f',name],capture_output=True)


if __name__=='__main__':main()
