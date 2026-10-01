"""Shared, fixed dark appearance for Desktop V2 and its child tools.

Only presentation is changed here: no metadata, selection, or network hooks.
The palette also covers native Qt dialogs and widgets created after startup.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QListView, QPushButton, QTableView, QTreeView, QSizePolicy

COLORS = {
    "background": "#10141b", "sidebar": "#141922", "panel": "#191f29",
    "input": "#111720", "hover": "#202838", "border": "#303947",
    "text": "#e7edf6", "muted": "#a0aec2", "accent": "#7c9fff",
    "selection": "#253753", "green": "#8cdbb3", "amber": "#eac385",
    "danger": "#f3a6ab", "simulation": "#243143", "write": "#413527",
}
TABLE_ROW_HEIGHT = 32
PRIMARY_ACTIONS = frozenset({
    "Appliquer", "Appliquer série", "Appliquer tome", "Appliquer la sélection",
    "Valider et appliquer", "Tester et activer", "Appliquer les actions prévisualisées",
    "Appliquer tome(s) sélectionné(s)", "Ajouter et définir comme couverture",
})

# No universal QWidget selector: this avoids needless stylesheet matching
# across thousands of cells and leaves status colors supplied by delegates intact.
DARK_STYLESHEET = """
QMainWindow, QDialog { background: #10141b; color: #e7edf6; }
QWidget#desktopRoot, QScrollArea > QWidget > QWidget { background: #10141b; }
QWidget#globalContextBar { background: #191f29; border: 1px solid #303947; border-radius: 8px; }
QWidget#navigationPanel { background: #141922; border-radius: 8px; }
QLabel { color: #e7edf6; background: transparent; }
QLabel[appearance="muted"] { color: #a0aec2; }
QLabel[appearance="heading"] { font-size: 21px; font-weight: 600; }
QLabel[appearance="brand"] { font-size: 16px; font-weight: 600; padding: 10px 8px; color: #c4d5ff; }
QLabel[appearance="step"] { padding: 6px; border: 1px solid #303947; border-radius: 5px; color: #a0aec2; }
QLabel[appearance="cover"] { border: 1px solid #303947; border-radius: 6px; background: #111720; color: #a0aec2; }
QLabel[appearance="success"] { color: #8cdbb3; }
QLabel[appearance="warning"] { color: #eac385; }
QGroupBox { background: #191f29; border: 1px solid #303947; border-radius: 8px;
    margin-top: 14px; padding: 12px 8px 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; padding: 0 5px; color: #cbd7e8; }
QPushButton, QToolButton { color: #e7edf6; background: #232c3b; border: 1px solid #3b475a;
    border-radius: 6px; padding: 6px 10px; min-height: 18px; }
QPushButton:hover, QToolButton:hover { background: #2d394b; border-color: #657896; }
QPushButton:pressed, QToolButton:pressed { background: #253753; border-color: #7c9fff; }
QPushButton:focus, QToolButton:focus { border: 1px solid #7c9fff; }
QPushButton:disabled, QToolButton:disabled { background: #191f29; color: #748298; border-color: #303947; }
QPushButton[appearance="primary"] { background: #7c9fff; color: #101b31; border-color: #7c9fff; font-weight: 600; }
QPushButton[appearance="primary"]:hover { background: #99b4ff; border-color: #99b4ff; }
QPushButton[appearance="primary"]:pressed { background: #6689eb; }
QPushButton[appearance="primary"]:focus { border: 1px solid #e7edf6; }
QPushButton[appearance="primary"]:disabled { background: #293346; color: #8a98ae; border-color: #303947; }
QPushButton:flat { background: transparent; border-color: transparent; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {
    background: #111720; color: #e7edf6; border: 1px solid #3a4557; border-radius: 5px;
    padding: 5px 7px; min-height: 20px; selection-background-color: #253753;
    selection-color: #e7edf6; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color: #7c9fff; }
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled { color: #748298; border-color: #303947; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { width: 20px; border: 0; }
QComboBox QAbstractItemView { background: #191f29; color: #e7edf6;
    border: 1px solid #3a4557; selection-background-color: #253753; selection-color: #e7edf6; }
QCheckBox, QRadioButton { color: #cbd7e8; spacing: 7px; background: transparent; }
QCheckBox:disabled, QRadioButton:disabled { color: #748298; }
QCheckBox:focus, QRadioButton:focus { color: #c4d5ff; }
QTableView, QTreeView, QListView { background: #191f29; alternate-background-color: #1b222d;
    color: #e7edf6; border: 1px solid #303947; border-radius: 6px;
    gridline-color: #252e3b; selection-background-color: #253753; selection-color: #e7edf6; }
QTableView::item, QTreeView::item { padding: 4px 6px; border-bottom: 1px solid #252e3b; }
QTableView::item:selected, QTreeView::item:selected { background: #253753; color: #e7edf6; }
QTableView::item:hover, QTreeView::item:hover { background: #202838; }
QHeaderView { background: #171d26; }
QHeaderView::section { background: #171d26; color: #a0aec2; padding: 7px 8px;
    border: 0; border-bottom: 1px solid #303947; border-right: 1px solid #252e3b; font-weight: 500; }
QTableCornerButton::section { background: #171d26; border: 0; }
QListWidget#desktopNavigation { background: transparent; border: 0; padding: 4px; }
QListWidget#desktopNavigation::item { padding: 8px; border: 0; border-radius: 5px; }
QListWidget#desktopNavigation::item:selected { background: #273652; color: #dfe8ff; border-left: 3px solid #7c9fff; }
QListWidget#desktopNavigation::item:hover { background: #202838; }
QTabWidget::pane { border: 0; background: #10141b; }
QTabBar::tab { color: #a0aec2; background: #141922; padding: 8px 12px;
    border: 0; border-bottom: 2px solid #303947; }
QTabBar::tab:selected { color: #e7edf6; background: #191f29; border-bottom: 2px solid #7c9fff; }
QTabBar::tab:hover { background: #202838; }
QTabBar::tab:disabled { color: #748298; }
QSplitter::handle { background: #10141b; }
QSplitter::handle:hover { background: #303947; }
QScrollArea { border: 0; background: #10141b; }
QScrollBar:vertical { background: #141922; width: 11px; margin: 0; }
QScrollBar:horizontal { background: #141922; height: 11px; margin: 0; }
QScrollBar::handle:vertical { background: #455268; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:horizontal { background: #455268; border-radius: 4px; min-width: 28px; }
QScrollBar::handle:hover { background: #657896; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: 0; background: transparent; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu, QMenuBar { background: #191f29; color: #e7edf6; border: 1px solid #303947; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected, QMenuBar::item:selected { background: #253753; color: #e7edf6; }
QMenu::item:disabled { color: #748298; }
QMenu::separator { height: 1px; background: #303947; margin: 5px 10px; }
QToolTip { background: #232c3b; color: #e7edf6; border: 1px solid #657896; padding: 6px; }
QProgressBar { background: #202838; color: #cbd7e8; border: 0; border-radius: 4px; text-align: center; min-height: 12px; }
QProgressBar::chunk { background: #7c9fff; border-radius: 4px; }
QStatusBar { background: #141922; color: #a0aec2; border-top: 1px solid #303947; }
QStatusBar::item { border: 0; }
"""


class ContextLabel(QLabel):
    """Keep full context text/tooltips without forcing the window wider."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.WindowText))
        painter.setFont(self.font())
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, rect.width()))
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)


def set_appearance(widget, role):
    """Repolish a single status/control, not the entire application."""
    if widget.property("appearance") == role:
        return
    widget.setProperty("appearance", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class _TableAppearance(QObject):
    def eventFilter(self, widget, event):
        if event.type() == QEvent.Polish and isinstance(widget, QPushButton):
            if widget.text().replace("&", "").strip() in PRIMARY_ACTIONS and not widget.property("appearance"):
                widget.setProperty("appearance", "primary")
        if event.type() == QEvent.Polish and isinstance(widget, (QTableView, QTreeView, QListView)):
            if not widget.property("darkAppearanceReady"):
                widget.setProperty("darkAppearanceReady", True)
                if isinstance(widget, QTableView):
                    widget.setShowGrid(False)
                    widget.setAlternatingRowColors(True)
                    widget.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
                    # Six usable rows, also in late-created validation dialogs.
                    widget.setMinimumHeight(max(widget.minimumHeight(), 6 * TABLE_ROW_HEIGHT + 42))
                elif isinstance(widget, QTreeView):
                    widget.setAlternatingRowColors(True)
        return False


def apply_dark_theme(app: QApplication):
    """Idempotent, one-time setup shared by the launcher and embedded tools."""
    if app.property("komgaDarkTheme"):
        return
    app.setProperty("komgaDarkTheme", True)
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    palette = QPalette()
    roles = {
        QPalette.Window: "background", QPalette.WindowText: "text",
        QPalette.Base: "input", QPalette.AlternateBase: "panel", QPalette.Text: "text",
        QPalette.Button: "panel", QPalette.ButtonText: "text", QPalette.ToolTipBase: "panel",
        QPalette.ToolTipText: "text", QPalette.Highlight: "selection",
        QPalette.HighlightedText: "text", QPalette.Link: "accent", QPalette.LinkVisited: "accent",
        QPalette.PlaceholderText: "muted", QPalette.BrightText: "danger",
        QPalette.Light: "border", QPalette.Midlight: "border", QPalette.Mid: "border",
        QPalette.Dark: "background", QPalette.Shadow: "background",
        QPalette.Accent: "accent",
    }
    for role, name in roles.items():
        palette.setColor(role, QColor(COLORS[name]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.PlaceholderText):
        palette.setColor(QPalette.Disabled, role, QColor("#748298"))
    app.setPalette(palette)
    polisher = _TableAppearance(app)
    app.installEventFilter(polisher)
    app._komga_dark_table_appearance = polisher
    app.setStyleSheet(DARK_STYLESHEET)
