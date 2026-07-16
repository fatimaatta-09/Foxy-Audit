# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Foxy Audit desktop pet.

Build:   pyinstaller desktop/omni_fox.spec
Output:  dist/FoxyAudit(.exe / .app)  — onefile, windowed (no console).

Notes:
- PyQt6 Qt plugins (platforms, imageformats) are the thing that most often
  silently breaks a --onefile GUI build, so we collect_all("PyQt6").
- Data files (the sprite atlas, and the optional fonts/ dir if present) are
  bundled and resolved at runtime via fox_settings.resource_path() / sys._MEIPASS.
"""
import os

from PyInstaller.utils.hooks import collect_all

HERE = SPECPATH  # directory containing this .spec (injected by PyInstaller)

datas = [(os.path.join(HERE, "ultimate_fox_spritesheet.png"), ".")]
_fonts = os.path.join(HERE, "fonts")
if os.path.isdir(_fonts):
    datas.append((_fonts, "fonts"))

pyqt_datas, pyqt_binaries, pyqt_hidden = collect_all("PyQt6")
datas += pyqt_datas

a = Analysis(
    [os.path.join(HERE, "omni_fox.py")],
    pathex=[HERE],
    binaries=pyqt_binaries,
    datas=datas,
    hiddenimports=pyqt_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FoxyAudit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,            # --windowed: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,   # set by CI on macOS when an Apple Developer ID is provided
    entitlements_file=None,
    icon=None,                # TODO: add a real .ico/.icns once branding art exists
)

# macOS: also wrap the onefile binary in a .app bundle.
app = BUNDLE(
    exe,
    name="FoxyAudit.app",
    icon=None,
    bundle_identifier="tech.foxyaudit.desktop",
)
