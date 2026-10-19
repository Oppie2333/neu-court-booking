"""Copy actual bundled dependency notices, without copying user data."""
import importlib.metadata as metadata
import json
import shutil
import sys
from pathlib import Path

RUNTIME = ['selenium', 'attrs', 'certifi', 'cffi', 'h11', 'idna', 'outcome', 'pycparser',
           'PySocks', 'sniffio', 'sortedcontainers', 'trio', 'trio-websocket',
           'typing_extensions', 'urllib3', 'websocket-client', 'wsproto', 'pyinstaller']


def main(destination):
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    versions = {}
    for name in RUNTIME:
        dist = metadata.distribution(name)
        versions[name] = dist.version
        copied = False
        for entry in dist.files or []:
            normalized = str(entry).replace('\\', '/')
            if ('.dist-info/' in normalized or '.egg-info/' in normalized) and any(
                    word in normalized.lower() for word in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    relative = normalized.split('.dist-info/', 1)[-1] if '.dist-info/' in normalized else source.name
                    dest = target / name / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    copied = True
        if not copied:
            raise RuntimeError(f'Missing license text for {name}')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.exists():
        raise RuntimeError('Python LICENSE.txt was not found')
    shutil.copy2(python_license, target / 'Python-LICENSE.txt')
    base = Path(sys.base_prefix)
    for source in base.glob('tcl/*/license.terms'):
        shutil.copy2(source, target / f'{source.parent.name}-license.terms')
    import selenium
    selenium_root = Path(selenium.__file__).parent
    notice = selenium_root / 'webdriver/common/selenium-manager-THIRD-PARTY-NOTICES.txt'
    shutil.copy2(notice, target / 'selenium-manager-THIRD-PARTY-NOTICES.txt')
    versions['Python'] = sys.version.split()[0]
    import tkinter
    versions['Tcl/Tk'] = str(tkinter.TclVersion) + '/' + str(tkinter.TkVersion)
    (target / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')


if __name__ == '__main__': main(sys.argv[1])
