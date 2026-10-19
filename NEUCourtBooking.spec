# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import selenium
import certifi
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
selenium_root = Path(selenium.__file__).parent
common = selenium_root / 'webdriver' / 'common'
manager = common / 'windows' / 'selenium-manager.exe'
data = [(certifi.where(), 'certifi'),
        (str(common / 'selenium-manager-THIRD-PARTY-NOTICES.txt'), 'selenium/webdriver/common')]
analysis = Analysis([str(root / 'main.py')], pathex=[str(root)],
    binaries=[(str(manager), 'selenium/webdriver/common/windows')],
    datas=data, hiddenimports=collect_submodules('selenium'), hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['pytest', 'unittest', 'pip', 'setuptools'], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='NEUCourtBooking',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='NEUCourtBooking')
