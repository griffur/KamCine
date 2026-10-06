"""Download exact PyPI source distributions from a runtime inventory, with SHA256 verification.

Usage: python outils/exporter_sources_image.py inventory.json destination
Debian and vendored native sources must be added separately as documented in the publication guide.
"""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request


def download(url, target, digest=None):
    if not target.exists():
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read()
        if digest and hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Source checksum mismatch: ' + target.name)
        target.write_bytes(data)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest and actual != digest:
        raise ValueError('Existing source checksum mismatch: ' + target.name)
    return {'file': target.name, 'url': url, 'sha256': actual}


def main(inventory_path, destination):
    inventory = json.loads(Path(inventory_path).read_text())
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for package in inventory['python']:
        name, version = package['name'], package['version']
        with urllib.request.urlopen('https://pypi.org/pypi/%s/%s/json' % (name, version), timeout=60) as response:
            release = json.load(response)
        source = next((u for u in release['urls'] if u['packagetype'] == 'sdist'), None)
        if source is None:
            raise ValueError('No source distribution: %s==%s' % (name, version))
        records.append(download(source['url'], root / source['filename'], source['digests']['sha256']))
        print(name, version, flush=True)
    version = inventory['python_version']
    url = 'https://www.python.org/ftp/python/%s/Python-%s.tar.xz' % (version, version)
    records.append(download(url, root / ('Python-%s.tar.xz' % version)))
    (root / 'sources.json').write_text(json.dumps(records, indent=2) + '\n')


if __name__ == '__main__':
    main(*sys.argv[1:])
