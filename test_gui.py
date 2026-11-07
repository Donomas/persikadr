"""Qt offscreen integration checks with real batch conversion."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import app as gui
from test_backend import artifact_folder, make_source, inspect_and_decode


class GuiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")
        cls.qt.setStyleSheet(gui.STYLE)
        cls.folder = artifact_folder("qt")
        cls.source = make_source(cls.folder)
        cls.facts = []

    @classmethod
    def tearDownClass(cls):
        (cls.folder / "results.json").write_text(json.dumps(cls.facts, ensure_ascii=False, indent=2), encoding="utf-8")

    def setUp(self):
        settings_path = self.folder / (self._testMethodName + ".ini")
        self.settings_patch = patch.object(gui, "QSettings", side_effect=lambda *args: QSettings(str(settings_path), QSettings.Format.IniFormat))
        self.warning_patch = patch.object(gui.QMessageBox, "warning")
        self.info_patch = patch.object(gui.QMessageBox, "information")
        self.settings_patch.start()
        self.warning = self.warning_patch.start()
        self.info_patch.start()
        self.window = gui.MainWindow()
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        if self.window.worker is not None:
            self.window.cancel_batch()
            self.wait_for_batch()
        self.window.close()
        self.qt.processEvents()
        self.info_patch.stop()
        self.warning_patch.stop()
        self.settings_patch.stop()

    def choose_folder(self, name):
        folder = self.folder / name
        with patch.object(gui.QFileDialog, "getExistingDirectory", return_value=str(folder)):
            self.window.choose_folder()
        self.assertEqual(self.window.folder, folder)
        self.assertEqual(self.window.folder_label.text(), str(folder))
        self.assertEqual(self.window.settings.value("output_folder"), str(folder))
        self.window.settings.sync()
        return folder

    def wait_for_batch(self):
        deadline = time.monotonic() + 45
        while self.window.worker is not None and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(0.01)
        self.qt.processEvents()
        self.assertIsNone(self.window.worker, "Batch did not release the worker within 45 seconds")
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertFalse(self.window.cancel_button.isEnabled())
        for widget in (self.window.drop, self.window.remove_button, self.window.profile,
                       self.window.quality, self.window.folder_button):
            self.assertTrue(widget.isEnabled())

    def test_file_queue_profiles_and_folder_selection(self):
        self.assertFalse(self.window.start_button.isEnabled())
        self.window.add_files([str(self.source), str(self.source)])
        self.assertEqual(self.window.files, [self.source])
        self.assertEqual(self.window.tree.topLevelItemCount(), 1)
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertEqual({self.window.profile.itemData(i) for i in range(self.window.profile.count())}, set(gui.PROFILES))
        self.assertEqual({self.window.quality.itemData(i) for i in range(self.window.quality.count())}, {"balanced", "high", "small"})
        self.window.profile.setCurrentIndex(self.window.profile.findData("webm"))
        self.assertEqual(self.window.profile.currentData(), "webm")
        folder = self.choose_folder("selected-folder")
        reopened = gui.MainWindow()
        self.assertEqual(reopened.folder, folder)
        reopened.close()
        self.window.grab().save(str(self.folder / "window.png"))
        self.window.tree.topLevelItem(0).setSelected(True)
        self.window.remove_selected()
        self.assertEqual(self.window.files, [])
        self.assertEqual(self.window.tree.topLevelItemCount(), 0)
        self.assertFalse(self.window.start_button.isEnabled())
        self.window.add_files([str(self.folder / "not-found.avi")])
        self.warning.assert_called_once()
        self.facts.append({"queue_add_duplicate_remove": True, "profiles": list(gui.PROFILES), "folder": str(folder), "unsupported_or_missing_file_warning": True})

    def test_corrupt_file_does_not_stop_batch(self):
        corrupt = self.folder / "corrupt.avi"
        corrupt.write_bytes(b"not an AVI\n")
        folder = self.choose_folder("continue-batch")
        self.window.add_files([str(corrupt), str(self.source)])
        self.window.start_batch()
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertFalse(self.window.add_button.isEnabled())
        self.assertTrue(self.window.cancel_button.isEnabled())
        self.wait_for_batch()
        statuses = [self.window.tree.topLevelItem(i).text(2) for i in range(2)]
        self.assertTrue(statuses[0].startswith("Ошибка:"), statuses)
        self.assertTrue(statuses[1].startswith("Готово"), statuses)
        error_details = self.window.tree.topLevelItem(0).toolTip(2)
        self.assertGreater(len(error_details), len(statuses[0]))
        self.assertIn("Invalid data", error_details)
        self.assertIn("готово: 1 · ошибок: 1", self.window.status.text())
        self.assertEqual(self.window.progress.value(), 100)
        facts = inspect_and_decode(folder / "sample.mp4", "mp4")
        self.facts.append({"continued_after_corrupt_input": True, "statuses": statuses,
                           "worker_released": True, "controls_restored": True, **facts})

    def test_same_names_produce_separate_outputs(self):
        alternate = self.folder / "alternate"
        alternate.mkdir()
        second = alternate / self.source.name
        shutil.copyfile(self.source, second)
        folder = self.choose_folder("same-name-batch")
        self.window.add_files([str(self.source), str(second)])
        self.window.start_batch()
        self.wait_for_batch()
        self.assertIn("готово: 2 · ошибок: 0", self.window.status.text())
        files = [folder / "sample.mp4", folder / "sample (2).mp4"]
        facts = [inspect_and_decode(path, "mp4") for path in files]
        self.facts.append({"same_name_batch_outputs": facts, "worker_released": True})

    def test_leaving_dvd_preset_restores_editable_video_settings(self):
        for preset, profile, resolution in (("pal", "pal-dvd", (720, 576)), ("ntsc", "ntsc-dvd", (720, 480))):
            with self.subTest(preset=preset):
                self.window.preset.setCurrentIndex(self.window.preset.findData(preset))
                self.assertEqual(self.window.profile.currentData(), profile)
                self.assertEqual(self.window.resolution.currentData(), resolution)
                self.assertFalse(self.window.resolution.isEnabled())
                self.assertFalse(self.window.quality.isEnabled())

                self.window.preset.setCurrentIndex(0)
                self.assertEqual(self.window.profile.currentData(), "mp4")
                self.assertIsNone(self.window.resolution.currentData())
                for control in (self.window.resolution, self.window.quality, self.window.codec, self.window.acceleration):
                    self.assertTrue(control.isEnabled())

        self.window.preset.setCurrentIndex(self.window.preset.findData("pal"))
        dvd = self.window.profile.findData("ntsc-dvd")
        self.window.profile.setCurrentIndex(dvd)
        self.window.profile.activated.emit(dvd)
        self.assertEqual(self.window.preset.currentIndex(), 0)
        self.assertEqual(self.window.profile.currentData(), "ntsc-dvd")
        self.assertFalse(self.window.resolution.isEnabled())
        self.window.preset.activated.emit(0)
        self.assertEqual(self.window.profile.currentData(), "mp4")
        self.assertTrue(self.window.resolution.isEnabled())

    def test_crt_preset_inline_name_and_rename_after_encoding(self):
        folder = self.choose_folder("crt-rename")
        self.window.add_files([str(self.source)])
        self.window.preset.setCurrentIndex(self.window.preset.findData("crt"))
        self.assertEqual(self.window.profile.currentData(), "avi")
        self.assertEqual(self.window.resolution.currentData(), (640, 480))
        self.assertEqual(self.window.acceleration.currentData(), "cpu")
        item = self.window.tree.topLevelItem(0)
        self.window.tree.setCurrentItem(item)
        self.window.tree.itemWidget(item, 3).setText("Для телевизора")
        self.window.start_batch()
        self.wait_for_batch()
        first = folder / "Для телевизора.avi"
        self.assertTrue(first.is_file())
        self.assertTrue(self.window.rename_button.isEnabled())
        with patch.object(gui.QInputDialog, "getText", return_value=("Новое имя", True)):
            self.window.rename_button.click()
        self.assertFalse(first.exists())
        renamed = folder / "Новое имя.avi"
        inspect_and_decode(renamed, "avi")
        self.assertIn("640x480", gui.media_info(renamed))

        with patch.object(gui, "convert_file", side_effect=RuntimeError("repeat failed")):
            self.window.start_batch()
            self.wait_for_batch()
        self.assertTrue(item.text(2).startswith("Ошибка:"))
        self.assertFalse(self.window.rename_button.isEnabled())
        self.assertTrue(renamed.is_file())

    def test_cancel_restores_controls_and_leaves_no_output(self):
        source = make_source(self.folder, long=True)
        folder = self.choose_folder("cancel-batch")
        self.window.add_files([str(source)])
        self.window.profile.setCurrentIndex(self.window.profile.findData("webm"))
        self.window.start_batch()
        self.window.cancel_batch()
        self.wait_for_batch()
        self.assertTrue(self.window.status.text().startswith("Остановлено"))
        self.assertEqual(list(folder.glob("*")), [])
        self.facts.append({"cancel_status": self.window.status.text(), "worker_released": True,
                           "controls_restored": True, "no_cancelled_output": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
