"""Collect native source archives and notices from the exact Python source bundle.

Usage: python outils/exporter_sources_natives.py source_directory
Writes native-notices.txt and rust-licenses.json alongside python/ and rust/.
"""
import concurrent.futures, hashlib, io, json, re, sys, tarfile, urllib.request
from pathlib import Path
root=Path(sys.argv[1])
crates={}
for filename in ('cryptography-50.0.2.tar.gz','pydantic_core-2.46.5.tar.gz'):
 with tarfile.open(root/'python'/filename) as archive:
  for item in archive.getmembers():
   if item.name.endswith('/Cargo.lock'):
    data=archive.extractfile(item).read().decode()
    for block in data.split('[[package]]')[1:]:
     fields=dict(re.findall(r'^(name|version|source|checksum) = "([^"]+)"',block,re.M))
     if fields.get('source','').startswith('registry+'):
      crates[(fields['name'],fields['version'])]=fields['checksum']
# Compléter la fermeture des sources par les composants natifs identifiés dans les roues livrées.
manifest=Path(__file__).resolve().parents[1]/'docker/licenses/rust-crates.json'
for filename in json.loads(manifest.read_text()):
 name,version=filename.removesuffix('.crate').rsplit('-',1)
 if (name,version) not in crates:
  with urllib.request.urlopen('https://crates.io/api/v1/crates/%s/%s'%(name,version),timeout=90) as response:
   crates[(name,version)]=json.load(response)['version']['checksum']
folder=root/'rust';folder.mkdir(exist_ok=True)
def fetch(entry):
 (name,version),digest=entry
 file=folder/(name+'-'+version+'.crate')
 url='https://static.crates.io/crates/%s/%s-%s.crate'%(name,name,version)
 if not file.exists():
  with urllib.request.urlopen(url,timeout=90) as response:data=response.read()
  assert hashlib.sha256(data).hexdigest()==digest
  file.write_bytes(data)
 assert hashlib.sha256(file.read_bytes()).hexdigest()==digest
 return {'name':name,'version':version,'sha256':digest,'url':url,'file':file.name}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:records=list(pool.map(fetch,crates.items()))
(folder/'sources.json').write_text(json.dumps(records,indent=2))
print(len(records),'Rust crates downloaded and verified',flush=True)
url='https://github.com/openssl/openssl/releases/download/openssl-4.0.3/openssl-4.0.3.tar.gz'
file=root/'openssl-4.0.3.tar.gz'
if not file.exists():
 with urllib.request.urlopen(url,timeout=90) as response:file.write_bytes(response.read())
print('OpenSSL 4.0.3',hashlib.sha256(file.read_bytes()).hexdigest(),flush=True)
# Keep every source notice, including a conservative superset of build dependencies.
notices=[];expressions={}
source_files=list((root/'python').rglob('*'))+list((root/'rust').rglob('*'))+list(root.glob('openssl-*.tar.gz'))
for file in sorted(source_files):
 if not file.is_file() or not file.name.endswith(('.gz','.xz','.crate')):continue
 with tarfile.open(file) as archive:
  for item in archive.getmembers():
   base=Path(item.name).name.lower()
   if not item.isfile() or item.size>500000:continue
   if base.startswith(('license','licence','copying','copyright','notice')) and not base.endswith(('.py','.rs','.c','.h')):
    content=archive.extractfile(item).read().decode('utf-8',errors='replace')
    content='\n'.join(line.rstrip() for line in content.splitlines()).strip()
    notices.append('\n\n===== '+file.name+' : '+item.name+' =====\n'+content)
   if file.suffix=='.crate' and item.name.count('/')==1 and base=='cargo.toml':
    content=archive.extractfile(item).read().decode()
    m=re.search(r'^license\s*=\s*"([^"]+)"',content,re.M)
    expressions[file.name]=m.group(1) if m else 'REVIEW'
(root/'native-notices.txt').write_text('Source notices for Python packages and their native components.\nIncludes the complete Cargo.lock closure, including build/test dependencies.\n'+''.join(notices).rstrip()+'\n')
(root/'rust-licenses.json').write_text(json.dumps(expressions,indent=2))
print('Rust license expressions:',sorted(set(expressions.values())),flush=True)
print(len(notices),'notice files collected',flush=True)
