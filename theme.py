"""Persikadr's colors and original play mark."""
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF


APP_NAME = "Persikadr"
_CHEVRON = Path(__file__).with_name("assets") / "chevron.svg"

STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 14px; color: #3b2822; }
QMainWindow, QWidget#main, QScrollArea#workspace { background: #fff7f1; }
QFrame#sidebar { background: #fff0e6; border-right: 1px solid #f1dacc; }
QFrame#formatsDrawer { background: #fffdfb; border-right: 1px solid #ead2c3; }
QWidget#formatsBody { background: #fffdfb; }
QPushButton#formatsMenu, QPushButton#nav { text-align: left; padding: 10px 12px; }
QPushButton#formatsMenu { background: transparent; border-color: transparent; color: #744934; }
QPushButton#formatsMenu:hover, QPushButton#formatsMenu:checked, QPushButton#nav {
    background: #ffe0ce; border-color: #ffe0ce; color: #873a21;
}
QFrame#card { background: #fffdfb; border: 1px solid #efdbcf; border-radius: 18px; }
QFrame#drop { background: #fff0e5; border: 1px dashed #d69a79; border-radius: 18px; }
QLabel#brand { font-size: 24px; font-weight: 700; color: #71351f; }
QLabel#title { font-size: 32px; font-weight: 700; }
QLabel#section { font-size: 16px; font-weight: 650; }
QLabel#field { font-size: 13px; font-weight: 600; color: #613e2e; }
QLabel#muted { color: #785e51; font-size: 13px; }
QLabel#eyebrow { color: #9b472a; font-size: 11px; font-weight: 700; }
QLabel#formatList { color: #613e2e; padding: 12px; background: #fff0e5; border-radius: 12px; }
QPushButton {
    border: 1px solid #e8cdbd; border-radius: 10px; background: #fffdfb;
    padding: 8px 13px; font-weight: 600;
}
QPushButton:hover { background: #ffebde; border-color: #d99976; }
QPushButton:pressed { background: #ffdfcb; }
QPushButton:focus, QComboBox:focus { border-color: #b6512e; }
QPushButton:disabled { color: #8d776c; background: #f6ebe4; border-color: #ecddd3; }
QPushButton#primary { background: #a94426; color: white; border-color: #a94426; padding: 10px 17px; }
QPushButton#primary:hover { background: #91381f; border-color: #91381f; }
QPushButton#primary:disabled { background: #edd4c5; border-color: #edd4c5; color: #69463a; }
QPushButton#queueAction { padding: 6px 9px; }
QLineEdit, QComboBox, QPlainTextEdit {
    background: #fff8f3; border: 1px solid #ead4c6; border-radius: 9px;
    padding: 6px 9px; selection-background-color: #ffd9c3; selection-color: #3b2822;
}
QLineEdit:focus, QPlainTextEdit:focus { border-color: #b6512e; background: #fffdfb; }
QComboBox { padding: 7px 27px 7px 10px; min-height: 18px; }
QComboBox:disabled { color: #856d60; background: #f5eae3; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url("@CHEVRON@"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: #fffdfb; selection-background-color: #ffdfcb; selection-color: #3b2822; }
QTreeWidget { background: #fffdfb; border: none; outline: none; font-size: 13px; }
QTreeWidget::item { padding: 5px; border-bottom: 1px solid #f2e2d8; }
QTreeWidget::item:selected { background: #ffeadc; color: #6e311d; }
QHeaderView::section {
    background: #fff3eb; color: #74513f; border: none; border-bottom: 1px solid #efd9cc;
    padding: 7px 5px; font-size: 11px; font-weight: 600;
}
QProgressBar { border: none; background: #f1dfd3; border-radius: 4px; min-height: 7px; max-height: 7px; }
QProgressBar::chunk { background: #c15a33; border-radius: 4px; }
""".replace("@CHEVRON@", _CHEVRON.as_posix())


def play_icon() -> QIcon:
    canvas = QPixmap(128, 128)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ad4929"))
    painter.drawRoundedRect(0, 0, 128, 128, 32, 32)
    painter.setBrush(QColor("#fff4e9"))
    painter.drawPolygon(QPolygonF([QPointF(48, 35), QPointF(91, 64), QPointF(48, 93)]))
    painter.end()
    return QIcon(canvas)
