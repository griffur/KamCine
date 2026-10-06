"""Build an offline inventory and retain notices from the actual runtime image."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def collect(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent / 'licenses' / 'native-notices.txt', root / 'NATIVE-NOTICES.txt')
    shutil.copyfile(Path(__file__).parent / 'licenses' / 'rust-crates.json', root / 'RUST-CRATES.json')
    packages = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name, version = dist.metadata['Name'], dist.version
        notices, native = [], []
        for relative in dist.files or []:
            source = Path(dist.locate_file(relative))
            if not source.is_file():
                continue
            if source.suffix == '.so':
                native.append({'path': str(relative), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
            if any(word in source.name.lower() for word in ('license', 'copying', 'copyright', 'notice')):
                target = root / 'python' / name / str(relative).replace('../', '')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                notices.append(str(target.relative_to(root)))
        if not notices and name.replace('_', '-') == 'http-ece' and version == '1.2.1':
            source = Path(__file__).parent / 'licenses' / 'http-ece-1.2.1.txt'
            target = root / 'python' / name / 'LICENSE'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            notices.append(str(target.relative_to(root)))
        packages.append({'name': name, 'version': version,
                         'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                         'classifiers': dist.metadata.get_all('Classifier', []),
                         'sources': 'https://pypi.org/project/%s/%s/#files' % (name, version),
                         'notices': notices, 'native': native})
    fmt = '${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'
    output = subprocess.check_output(['dpkg-query', '-W', '-f', fmt], text=True)
    debian = []
    for line in output.splitlines():
        name, version, source, source_version = line.split('\t')
        copyright_path = Path('/usr/share/doc') / name.split(':')[0] / 'copyright'
        target = root / 'debian' / (name + '.copyright')
        target.parent.mkdir(exist_ok=True)
        if copyright_path.is_file():
            shutil.copyfile(copyright_path, target)
        debian.append({'name': name, 'version': version, 'source': source, 'source_version': source_version,
                       'notice': str(target.relative_to(root)) if target.is_file() else None})
    shutil.copyfile(Path(sys.base_prefix) / 'lib' / ('python' + platform.python_version_tuple()[0] + '.' + platform.python_version_tuple()[1]) / 'LICENSE.txt', root / 'PYTHON-LICENSE.txt')
    inventory = {'python_version': platform.python_version(), 'architecture': platform.machine(),
                 'python': packages, 'debian': debian}
    (root / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    (root / 'README.txt').write_text('Runtime notices and exact package versions for this image.\n'
        'Python sources: versioned PyPI source distributions listed in inventory.json.\n'
        'Debian sources: matching source package/version via Debian Sources or snapshot.debian.org.\n'
        'CPython sources: https://www.python.org/downloads/source/ (exact version in inventory.json).\n'
        'Before distributing, accompany the image with its corresponding source bundle and build instructions.\n'
        'KamCine source license: /app/LICENSE; third party notices: /app/THIRD_PARTY_NOTICES.md.\n')
    missing = [p['name'] for p in packages if not p['notices']]
    missing += [p['name'] for p in debian if not p['notice']]
    if missing:
        raise RuntimeError('Missing license notices: ' + ', '.join(missing))


if __name__ == '__main__':
    collect(sys.argv[1])
