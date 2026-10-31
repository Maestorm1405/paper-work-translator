# PyInstaller recipe for the desktop app. Build with: pyinstaller --noconfirm packaging/app.spec
import os
import sys
from PyInstaller.utils.hooks import collect_submodules

NAME = "Medical Paperwork Translator"

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "desktop_app.py")],
    pathex=[ROOT],
    datas=[  # the page, styles, scripts, and sample papers
        (os.path.join(ROOT, "paperwork_translator", "static"), "paperwork_translator/static"),
        (os.path.join(ROOT, "paperwork_translator", "samples"), "paperwork_translator/samples"),
    ],
    hiddenimports=collect_submodules("uvicorn") + ["python_multipart"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        bundle_identifier="io.github.maestorm1405.paperworktranslator",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSCameraUsageDescription": "The camera is used to scan your medical papers.",
        },
    )
