"""Persikadr desktop media converter."""
from __future__ import annotations

from pathlib import Path
import sys
import threading

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSettings, QRect, QSize, QPropertyAnimation, QEasingCurve, QSignalBlocker, QStandardPaths
from PySide6.QtGui import QDesktopServices, QFont, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QFileDialog, QComboBox, QTreeWidget,
    QTreeWidgetItem, QProgressBar, QMessageBox, QAbstractItemView, QScrollArea,
    QLineEdit, QInputDialog, QDialog, QPlainTextEdit, QHeaderView,
)

from backend import (
    PROFILES, INPUT_EXTENSIONS, VIDEO_CODECS, GPU_ENCODERS, DVD_TARGETS, supported_video_codecs,
    output_path, output_stem, media_info, convert_file, ConversionCancelled,
)

from theme import APP_NAME, STYLE, play_icon


SIDEBAR_WIDTH = 190
DRAWER_WIDTH = 370
PRESETS = {
    "crt": ("ЭЛТ 4:3 · через медиаплеер", "avi", "mpeg4", (640, 480)),
    "pal": ("DVD PAL · 4:3", "pal-dvd", "mpeg2", (720, 576)),
    "ntsc": ("DVD NTSC · 4:3", "ntsc-dvd", "mpeg2", (720, 480)),
    "hdtv": ("HD-телевизор · Full HD", "mp4", "h264", (1920, 1080)),
}

def default_output_folder() -> Path:
    videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
    return Path(videos) if videos else Path.home() / "Videos"


def label(text, name=None):
    result = QLabel(text)
    if name:
        result.setObjectName(name)
    return result


class DropCard(QFrame):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.files_dropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()


class BatchWorker(QThread):
    item_status = Signal(int, str)
    item_output = Signal(int, str)
    progress_changed = Signal(int, float)
    batch_done = Signal(int, int, bool)

    def __init__(self, files, profile, quality, targets, codec, acceleration, resolution):
        super().__init__()
        self.files, self.profile, self.quality = files, profile, quality
        self.targets, self.codec, self.acceleration = targets, codec, acceleration
        self.resolution = resolution
        self.cancel = threading.Event()

    def run(self):
        successes = errors = 0
        for index, source in enumerate(self.files):
            if self.cancel.is_set():
                break
            engine = ""

            def report_encoder(name):
                nonlocal engine
                engine = name
                self.item_status.emit(index, f"Кодирование · {name}")

            try:
                target = self.targets[index]
                self.item_status.emit(index, "Конвертация…")
                convert_file(source, target, self.profile, self.quality,
                             lambda value, i=index: self.progress_changed.emit(i, value), self.cancel,
                             video_codec=self.codec, acceleration=self.acceleration,
                             encoder_status=report_encoder, resolution=self.resolution)
                successes += 1
                self.item_output.emit(index, str(target))
                self.item_status.emit(index, f"Готово · {target.name}\nКодирование: {engine}")
            except ConversionCancelled:
                self.item_status.emit(index, "Отменено")
                break
            except Exception as exc:
                errors += 1
                self.item_status.emit(index, f"Ошибка: {exc}")
            self.progress_changed.emit(index, 100.0)
        self.batch_done.emit(successes, errors, self.cancel.is_set())


class FileInfoWorker(QThread):
    details = Signal(str, str)

    def __init__(self, source, parent):
        super().__init__(parent)
        self.source = source

    def run(self):
        try:
            result = media_info(self.source)
        except (ValueError, RuntimeError, OSError) as exc:
            result = str(exc)
        self.details.emit(self.source.name, result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — видеоконвертер")
        self.brand_icon = play_icon()
        self.setWindowIcon(self.brand_icon)
        self.resize(1120, 840)
        self.setMinimumSize(850, 640)
        self.files = []
        self.worker = None
        self.info_worker = None
        self.acceleration_supported = True
        self.settings = QSettings("Luma", "VideoConverter")
        self.folder = Path(self.settings.value("output_folder", str(default_output_folder())))
        main = QWidget()
        main.setObjectName("main")
        self.setCentralWidget(main)
        layout = QHBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.build_sidebar())
        content_widget = QWidget()
        content_widget.setObjectName("main")
        content_widget.setMinimumHeight(690)
        content = QVBoxLayout(content_widget)
        content.setContentsMargins(22, 20, 22, 18)
        content.setSpacing(12)
        scroll = QScrollArea()
        scroll.setObjectName("workspace")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content_widget)
        workspace = QVBoxLayout()
        workspace.setSpacing(0)
        workspace.addWidget(scroll, 1)
        layout.addLayout(workspace, 1)
        content.addLayout(self.build_heading())

        content.addWidget(self.build_drop_card())

        content.addWidget(self.build_queue_card(), 1)

        content.addWidget(self.build_options_card())

        workspace.addLayout(self.build_controls())
        self.build_formats_drawer(main)
        self.preset.currentIndexChanged.connect(self.apply_preset)
        self.preset.activated.connect(self.unlock_custom_settings)
        for combo in (self.profile, self.codec, self.resolution):
            combo.activated.connect(self.clear_preset)
        self.refresh_codecs()

    def build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 25, 18, 20)
        side.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(self.brand_icon.pixmap(38, 38))
        side.addWidget(mark)
        side.addWidget(label(APP_NAME, "brand"))
        side.addWidget(label("ВИДЕОКОНВЕРТЕР", "eyebrow"))
        side.addSpacing(20)
        nav = QPushButton("Конвертер")
        nav.setObjectName("nav")
        side.addWidget(nav)
        nav.clicked.connect(lambda: self.add_button.setFocus())
        self.formats_button = QPushButton("Поддерживаемые\nфайлы   ›")
        self.formats_button.setObjectName("formatsMenu")
        self.formats_button.setCheckable(True)
        self.formats_button.setAccessibleName("Поддерживаемые файлы")
        self.formats_button.setToolTip("Показать входные форматы и варианты конвертации")
        self.formats_button.toggled.connect(self.toggle_formats)
        side.addWidget(self.formats_button)
        side.addStretch()
        side.addWidget(label("На вашем ПК", "section"))
        privacy = label("Файлы обрабатываются\nна компьютере.\nБез загрузки в облако.", "muted")
        privacy.setWordWrap(True)
        side.addWidget(privacy)
        return sidebar

    def build_heading(self):
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(label("РАБОЧАЯ ОБЛАСТЬ", "eyebrow"))
        titles.addWidget(label("Конвертация видео", "title"))
        titles.addWidget(label("Добавьте файлы, настройте формат и сохраните результат.", "muted"))
        heading.addLayout(titles)
        heading.addStretch()
        return heading

    def build_drop_card(self):
        self.drop = DropCard()
        drop_layout = QHBoxLayout(self.drop)
        drop_layout.setContentsMargins(16, 12, 16, 12)
        drop_layout.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(self.brand_icon.pixmap(34, 34))
        drop_layout.addWidget(mark)
        drop_text = QVBoxLayout()
        drop_text.addWidget(label("Добавьте ваши видео", "section"))
        hint = label("Перетащите сюда один или несколько файлов.", "muted")
        hint.setWordWrap(True)
        drop_text.addWidget(hint)
        drop_layout.addLayout(drop_text, 1)
        self.add_button = QPushButton("Выбрать файлы")
        self.add_button.clicked.connect(self.choose_files)
        drop_layout.addWidget(self.add_button)
        self.drop.files_dropped.connect(self.add_files)
        return self.drop

    def build_queue_card(self):
        queue_card = QFrame()
        queue_card.setObjectName("card")
        queue_card.setMaximumHeight(230)
        queue_layout = QVBoxLayout(queue_card)
        queue_layout.setContentsMargins(15, 12, 15, 12)
        queue_layout.setSpacing(8)
        queue_header = QHBoxLayout()
        queue_header.setSpacing(6)
        self.count_label = label("Очередь · 0 файлов", "section")
        queue_header.addWidget(self.count_label)
        queue_header.addStretch()
        self.info_button = QPushButton("О файле")
        self.info_button.setObjectName("queueAction")
        self.info_button.clicked.connect(self.show_file_info)
        queue_header.addWidget(self.info_button)
        self.rename_button = QPushButton("Переименовать")
        self.rename_button.setObjectName("queueAction")
        self.rename_button.clicked.connect(self.rename_completed)
        self.rename_button.setEnabled(False)
        queue_header.addWidget(self.rename_button)
        self.remove_button = QPushButton("Удалить")
        self.remove_button.setObjectName("queueAction")
        self.remove_button.clicked.connect(self.remove_selected)
        queue_header.addWidget(self.remove_button)
        self.info_button.setToolTip("Сведения о выбранном исходном файле")
        self.rename_button.setToolTip("Переименовать готовый файл для выбранной строки")
        self.remove_button.setToolTip("Удалить выбранные строки из очереди")
        for action in (self.info_button, self.rename_button, self.remove_button):
            action.setMinimumHeight(32)
        queue_layout.addLayout(queue_header)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["ФАЙЛ", "РАЗМЕР", "СТАТУС", "ИМЯ РЕЗУЛЬТАТА"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(2, 137)
        self.tree.setMinimumHeight(85)
        self.tree.setMaximumHeight(130)
        self.tree.setToolTip("Введите имя результата в поле справа. Выберите строку для просмотра сведений о файле.")
        self.tree.itemDoubleClicked.connect(self.queue_double_clicked)
        self.tree.currentItemChanged.connect(self.update_rename_button)
        queue_layout.addWidget(self.tree)
        self.common_name = QLineEdit()
        self.common_name.setPlaceholderText("Например, Отпуск → Отпуск 01, Отпуск 02")
        self.common_name.setAccessibleName("Общее имя для пачки")
        self.common_name.textChanged.connect(self.refresh_names)
        naming = QHBoxLayout()
        naming.addWidget(label("Общее имя", "field"))
        naming.addWidget(self.common_name, 1)
        queue_layout.addLayout(naming)
        return queue_card

    def build_options_card(self):
        options = QFrame()
        options.setObjectName("card")
        opts = QVBoxLayout(options)
        opts.setContentsMargins(15, 12, 15, 13)
        opts.setSpacing(9)
        opts.addWidget(label("Параметры конвертации", "section"))
        preset_row = QHBoxLayout()
        preset_col = QVBoxLayout()
        preset_col.addWidget(label("Пресет устройства", "field"))
        self.preset = QComboBox()
        self.preset.setToolTip("DVD создаёт MPEG-2 .mpg для подготовки диска. "
                               "Для просмотра файлов на ЭЛТ нужен совместимый медиаплеер.")
        self.preset.addItem("Свои настройки", None)
        for key, (name, _, _, _) in PRESETS.items():
            self.preset.addItem(name, key)
        preset_col.addWidget(self.preset)
        preset_row.addLayout(preset_col, 1)
        resolution_col = QVBoxLayout()
        resolution_col.addWidget(label("Разрешение", "field"))
        self.resolution = QComboBox()
        self.resolution.addItem("Как в исходнике", None)
        for width, height in [(640, 480), (720, 480), (720, 576), (854, 480),
                              (1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)]:
            self.resolution.addItem(f"{width} × {height}", (width, height))
        self.resolution.setToolTip("Видео вписывается в выбранный размер с сохранением пропорций.")
        resolution_col.addWidget(self.resolution)
        preset_row.addLayout(resolution_col, 1)
        opts.addLayout(preset_row)
        settings_row = QHBoxLayout()
        format_col = QVBoxLayout()
        format_col.addWidget(label("Формат результата", "field"))
        self.profile = QComboBox()
        for key, profile in PROFILES.items():
            self.profile.addItem(profile.label, key)
        format_col.addWidget(self.profile)
        settings_row.addLayout(format_col, 3)
        quality_col = QVBoxLayout()
        quality_col.addWidget(label("Качество", "field"))
        self.quality = QComboBox()
        for text, value in [("Баланс качества и размера", "balanced"), ("Высокое качество", "high"), ("Меньший размер", "small")]:
            self.quality.addItem(text, value)
        quality_col.addWidget(self.quality)
        settings_row.addLayout(quality_col, 2)
        opts.addLayout(settings_row)
        self.codec = QComboBox()
        self.acceleration = QComboBox()
        codec_col = QVBoxLayout()
        codec_col.addWidget(label("Видеокодек", "field"))
        codec_col.addWidget(self.codec)
        settings_row.insertLayout(1, codec_col, 2)
        acceleration_col = QVBoxLayout()
        acceleration_col.addWidget(label("Ускорение", "field"))
        acceleration_col.addWidget(self.acceleration)
        preset_row.addLayout(acceleration_col, 1)
        self.acceleration.addItem("Авто", "auto")
        self.acceleration.addItem("Процессор · CPU", "cpu")
        self.acceleration.addItem("Видеокарта · GPU", "gpu")
        self.acceleration.addItem("Комбинированный", "hybrid")
        self.acceleration.setToolTip(
            "CPU: чтение и кодирование на процессоре. GPU: аппаратное декодирование и кодирование. "
            "Комбинированный: чтение на CPU, обработка и кодирование на GPU при поддержке. "
            "Авто: выбирает аппаратный путь и при необходимости использует CPU."
        )
        for combo in (self.preset, self.resolution, self.acceleration, self.profile, self.codec, self.quality):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
        self.profile.currentIndexChanged.connect(self.refresh_codecs)
        self.codec.currentIndexChanged.connect(self.update_acceleration)
        save_row = QHBoxLayout()
        folder_col = QVBoxLayout()
        folder_col.addWidget(label("Сохранить в", "field"))
        self.folder_label = label(str(self.folder), "muted")
        self.folder_label.setWordWrap(True)
        folder_col.addWidget(self.folder_label)
        save_row.addLayout(folder_col, 1)
        self.folder_button = QPushButton("Изменить…")
        self.folder_button.clicked.connect(self.choose_folder)
        save_row.addWidget(self.folder_button)
        opts.addLayout(save_row)
        return options

    def build_controls(self):
        controls = QVBoxLayout()
        controls.setContentsMargins(22, 8, 22, 14)
        controls.setSpacing(10)
        self.status = label("Всё готово. Добавьте файлы, чтобы начать.", "muted")
        self.status.setWordWrap(True)
        controls.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        controls.addWidget(self.progress)
        footer = QHBoxLayout()
        self.open_button = QPushButton("Открыть папку")
        self.open_button.clicked.connect(self.open_folder)
        footer.addWidget(self.open_button)
        footer.addStretch()
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_batch)
        footer.addWidget(self.cancel_button)
        self.start_button = QPushButton("Конвертировать  →")
        self.start_button.setObjectName("primary")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_batch)
        footer.addWidget(self.start_button)
        controls.addLayout(footer)
        return controls

    def apply_preset(self):
        key = self.preset.currentData()
        if key is None:
            self.unlock_custom_settings()
            return
        _, profile, codec, resolution = PRESETS[key]
        self.profile.setCurrentIndex(self.profile.findData(profile))
        self.codec.setCurrentIndex(self.codec.findData(codec))
        self.select_resolution(resolution)

    def unlock_custom_settings(self):
        if self.preset.currentData() is None and self.profile.currentData() in DVD_TARGETS:
            self.profile.setCurrentIndex(self.profile.findData("mp4"))
            self.select_resolution(None)

    def select_resolution(self, resolution):
        for index in range(self.resolution.count()):
            if self.resolution.itemData(index) == resolution:
                self.resolution.setCurrentIndex(index)
                break

    def clear_preset(self):
        # A manual edit only removes the preset label. Keep the user's new value.
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(0)

    def refresh_codecs(self):
        self.codec.blockSignals(True)
        self.codec.clear()
        for key in supported_video_codecs(self.profile.currentData()):
            self.codec.addItem(VIDEO_CODECS[key][0], key)
        if not self.codec.count():
            self.codec.addItem("Только звук", None)
        self.codec.blockSignals(False)
        self.codec.setEnabled(self.codec.count() > 1)
        self.update_acceleration()
        self.update_resolution()
        self.refresh_names()

    def update_acceleration(self):
        supported = self.codec.currentData() in GPU_ENCODERS
        if not supported:
            self.acceleration.setCurrentIndex(self.acceleration.findData("cpu"))
        elif not self.acceleration_supported:
            self.acceleration.setCurrentIndex(self.acceleration.findData("auto"))
        self.acceleration_supported = supported
        self.acceleration.setEnabled(supported and self.worker is None)

    def update_resolution(self):
        profile = self.profile.currentData()
        fixed = DVD_TARGETS.get(profile)
        if fixed is not None:
            self.select_resolution(fixed)
        self.resolution.setEnabled(fixed is None and profile != "mp3" and self.worker is None)
        self.quality.setEnabled(fixed is None and self.worker is None)

    def planned_name(self, index):
        item = self.tree.topLevelItem(index)
        custom = self.tree.itemWidget(item, 3).text().strip()
        if custom:
            return custom
        common = self.common_name.text().strip()
        if not common:
            return None
        stem = output_stem(common, self.profile.currentData())
        digits = max(2, len(str(len(self.files))))
        return f"{stem} {index + 1:0{digits}d}"

    def refresh_names(self):
        profile = self.profile.currentData()
        for i, source in enumerate(self.files):
            item = self.tree.topLevelItem(i)
            try:
                custom = self.planned_name(i)
                stem = output_stem(custom, profile) if custom else source.stem
                name = stem + PROFILES[profile].extension
            except ValueError:
                name = "Некорректное имя"
            editor = self.tree.itemWidget(item, 3)
            editor.setPlaceholderText(name)
            editor.setToolTip(f"Результат: {name}")

    def queue_double_clicked(self, item, column):
        if column != 3:
            self.show_file_info()
            return
        self.tree.itemWidget(item, 3).setFocus()

    def update_rename_button(self):
        item = self.tree.currentItem()
        self.rename_button.setEnabled(
            self.worker is None and item is not None and bool(item.data(2, Qt.ItemDataRole.UserRole))
        )

    def remember_output(self, index, path):
        item = self.tree.topLevelItem(index)
        item.setData(2, Qt.ItemDataRole.UserRole, path)

    def rename_completed(self):
        item = self.tree.currentItem()
        if self.worker is not None or item is None:
            return
        path = item.data(2, Qt.ItemDataRole.UserRole)
        if not path:
            return
        current = Path(path)
        name, ok = QInputDialog.getText(self, "Переименовать готовый файл",
                                        "Новое имя без расширения:", text=current.stem)
        if not ok:
            return
        profile = next(key for key, spec in PROFILES.items() if spec.extension == current.suffix.lower())
        try:
            stem = output_stem(name, profile)
            if stem == current.stem:
                return
            target = output_path(current, current.parent, profile, name=stem)
            current.rename(target)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Переименовать готовый файл", str(exc))
            return
        item.setData(2, Qt.ItemDataRole.UserRole, str(target))
        self.set_item_status(self.tree.indexOfTopLevelItem(item), f"Готово · {target.name}")

    def show_file_info(self):
        selected = self.tree.currentItem()
        if self.info_worker is not None:
            return
        if selected is None:
            QMessageBox.information(self, "О файле", "Сначала выберите файл в очереди.")
            return
        source = self.files[self.tree.indexOfTopLevelItem(selected)]
        self.info_worker = FileInfoWorker(source, self)
        self.info_worker.details.connect(self.display_file_info)
        self.info_worker.finished.connect(self.file_info_finished)
        self.info_button.setEnabled(False)
        self.info_worker.start()

    def display_file_info(self, name, details):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("Сведения об исходнике")
        dialog.resize(690, 280)
        layout = QVBoxLayout(dialog)
        title = label(name, "section")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        field = QPlainTextEdit(details)
        field.setReadOnly(True)
        layout.addWidget(field)
        close = QPushButton("Закрыть")
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        dialog.show()

    def file_info_finished(self):
        self.info_worker.deleteLater()
        self.info_worker = None
        self.info_button.setEnabled(True)

    def build_formats_drawer(self, parent):
        # This overlay clips its contents while expanding, leaving the queue in place.
        self.formats_drawer = QFrame(parent)
        self.formats_drawer.setObjectName("formatsDrawer")
        self.formats_drawer.setGeometry(SIDEBAR_WIDTH, 0, 0, self.height())
        self.formats_inner = QFrame(self.formats_drawer)
        self.formats_inner.setObjectName("formatsDrawer")
        self.formats_inner.setGeometry(0, 0, DRAWER_WIDTH, self.height())
        inner = QVBoxLayout(self.formats_inner)
        inner.setContentsMargins(24, 30, 24, 24)
        inner.setSpacing(18)
        header = QHBoxLayout()
        header.addWidget(label("Поддержка файлов", "section"), 1)
        close = QPushButton("×")
        close.setAccessibleName("Закрыть подсказку форматов")
        close.setToolTip("Закрыть · Esc")
        close.setFixedSize(36, 36)
        close.setStyleSheet("padding: 0; font-size: 22px;")
        close.clicked.connect(lambda: self.formats_button.setChecked(False))
        header.addWidget(close)
        inner.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("formatsBody")
        details = QVBoxLayout(body)
        details.setContentsMargins(0, 0, 6, 0)
        details.setSpacing(14)
        audio = INPUT_EXTENSIONS & {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma", ".aiff", ".aif"}
        self.formats_video_text = label(" · ".join(ext[1:].upper() for ext in sorted(INPUT_EXTENSIONS - audio)))
        self.formats_audio_text = label(" · ".join(ext[1:].upper() for ext in sorted(audio)))
        output_formats = []
        for key, profile in PROFILES.items():
            codecs = supported_video_codecs(key)
            video = ", ".join(VIDEO_CODECS[codec][0] for codec in codecs)
            output_formats.append(f"{key.upper()} — {video}" if codecs else profile.label)
        self.formats_output_text = label("\n".join(output_formats))
        for heading, text_widget in [("Видео на входе", self.formats_video_text),
                                     ("Аудиофайлы на входе", self.formats_audio_text),
                                     ("Во что конвертировать", self.formats_output_text)]:
            details.addWidget(label(heading, "section"))
            text_widget.setWordWrap(True)
            text_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text_widget.setObjectName("formatList")
            details.addWidget(text_widget)
        note = label("Выберите один файл или несколько сразу. Аудиофайлы можно преобразовать в MP3.\n\nРасширение — подсказка: чтение конкретного файла зависит от его кодеков и целостности.", "muted")
        note.setWordWrap(True)
        details.addWidget(note)
        details.addStretch()
        scroll.setWidget(body)
        inner.addWidget(scroll, 1)
        self.drawer_animation = QPropertyAnimation(self.formats_drawer, b"geometry", self)
        self.drawer_animation.setDuration(240)
        self.drawer_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.drawer_animation.finished.connect(self.finish_drawer_animation)
        self.formats_drawer.hide()
        self.formats_escape = QShortcut(QKeySequence("Escape"), self)
        self.formats_escape.activated.connect(lambda: self.formats_button.setChecked(False))

    def toggle_formats(self, opened):
        self.drawer_animation.stop()
        self.formats_button.setText("Поддерживаемые\nфайлы   ‹" if opened else "Поддерживаемые\nфайлы   ›")
        self.formats_drawer.show()
        self.formats_drawer.raise_()
        self.drawer_animation.setStartValue(self.formats_drawer.geometry())
        self.drawer_animation.setEndValue(QRect(SIDEBAR_WIDTH, 0, DRAWER_WIDTH if opened else 0, self.centralWidget().height()))
        self.drawer_animation.start()

    def finish_drawer_animation(self):
        if not self.formats_button.isChecked():
            self.formats_drawer.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "formats_drawer"):
            self.drawer_animation.stop()
            self.formats_inner.resize(DRAWER_WIDTH, event.size().height())
            self.formats_drawer.setGeometry(SIDEBAR_WIDTH, 0, DRAWER_WIDTH if self.formats_button.isChecked() else 0, event.size().height())
            self.finish_drawer_animation()

    def choose_files(self):
        patterns = " ".join("*" + ext for ext in sorted(INPUT_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите видео", "", f"Видео ({patterns});;Все файлы (*)")
        self.add_files(files)

    def add_files(self, files):
        if self.worker is not None:
            return
        rejected = []
        for filename in files:
            path = Path(filename).resolve()
            if not path.is_file() or path.suffix.lower() not in INPUT_EXTENSIONS:
                rejected.append(path.name)
                continue
            if path in self.files:
                continue
            try:
                size = path.stat().st_size / (1024 * 1024)
            except OSError:
                rejected.append(path.name)
                continue
            self.files.append(path)
            item = QTreeWidgetItem([path.name, f"{size:.1f} МБ", "В очереди"])
            item.setSizeHint(0, QSize(0, 42))
            item.setToolTip(0, str(path))
            self.tree.addTopLevelItem(item)
            editor = QLineEdit()
            editor.setAccessibleName(f"Имя результата для {path.name}")
            editor.textChanged.connect(self.refresh_names)
            self.tree.setItemWidget(item, 3, editor)
        self.update_count()
        if rejected:
            QMessageBox.warning(self, "Не удалось добавить", "Неподдерживаемые или недоступные файлы:\n" + "\n".join(rejected[:10]))

    def update_count(self):
        self.count_label.setText(f"Очередь · файлов: {len(self.files)}")
        self.start_button.setEnabled(bool(self.files) and self.worker is None)
        self.refresh_names()

    def remove_selected(self):
        for row in sorted((self.tree.indexOfTopLevelItem(item) for item in self.tree.selectedItems()), reverse=True):
            self.tree.takeTopLevelItem(row)
            del self.files[row]
        self.update_count()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Куда сохранить результат", str(self.folder))
        if folder:
            self.folder = Path(folder)
            self.folder_label.setText(folder)
            self.settings.setValue("output_folder", folder)

    def open_folder(self):
        if not self.folder.is_dir():
            QMessageBox.information(self, "Папка результата", "Папка появится после первой конвертации.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))

    def start_batch(self):
        if not self.files or self.worker is not None:
            return
        reserved = set(self.files)
        targets = []
        profile = self.profile.currentData()
        try:
            for i, source in enumerate(self.files):
                target = output_path(source, self.folder, profile, reserved, name=self.planned_name(i))
                reserved.add(target)
                targets.append(target)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Имя результата", str(exc))
            return
        for i in range(self.tree.topLevelItemCount()):
            self.set_item_status(i, "В очереди")
            item = self.tree.topLevelItem(i)
            item.setData(2, Qt.ItemDataRole.UserRole, None)
            self.tree.itemWidget(item, 3).setReadOnly(True)
        self.progress.setValue(0)
        self.worker = BatchWorker(list(self.files), profile, self.quality.currentData(),
                                  targets, self.codec.currentData(), self.acceleration.currentData(),
                                  self.resolution.currentData() if profile != "mp3" else None)
        self.worker.item_status.connect(self.set_item_status)
        self.worker.item_output.connect(self.remember_output)
        self.worker.progress_changed.connect(self.update_progress)
        self.worker.batch_done.connect(self.batch_done)
        self.worker.finished.connect(self.worker_finished)
        for widget in (self.drop, self.remove_button, self.profile, self.quality, self.folder_button,
                       self.start_button, self.codec, self.acceleration, self.common_name,
                       self.preset, self.resolution, self.rename_button):
            widget.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Подготовка конвертации…")
        self.worker.start()

    def set_item_status(self, index, text):
        item = self.tree.topLevelItem(index)
        item.setText(2, text.splitlines()[0][:160])
        item.setToolTip(2, text)

    def update_progress(self, index, percent):
        self.progress.setValue(int((index + percent / 100) / len(self.files) * 100))
        self.status.setText(f"Файл {index + 1} из {len(self.files)} · {percent:.0f}%")

    def cancel_batch(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("Отмена…")

    def batch_done(self, successes, errors, cancelled):
        self.status.setText(f"{'Остановлено' if cancelled else 'Завершено'} · готово: {successes} · ошибок: {errors}")
        if not cancelled:
            self.progress.setValue(100)
        else:
            for i in range(self.tree.topLevelItemCount()):
                if self.tree.topLevelItem(i).text(2) == "В очереди":
                    self.set_item_status(i, "Не запущено")

    def worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        for widget in (self.drop, self.remove_button, self.profile, self.folder_button,
                       self.common_name, self.preset):
            widget.setEnabled(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.itemWidget(self.tree.topLevelItem(i), 3).setReadOnly(False)
        self.cancel_button.setEnabled(False)
        self.codec.setEnabled(self.codec.count() > 1)
        self.update_acceleration()
        self.update_resolution()
        self.update_rename_button()
        self.update_count()

    def closeEvent(self, event):
        if self.worker is not None:
            QMessageBox.information(self, "Конвертация выполняется", "Отмените конвертацию и дождитесь остановки перед закрытием.")
            event.ignore()
        elif self.info_worker is not None:
            QMessageBox.information(self, "Чтение файла", "Дождитесь чтения сведений об исходнике.")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    window = MainWindow()
    available = app.primaryScreen().availableGeometry()
    window.resize(min(1120, available.width() - 40), min(840, available.height() - 60))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
