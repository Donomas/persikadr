"""Packaged entry point; --smoke-test verifies the actual frozen bundle."""
import json
from pathlib import Path
import sys


def smoke_test(folder):
    import os
    import subprocess
    import traceback
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    report = {"frozen": bool(getattr(sys, "frozen", False))}
    try:
        from converter import find_ffmpeg
        from backend import convert_file
        ffmpeg = find_ffmpeg()
        report["ffmpeg"] = ffmpeg
        source = folder / "sample.avi"
        target = folder / "sample.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-n", "-f", "lavfi", "-i",
                        "testsrc2=size=160x120:rate=10:duration=1", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1", "-c:v", "mpeg4", "-c:a", "pcm_s16le",
                        str(source)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        convert_file(source, target, "mp4")
        subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", str(target), "-f", "null", "-"],
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        report["conversion_and_decode"] = True
        from app import APP_NAME, QApplication, MainWindow, STYLE, default_output_folder
        from theme import _CHEVRON
        from PySide6.QtTest import QTest
        application = QApplication([])
        application.setStyleSheet(STYLE)
        window = MainWindow()
        assert window.windowTitle().startswith(APP_NAME)
        assert not window.windowIcon().isNull()
        assert _CHEVRON.is_file()
        report["branding"] = APP_NAME
        window.show()
        application.processEvents()
        window.formats_button.click()
        QTest.qWait(320)
        assert window.formats_drawer.width() == 370
        window.formats_button.click()
        QTest.qWait(320)
        assert not window.formats_drawer.isVisible()
        report["gui_and_drawer"] = True
        window.add_files([str(source)])
        window.preset.setCurrentIndex(window.preset.findData("pal"))
        assert window.resolution.currentData() == (720, 576)
        assert window.profile.currentData() == "pal-dvd"
        assert not window.resolution.isEnabled()
        editor = window.tree.itemWidget(window.tree.topLevelItem(0), 3)
        editor.setText("DVD test")
        assert window.planned_name(0) == "DVD test"
        report["presets_and_inline_names"] = True
        report["default_output_folder"] = str(default_output_folder())
        window.close()
        report["ok"] = True
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        sys.exit(smoke_test(sys.argv[2]))
    from app import main
    sys.exit(main())
