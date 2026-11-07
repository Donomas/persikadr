"""Run with .venv/Scripts/python.exe build_exe.py."""
from pathlib import Path
import os

import PyInstaller.__main__
from PySide6.QtGui import QGuiApplication

from theme import APP_NAME, play_icon

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
os.environ["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "cache")
# Qt uses Windows ICU. Another application's ICU on PATH can expose
# different symbols under the same DLL name.
system32 = Path(os.environ["SystemRoot"]) / "System32"
os.environ["PATH"] = os.pathsep.join(
    entry for entry in os.environ["PATH"].split(os.pathsep)
    if entry and (Path(entry.strip('"')).resolve() == system32.resolve()
                  or not (Path(entry.strip('"')) / "icuuc.dll").is_file())
)
icon_file = ROOT / "build" / f"{APP_NAME}.ico"
icon_file.parent.mkdir(exist_ok=True)
qt = QGuiApplication([APP_NAME, "-platform", "offscreen"])
if not play_icon().pixmap(128, 128).save(str(icon_file), "ICO"):
    raise RuntimeError("Could not create the application icon.")

PyInstaller.__main__.run([
    "--clean", "--noconfirm", "--onefile", "--windowed", "--name", APP_NAME,
    "--icon", str(icon_file), "--add-data", "assets/chevron.svg:assets",
    "--collect-all", "imageio_ffmpeg", "--hidden-import", "PySide6.QtTest",
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "tkinter", "launcher.py",
])
