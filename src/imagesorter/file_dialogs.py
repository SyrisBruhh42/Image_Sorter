"""File selection without native-dialog history in isolated application profiles."""
from __future__ import annotations

import os
import re
from pathlib import Path

from PyQt6.QtCore import QDir, Qt
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeView,
    QVBoxLayout,
)

from .paths import get_config_dir, get_portable_root


def _isolated_profile() -> bool:
    selected = bool(os.environ.get("IMAGESORTER_PROFILE_ROOT")) or get_portable_root() is not None
    if selected:
        # Refuse a removed/unwritable/escaping profile before starting a dialog.
        get_config_dir()
    return selected


class IsolatedPathDialog(QDialog):
    """Read-only filesystem browser with no QSettings or OS recent-item writes.

    QFileDialog saves its own QtProject NativeFormat settings, even when the
    user cancels or native widgets are disabled. Its settings cannot be routed
    with setDefaultFormat on Windows/macOS, so do not instantiate it here.
    """

    def __init__(self, parent=None, caption="", directory="", file_filter="", *, directories=False):
        super().__init__(parent)
        self.setWindowTitle(caption or ("Select directory" if directories else "Open file"))
        self.setAccessibleName(self.windowTitle())
        self.resize(720, 480)
        self.directories = directories
        self.selected_path = ""
        self.current_directory = Path.cwd()
        layout = QVBoxLayout(self)
        location_row = QHBoxLayout()
        up = QPushButton("Up")
        up.setAccessibleName("Parent directory")
        up.clicked.connect(lambda: self.navigate(self.current_directory.parent))
        location_row.addWidget(up)
        self.location = QLineEdit()
        self.location.setAccessibleName("Directory location")
        self.location.returnPressed.connect(lambda: self.navigate(self.location.text()))
        location_row.addWidget(self.location)
        go = QPushButton("Go")
        go.clicked.connect(lambda: self.navigate(self.location.text()))
        location_row.addWidget(go)
        layout.addLayout(location_row)

        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)
        self.model.setOption(QFileSystemModel.Option.DontUseCustomDirectoryIcons)
        self.model.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setAccessibleName("Files and directories")
        self.tree.setModel(self.model)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.setColumnWidth(0, 360)
        self.tree.doubleClicked.connect(self._activate)
        self.tree.selectionModel().currentChanged.connect(self._selected)
        layout.addWidget(self.tree)
        self.hidden = QCheckBox("Show hidden files")
        self.hidden.toggled.connect(self._update_filters)
        layout.addWidget(self.hidden)
        self.filters = QComboBox()
        self.filters.setAccessibleName("File type")
        self.filters.addItems(file_filter.split(";;") if file_filter else ["All files (*)"])
        self.filters.currentIndexChanged.connect(self._update_filters)
        if not directories:
            layout.addWidget(self.filters)
        self.selection = QLineEdit()
        self.selection.setAccessibleName("Selected directory" if directories else "Selected file")
        self.selection.returnPressed.connect(self.accept)
        layout.addWidget(self.selection)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setAccessibleName("Selection status")
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Select folder" if directories else "Open")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_filters()
        initial = Path(directory).expanduser() if directory else Path.cwd()
        if initial.is_file():
            self.navigate(initial.parent)
            self.selection.setText(str(initial))
        elif not self.navigate(initial):
            self.navigate(Path.cwd())

    def _patterns(self) -> list[str]:
        match = re.search(r"\(([^()]*)\)\s*$", self.filters.currentText())
        return match.group(1).split() if match else ["*"]

    def _update_filters(self, *_args):
        flags = QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot
        if not self.directories:
            flags |= QDir.Filter.Files
        if self.hidden.isChecked():
            flags |= QDir.Filter.Hidden
        self.model.setFilter(flags)
        self.model.setNameFilters([] if self.directories else self._patterns())

    def navigate(self, directory) -> bool:
        path = Path(directory).expanduser()
        if not path.is_absolute():
            path = self.current_directory / path
        if not path.is_dir():
            self.error.setText("Choose an existing directory.")
            return False
        self.current_directory = path.absolute()
        self.location.setText(str(self.current_directory))
        self.tree.setRootIndex(self.model.setRootPath(str(self.current_directory)))
        self.selection.setText(str(self.current_directory) if self.directories else "")
        self.error.clear()
        return True

    def _selected(self, index, _previous):
        path = self.model.filePath(index)
        if path and (not self.directories or self.model.isDir(index)):
            self.selection.setText(path)

    def _activate(self, index):
        if self.model.isDir(index):
            self.navigate(self.model.filePath(index))
        elif not self.directories:
            self.selection.setText(self.model.filePath(index))
            self.accept()

    def accept(self):
        value = self.selection.text()
        if not value:
            self.error.setText("Choose a directory." if self.directories else "Choose a file.")
            return
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.current_directory / path
        if self.directories:
            valid = path.is_dir()
        else:
            valid = path.is_file() and QDir.match(self._patterns(), path.name)
        if not valid:
            self.error.setText("Choose an existing directory." if self.directories else "Choose an existing file of the selected type.")
            return
        self.selected_path = str(path.absolute())
        super().accept()


class FileDialog:
    """Compatibility facade for the application's three selection entry points."""

    @staticmethod
    def getExistingDirectory(parent=None, caption="", directory=""):
        if not _isolated_profile():
            return QFileDialog.getExistingDirectory(parent, caption, directory)
        dialog = IsolatedPathDialog(parent, caption, directory, directories=True)
        try:
            return dialog.selected_path if dialog.exec() == QDialog.DialogCode.Accepted else ""
        finally:
            dialog.deleteLater()

    @staticmethod
    def getOpenFileName(parent=None, caption="", directory="", filter=""):
        if not _isolated_profile():
            return QFileDialog.getOpenFileName(parent, caption, directory, filter)
        dialog = IsolatedPathDialog(parent, caption, directory, filter)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.selected_path, dialog.filters.currentText()
            return "", ""
        finally:
            dialog.deleteLater()
