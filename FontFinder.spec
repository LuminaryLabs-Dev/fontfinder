# PyInstaller one-file desktop launcher. The UI is served locally by FastAPI.
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules


root = Path(SPECPATH)
llama_datas, llama_binaries, llama_hidden = collect_all("llama_cpp")
uvicorn_hidden = collect_submodules("uvicorn")

analysis = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root)],
    binaries=llama_binaries,
    datas=llama_datas + [(str(root / "frontend" / "dist"), "frontend/dist")],
    hiddenimports=llama_hidden + uvicorn_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="FontFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform != "darwin",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="FontFinder.app",
        icon=None,
        bundle_identifier="dev.luminarylabs.fontfinder",
    )
