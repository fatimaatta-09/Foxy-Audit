# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Foxy Audit desktop pet.

Build:   pyinstaller desktop/omni_fox.spec
Output:  Windows  dist/FoxyAudit.exe   — onefile, windowed
         macOS    dist/FoxyAudit.app   — onefile binary wrapped in a bundle
         Linux    dist/FoxyAudit       — onefile, plus foxy-audit.desktop

Notes:
- PyQt6 Qt plugins (platforms, imageformats) are the thing that most often
  silently breaks a --onefile GUI build, so we collect_all("PyQt6").
- Data files (the sprite atlas, and the brand fonts/ dir) are bundled and
  resolved at runtime via foxy_tokens.resource_path() / sys._MEIPASS.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all

HERE = SPECPATH  # directory containing this .spec (injected by PyInstaller)

datas = [
    (os.path.join(HERE, "ultimate_fox_spritesheet.png"), "."),
    (os.path.join(HERE, "logo.png"), "."),          # window/dialog/taskbar icon at runtime
]

# The single source of the build number. foxy_tokens._read_version() reads it
# from sys._MEIPASS in a frozen build; without this line every shipped build
# would say "Version unknown in this build".
_VERSION_FILE = os.path.join(HERE, os.pardir, "VERSION")
if os.path.isfile(_VERSION_FILE):
    datas.append((_VERSION_FILE, "."))
# Brand fonts (Unbounded / Space Mono) live at the repo root fonts/ dir;
# an in-desktop fonts/ dir wins if one is ever added.
for _fonts in (os.path.join(HERE, "fonts"), os.path.join(HERE, os.pardir, "fonts")):
    if os.path.isdir(_fonts):
        datas.append((_fonts, "fonts"))
        break

# Icon for the built executable / .app — foxy.ico (Windows) or foxy.icns (macOS).
_ICON = None
if sys.platform.startswith("win") and os.path.exists(os.path.join(HERE, "foxy.ico")):
    _ICON = os.path.join(HERE, "foxy.ico")
elif sys.platform == "darwin" and os.path.exists(os.path.join(HERE, "foxy.icns")):
    _ICON = os.path.join(HERE, "foxy.icns")
elif os.path.exists(os.path.join(HERE, "logo.png")):
    # Linux/BSD: PyInstaller accepts a PNG here, and the .desktop entry points
    # at the same file once installed.
    _ICON = os.path.join(HERE, "logo.png")

_VERSION = "0.0.0"
if os.path.isfile(_VERSION_FILE):
    with open(_VERSION_FILE, "r", encoding="utf-8") as _fh:
        _VERSION = _fh.read().strip() or _VERSION

pyqt_datas, pyqt_binaries, pyqt_hidden = collect_all("PyQt6")
datas += pyqt_datas

# keyring discovers its OS backend via entry points, which PyInstaller's
# static analysis can miss — collect it explicitly so the keychain works
# in the frozen build.
kr_datas, kr_binaries, kr_hidden = collect_all("keyring")
datas += kr_datas
pyqt_binaries += kr_binaries
pyqt_hidden += kr_hidden

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
    icon=_ICON,
)

# macOS: also wrap the onefile binary in a .app bundle. Guarded by platform —
# BUNDLE() on Windows or Linux emits a stray dist/FoxyAudit.app directory that
# is not a runnable anything, and the release workflow would upload it.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="FoxyAudit.app",
        icon=_ICON,
        bundle_identifier="tech.foxyaudit.desktop",   # matches autostart.BUNDLE_ID
        info_plist={
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            # The fox is a companion, not a document app: no Dock tile, no
            # menu bar. It lives in the tray/menu-extra area.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
