from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .ai_tagger import ModelDownloader
from .logger import logger
from .settings_manager import (
    RESERVED_HOTKEYS,
    SettingsManager,
    SettingsPersistenceError,
)


class ModelCheckWorker(QThread):
    """Asynchronously verifies AI model cryptographic integrity off the GUI thread."""
    check_finished = pyqtSignal(bool)

    def __init__(self, model_dir: str | None = None) -> None:
        super().__init__()
        self.model_dir = model_dir

    def run(self) -> None:
        from .reader_process import run_reader
        try:
            result, _ = run_reader({"action": "validate_model", "model_dir": self.model_dir},
                                   cancelled=self.isInterruptionRequested, timeout=60)
            self.check_finished.emit(bool(result["valid"]))
        except Exception:
            self.check_finished.emit(False)


class SettingsWindow(QDialog):
    """
    Settings interface with WCAG AAA accessibility options and input validation.
    """
    def __init__(self, settings_manager: SettingsManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings_manager
        self.downloader: ModelDownloader | None = None
        self.progress: QProgressDialog | None = None
        self.check_worker: ModelCheckWorker | None = None
        self._model_refresh_pending = False
        self._close_pending = False
        self._pending_result = None
        self.setWindowTitle("Image Sorter Settings")
        self.resize(800, 600)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Image Sorter Configuration Window")
        self.setAccessibleDescription("Tabbed settings interface to configure directories, hotkeys, AI tagging, and system performance.")
        self.init_ui()
        for widget in [self, *self.findChildren(QWidget)]:
            widget.installEventFilter(self)
        self.chk_tooltips.toggled.connect(lambda enabled: None if enabled else QToolTip.hideText())

    def eventFilter(self, watched, event):
        """
        Filters UI events for WCAG AAA tooltips and dynamic child widget event filter installation.

        :param watched: The QObject being monitored.
        :param event: The QEvent being processed.
        :return: True if the event was handled and should be consumed; False otherwise.
        """
        if event.type() == QEvent.Type.ToolTip and not self.chk_tooltips.isChecked():
            if not QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier:
                return True
        if event.type() == QEvent.Type.ChildAdded and isinstance(event.child(), QWidget):
            event.child().installEventFilter(self)
        return super().eventFilter(watched, event)

    def init_ui(self) -> None:
        """Builds the tabbed UI for settings."""
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Settings Categories")
        self.tabs.setAccessibleDescription("Use arrow keys to navigate between settings tabs.")

        # General Tab
        self.tab_general = QWidget()
        self.tab_general.setAccessibleName("General Settings Tab")
        self.init_general_tab()
        self.tabs.addTab(self.tab_general, "General & UI")

        # Hotkeys Tab
        self.tab_hotkeys = QWidget()
        self.tab_hotkeys.setAccessibleName("Hotkeys Configuration Tab")
        self.init_hotkeys_tab()
        self.tabs.addTab(self.tab_hotkeys, "Hotkeys")

        # AI Tab
        self.tab_ai = QWidget()
        self.tab_ai.setAccessibleName("AI & Metadata Configuration Tab")
        self.init_ai_tab()
        self.tabs.addTab(self.tab_ai, "AI & Metadata")
        from .ui_components import ComponentsPanel
        self.components_panel = ComponentsPanel(self)
        self.tabs.addTab(self.components_panel, "Optional Components")

        # Advanced/Hardware Tab
        self.tab_advanced = QWidget()
        self.tab_advanced.setAccessibleName("Advanced Performance Tab")
        self.init_advanced_tab()
        self.tabs.addTab(self.tab_advanced, "Advanced")

        layout.addWidget(self.tabs)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Save Settings")
        self.btn_save.setDefault(True)
        self.btn_save.setAccessibleName("Save Settings Button")
        self.btn_save.setAccessibleDescription("Saves all configured settings and closes the window.")
        self.btn_save.clicked.connect(self.save_settings)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)

        layout.addLayout(btn_layout)

        # Visible focus outline stylesheet for WCAG AAA
        self.setStyleSheet("""
            QWidget:focus {
                outline: 2px solid #3B82F6;
            }
        """)

        # Set up tab order
        self.setTabOrder(self.tabs, self.src_edit)
        self.setTabOrder(self.src_edit, self.src_btn)
        self.setTabOrder(self.src_btn, self.trash_edit)
        self.setTabOrder(self.trash_edit, self.trash_btn)
        self.setTabOrder(self.trash_btn, self.chk_fullscreen)
        self.setTabOrder(self.chk_fullscreen, self.chk_show_tags)
        self.setTabOrder(self.chk_show_tags, self.chk_tooltips)
        self.setTabOrder(self.chk_tooltips, self.theme_combo)
        self.setTabOrder(self.theme_combo, self.font_spin)
        self.setTabOrder(self.font_spin, self.btn_save)

    def init_general_tab(self) -> None:
        """Initializes the General & UI options tab."""
        layout = QFormLayout(self.tab_general)

        # Source Directory
        src_layout = QHBoxLayout()
        self.src_edit = QLineEdit(self.settings.get('directories', 'source') or "")
        self.src_edit.setAccessibleName("Source Directory Path Input")
        self.src_edit.setAccessibleDescription("Specifies the source directory path to scan images from.")
        self.src_edit.setToolTip("The directory where the application will scan for supported images.")
        self.src_edit.editingFinished.connect(lambda: self.src_edit.setText(self.src_edit.text().strip()))
        self.src_btn = QPushButton("Browse...")
        self.src_btn.setAccessibleName("Browse Source Directory Button")
        self.src_btn.setAccessibleDescription("Opens a file dialog to select the source directory.")
        self.src_btn.setToolTip("Open a file dialog to select the source directory.")
        self.src_btn.clicked.connect(lambda: self.browse_folder(self.src_edit))
        src_layout.addWidget(self.src_edit)
        src_layout.addWidget(self.src_btn)
        layout.addRow("Source Directory:", src_layout)

        # Trash Directory
        trash_layout = QHBoxLayout()
        self.trash_edit = QLineEdit(self.settings.get('directories', 'trash') or "")
        self.trash_edit.setAccessibleName("Trash Directory Path Input")
        self.trash_edit.setAccessibleDescription("Specifies the custom staging trash directory path.")
        self.trash_edit.setToolTip("The directory where deleted images will be moved.")
        self.trash_edit.editingFinished.connect(lambda: self.trash_edit.setText(self.trash_edit.text().strip()))
        self.trash_btn = QPushButton("Browse...")
        self.trash_btn.setAccessibleName("Browse Trash Directory Button")
        self.trash_btn.setAccessibleDescription("Opens a file dialog to select the trash directory.")
        self.trash_btn.setToolTip("Open a file dialog to select the trash directory.")
        self.trash_btn.clicked.connect(lambda: self.browse_folder(self.trash_edit))
        trash_layout.addWidget(self.trash_edit)
        trash_layout.addWidget(self.trash_btn)
        layout.addRow("Trash Directory:", trash_layout)

        # UI Options
        self.chk_fullscreen = QCheckBox("Start in Fullscreen Mode")
        self.chk_fullscreen.setAccessibleName("Start Fullscreen Checkbox")
        self.chk_fullscreen.setAccessibleDescription("Toggles launching in full screen by default.")
        self.chk_fullscreen.setToolTip("Launch the application in full screen by default.")
        self.chk_fullscreen.setChecked(self.settings.get('ui', 'fullscreen') or False)
        layout.addRow("UI Mode:", self.chk_fullscreen)

        self.chk_show_tags = QCheckBox("Show AI status in image details")
        self.chk_show_tags.setAccessibleName("Show AI Status Checkbox")
        self.chk_show_tags.setAccessibleDescription("Shows whether AI tagging is enabled in the image details.")
        self.chk_show_tags.setToolTip("Show the AI tagging status in image details; this does not display predicted tags.")
        self.chk_show_tags.setChecked(self.settings.get('ui', 'show_tags') is not False)
        layout.addRow("Image Details:", self.chk_show_tags)

        self.chk_tooltips = QCheckBox("Enable Helpful Tooltips")
        self.chk_tooltips.setAccessibleName("Enable Tooltips Checkbox")
        self.chk_tooltips.setAccessibleDescription("Toggles application-wide tooltips.")
        self.chk_tooltips.setToolTip("Toggle helpful tooltips throughout the application.")
        tooltips_enabled = self.settings.get('ui', 'tooltips_enabled')
        if tooltips_enabled is None:
            tooltips_enabled = True
        self.chk_tooltips.setChecked(tooltips_enabled)
        layout.addRow("Tooltips:", self.chk_tooltips)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark", "High Contrast"])
        current_theme = self.settings.get('ui', 'theme') or "Dark"
        self.theme_combo.setCurrentText(current_theme)
        self.theme_combo.setAccessibleName("Application Theme Selector")
        self.theme_combo.setAccessibleDescription("Selects visual color theme including High Contrast.")
        self.theme_combo.setToolTip("Select the visual theme. High Contrast is recommended for accessibility.")
        layout.addRow("Theme:", self.theme_combo)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(12, 72)
        self.font_spin.setValue(self.settings.get('ui', 'font_size') or 24)
        self.font_spin.setAccessibleName("Image Label Font Size Selector")
        self.font_spin.setAccessibleDescription("Adjusts text size for labels and empty states.")
        self.font_spin.setToolTip("Adjust the text size for labels and empty states for better readability.")
        layout.addRow("Image Label Font Size:", self.font_spin)

    def init_hotkeys_tab(self) -> None:
        """Initializes the Hotkeys configuration tab."""
        layout = QVBoxLayout(self.tab_hotkeys)

        self.hotkey_table = QTableWidget(0, 4)
        self.hotkey_table.setHorizontalHeaderLabels(["Hotkey", "Action (move/copy)", "Target Folder", "Auto Advance"])
        self.hotkey_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.hotkey_table.setAccessibleName("Hotkeys Configuration Table")
        self.hotkey_table.setAccessibleDescription("Table mapping single keys to move/copy operations and target folders.")
        self.hotkey_table.setToolTip("Configure keyboard shortcuts to rapidly move or copy images to designated folders.")
        layout.addWidget(self.hotkey_table)

        btn_layout = QHBoxLayout()
        self.btn_add_hotkey = QPushButton("Add Hotkey")
        self.btn_add_hotkey.setAccessibleName("Add Hotkey Row Button")
        self.btn_add_hotkey.setAccessibleDescription("Adds a new row to the hotkey table.")
        self.btn_add_hotkey.setToolTip("Create a new keyboard shortcut binding.")
        self.btn_add_hotkey.clicked.connect(lambda: self.add_hotkey_row())
        self.btn_del_hotkey = QPushButton("Remove Selected")
        self.btn_del_hotkey.setAccessibleName("Remove Selected Hotkey Button")
        self.btn_del_hotkey.setAccessibleDescription("Removes the selected hotkey row from table.")
        self.btn_del_hotkey.setToolTip("Remove the currently selected hotkey binding from the list.")
        self.btn_del_hotkey.clicked.connect(self.remove_hotkey_row)
        btn_layout.addWidget(self.btn_add_hotkey)
        btn_layout.addWidget(self.btn_del_hotkey)
        layout.addLayout(btn_layout)

        self.load_hotkeys_to_table()

    def init_ai_tab(self) -> None:
        """Initializes the AI and Metadata configuration tab."""
        layout = QFormLayout(self.tab_ai)

        ai_layout = QHBoxLayout()
        self.chk_ai_enable = QCheckBox("Enable AI Auto-Tagging")
        self.chk_ai_enable.setAccessibleName("Enable AI Auto-Tagging Checkbox")
        self.chk_ai_enable.setAccessibleDescription("Toggles automatic image classification using ONNX model.")
        self.chk_ai_enable.setToolTip("Automatically analyze images to generate relevant descriptive tags.")

        self.btn_download_model = QPushButton("Manage AI Model…")
        self.btn_download_model.setAccessibleName("Manage AI Model Button")
        self.btn_download_model.setAccessibleDescription("Opens Optional Components to manage or import the verified MobileNetV2 model.")
        self.btn_download_model.setToolTip("Manage or import the verified model in Optional Components. Custom ONNX model paths are not supported.")
        self.btn_download_model.clicked.connect(self.download_ai_model)

        self.refresh_ai_model_status()

        ai_layout.addWidget(self.chk_ai_enable)
        ai_layout.addWidget(self.btn_download_model)
        layout.addRow("AI Tagger:", ai_layout)

        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.0, 1.0)
        self.confidence_spin.setDecimals(3)
        self.confidence_spin.setSingleStep(0.05)
        threshold = self.settings.get('ai_tagger', 'threshold')
        self.confidence_spin.setValue(0.5 if threshold is None else threshold)
        self.confidence_spin.setAccessibleName("AI Confidence Threshold")
        self.confidence_spin.setAccessibleDescription("Minimum confidence from zero to one for generated tags; the default is 0.5.")
        self.confidence_spin.setToolTip("Keep up to 10 ranked predictions with confidence at or above this value. Higher values produce fewer tags.")
        layout.addRow("Minimum Tag Confidence:", self.confidence_spin)

        self.model_source_label = QLabel("Use the verified model in Optional Components. Existing model files can be imported there.")
        self.model_source_label.setWordWrap(True)
        layout.addRow("Model:", self.model_source_label)

        self.chk_exif = QCheckBox("Write Tags to EXIF (XPKeywords)")
        self.chk_exif.setAccessibleName("Write Tags to EXIF Checkbox")
        self.chk_exif.setAccessibleDescription("Embeds tags directly into JPEG EXIF XPKeywords metadata.")
        self.chk_exif.setToolTip("Embed generated tags directly into the image file's EXIF metadata.")
        self.chk_exif.setChecked(self.settings.get('metadata', 'write_exif') or False)
        layout.addRow("Metadata:", self.chk_exif)

        self.chk_sidecar = QCheckBox("Write Tags to Sidecar (.txt)")
        self.chk_sidecar.setAccessibleName("Write Tags to Sidecar Checkbox")
        self.chk_sidecar.setAccessibleDescription("Saves tags in a non-destructive sidecar .txt file.")
        self.chk_sidecar.setToolTip("Save generated tags in a separate text file alongside the image.")
        self.chk_sidecar.setChecked(self.settings.get('metadata', 'write_sidecar') or False)
        layout.addRow("", self.chk_sidecar)

    def init_advanced_tab(self) -> None:
        """Initializes the Advanced settings and Hardware check tab."""
        layout = QFormLayout(self.tab_advanced)

        self.worker_spin = QSpinBox()
        self.worker_spin.setRange(1, 32)
        self.worker_spin.setValue(self.settings.get('advanced', 'worker_threads') or 2)
        self.worker_spin.setAccessibleName("Worker Threads Spinbox")
        self.worker_spin.setAccessibleDescription("Configures maximum concurrent background worker threads.")
        self.worker_spin.setToolTip("Adjust background threads for processing files and AI tasks.")
        layout.addRow("Worker Threads:", self.worker_spin)

        self.btn_scan = QPushButton("Run Hardware Scan for Optimizations")
        self.btn_scan.setAccessibleName("Run Hardware Scan Button")
        self.btn_scan.setAccessibleDescription("Scans system hardware and updates optimal execution settings.")
        self.btn_scan.clicked.connect(self.run_hardware_scan)
        layout.addRow("Optimization:", self.btn_scan)

        self.lbl_scan_result = QLabel("")
        self.lbl_scan_result.setWordWrap(True)
        self.lbl_scan_result.setAccessibleName("Hardware Scan Result Output")
        layout.addRow("", self.lbl_scan_result)

    def run_hardware_scan(self) -> None:
        """Runs hardware scanner and displays recommendations."""
        try:
            # Inventory is cheap and does not initialize an ONNX session; full
            # provider execution qualification belongs to the component worker.
            import psutil
            hw = {"physical_cores": psutil.cpu_count(logical=False) or 1,
                  "logical_cores": psutil.cpu_count() or 1,
                  "memory_total_gb": round(psutil.virtual_memory().total / 1024**3, 2),
                  "onnx_providers": ["See component qualification"],
                  "suggestions": {"ai_provider": "Configured by optional provider component",
                                  "queue_threads": 1}}
            report = (
                f"<b>Physical Cores:</b> {hw['physical_cores']}<br>"
                f"<b>Logical Cores:</b> {hw['logical_cores']}<br>"
                f"<b>RAM:</b> {hw['memory_total_gb']} GB<br>"
                f"<b>ONNX Providers:</b> {', '.join(hw['onnx_providers'])}<br><br>"
                f"<b>Recommendations:</b><br>"
                f"AI Provider: {hw['suggestions']['ai_provider']}<br>"
                f"Worker Threads: {hw['suggestions']['queue_threads']}"
            )
            self.lbl_scan_result.setText(report)
            self.worker_spin.setValue(hw['suggestions']['queue_threads'])
            QMessageBox.information(self, "Hardware Scan", "Hardware scanned successfully. Recommendations applied to settings.")
        except Exception as e:
            logger.error(f"Hardware scan failed: {e}")
            QMessageBox.warning(self, "Error", f"Failed to run hardware scan: {e}")

    def browse_folder(self, line_edit: QLineEdit) -> None:
        """Opens directory selection dialog."""
        folder = QFileDialog.getExistingDirectory(self, "Select Directory", line_edit.text().strip())
        if folder:
            line_edit.setText(os.path.normpath(folder))

    def add_hotkey_row(self, key: str = "", action: str = "move", folder: str = "", auto_advance: bool = True) -> None:
        """
        Adds a new row to the hotkey table with configured shortcuts and target folders.

        :param key: Single-character key binding string.
        :param action: Action type ('move' or 'copy').
        :param folder: Target directory path for file operations.
        :param auto_advance: Whether to automatically navigate to the next image after action.
        """
        row = self.hotkey_table.rowCount()
        self.hotkey_table.insertRow(row)

        key_item = QTableWidgetItem(key)
        self.hotkey_table.setItem(row, 0, key_item)

        label_key = key.strip() if key and key.strip() else "row"

        action_combo = QComboBox()
        action_combo.addItems(["move", "copy"])
        action_combo.setCurrentText(action)
        action_combo.setAccessibleName(f"Action for hotkey {label_key}")
        self.hotkey_table.setCellWidget(row, 1, action_combo)

        folder_widget = QWidget()
        folder_layout = QHBoxLayout(folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)

        folder_edit = QLineEdit(folder)
        folder_edit.setAccessibleName(f"Target folder for hotkey {key}")
        folder_edit.editingFinished.connect(lambda: folder_edit.setText(folder_edit.text().strip()))
        folder_btn = QPushButton("...")
        folder_btn.setFixedWidth(30)
        folder_btn.setAccessibleName(f"Browse target folder for hotkey {key}")
        folder_btn.setAccessibleDescription("Opens a file dialog to select the target folder for this hotkey.")
        folder_btn.setToolTip("Browse to select target folder for this hotkey.")
        folder_btn.clicked.connect(lambda: self.browse_folder(folder_edit))

        folder_layout.addWidget(folder_edit)
        folder_layout.addWidget(folder_btn)

        self.hotkey_table.setCellWidget(row, 2, folder_widget)

        advance_chk = QCheckBox()
        advance_chk.setChecked(auto_advance)
        advance_chk.setAccessibleName(f"Auto Advance for hotkey {label_key}")
        chk_widget = QWidget()
        chk_layout = QHBoxLayout(chk_widget)
        chk_layout.addWidget(advance_chk)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        self.hotkey_table.setCellWidget(row, 3, chk_widget)

    def remove_hotkey_row(self) -> None:
        """Removes the selected hotkey row."""
        curr = self.hotkey_table.currentRow()
        if curr >= 0:
            self.hotkey_table.removeRow(curr)

    def load_hotkeys_to_table(self) -> None:
        """Populates hotkey table from settings."""
        hotkeys = self.settings.get('hotkeys')
        if not hotkeys:
            return

        for key, config in hotkeys.items():
            self.add_hotkey_row(key, config.get("action", "move"), config.get("folder", ""), config.get("auto_advance", True))

    def refresh_ai_model_status(self) -> None:
        """Verify optional model files without hashing them on the GUI thread."""
        if self.check_worker and self.check_worker.isRunning():
            self._model_refresh_pending = True
            return
        self.btn_download_model.setText("Checking Model…")
        self.btn_download_model.setEnabled(False)
        self.chk_ai_enable.setEnabled(False)
        worker = ModelCheckWorker()
        self.check_worker = worker
        worker.check_finished.connect(self._on_model_check_finished)
        worker.finished.connect(self._on_model_check_thread_finished)
        worker.start()

    def _on_model_check_thread_finished(self) -> None:
        self.check_worker = None
        if self._close_pending:
            self._finish_pending_close()
        elif self._model_refresh_pending:
            self._model_refresh_pending = False
            self.refresh_ai_model_status()

    def _on_model_check_finished(self, is_valid: bool) -> None:
        """Callback when background model check finishes."""
        self.btn_download_model.setText("Manage AI Model…")
        self.btn_download_model.setEnabled(True)
        if is_valid:
            self.chk_ai_enable.setEnabled(True)
            saved_enabled = self.settings.get('ai_tagger', 'enabled') or False
            self.chk_ai_enable.setChecked(saved_enabled)
        else:
            self.chk_ai_enable.setChecked(False)
            self.chk_ai_enable.setEnabled(False)

    def download_ai_model(self) -> None:
        """Opens verified component management without downloading or selecting arbitrary paths."""
        self.tabs.setCurrentWidget(self.components_panel)
        self.components_panel.component.setCurrentText("ai.mobilenet-v2")

    def cancel_download(self) -> None:
        """Requests interruption for active download without making false success claims."""
        if self.downloader and self.downloader.isRunning():
            logger.info("User requested cancellation of model download.")
            self.downloader.requestInterruption()

    def on_download_finished(self, success: bool, msg: str) -> None:
        """Handles model download completion or cancellation safely."""
        if self.progress:
            self.progress.close()
        if self._close_pending:
            return  # Closing must not open another modal completion dialog.

        was_cancelled = False
        if self.downloader and self.downloader.isInterruptionRequested():
            was_cancelled = True

        if not self._close_pending:
            self.refresh_ai_model_status()

        if was_cancelled:
            QMessageBox.information(self, "Download Cancelled", "Model download was cancelled by user.")
        elif success:
            QMessageBox.information(self, "Success", "Model downloaded and verified successfully! You can now enable AI tagging.")
        else:
            QMessageBox.critical(self, "Error", f"Failed to download model: {msg}")

    def keyPressEvent(self, event) -> None:
        """Dismiss settings dialog when Escape key is pressed."""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        """Cancel optional work and close once threads finish, without GUI waits."""
        downloader_running = bool(self.downloader and self.downloader.isRunning())
        checker_running = bool(self.check_worker and self.check_worker.isRunning())
        component_running = bool(self.components_panel.process)
        if downloader_running or checker_running or component_running:
            event.ignore()
            self._close_pending = True
            self._pending_result = QDialog.DialogCode.Rejected
            self.setEnabled(False)
            if self.downloader:
                self.downloader.requestInterruption()
            if self.check_worker:
                self.check_worker.requestInterruption()
            self.components_panel.cancel()
            QTimer.singleShot(50, self._finish_pending_close)
            return
        super().closeEvent(event)

    def _finish_pending_close(self) -> None:
        if not self._close_pending:
            return
        if self.components_panel.process or (self.downloader and self.downloader.isRunning()) or (
            self.check_worker and self.check_worker.isRunning()
        ):
            QTimer.singleShot(50, self._finish_pending_close)
            return
        self._close_pending = False
        result, self._pending_result = self._pending_result, None
        super().done(QDialog.DialogCode.Rejected if result is None else result)

    def done(self, result):
        if self.components_panel.process or (self.check_worker and self.check_worker.isRunning()) or (
            self.downloader and self.downloader.isRunning()
        ):
            self._close_pending = True
            self._pending_result = result
            self.components_panel.cancel()
            if self.check_worker:
                self.check_worker.requestInterruption()
            if self.downloader:
                self.downloader.requestInterruption()
            QTimer.singleShot(50, self._finish_pending_close)
            return
        super().done(result)

    def save_settings(self) -> None:
        """
        Validates all inputs across tabs and applies changes atomically.
        If validation or persistence fails, reports error and retains user dialog state.
        """
        changes: dict[str, dict[str, Any]] = {}

        # 1. Directories Validation
        src_dir = self.src_edit.text().strip()
        if src_dir:
            if os.path.isfile(src_dir):
                QMessageBox.warning(self, "Validation Error", f"Source directory path points to a file, not a directory: {src_dir}")
                return
            if not os.path.exists(src_dir):
                QMessageBox.warning(self, "Validation Error", f"Source directory does not exist: {src_dir}")
                return

        trash_dir = self.trash_edit.text().strip()
        if trash_dir:
            if os.path.isfile(trash_dir):
                QMessageBox.warning(self, "Validation Error", f"Trash directory path points to a file, not a directory: {trash_dir}")
                return
            if not os.path.exists(trash_dir):
                QMessageBox.warning(self, "Validation Error", f"Trash directory does not exist: {trash_dir}")
                return

        changes['directories'] = {
            'source': os.path.normpath(src_dir) if src_dir else "",
            'trash': os.path.normpath(trash_dir) if trash_dir else ""
        }

        # 2. UI Validation
        ui_sec = self.settings.get('ui') or {}
        ui_sec['fullscreen'] = self.chk_fullscreen.isChecked()
        ui_sec['tooltips_enabled'] = self.chk_tooltips.isChecked()
        ui_sec['show_tags'] = self.chk_show_tags.isChecked()
        ui_sec['theme'] = self.theme_combo.currentText()
        ui_sec['font_size'] = self.font_spin.value()
        changes['ui'] = ui_sec

        # 3. Metadata Validation
        meta_sec = self.settings.get('metadata') or {}
        meta_sec['write_exif'] = self.chk_exif.isChecked()
        meta_sec['write_sidecar'] = self.chk_sidecar.isChecked()
        changes['metadata'] = meta_sec

        # 4. AI Tagger Validation
        ai_sec = self.settings.get('ai_tagger') or {}
        ai_sec['enabled'] = self.chk_ai_enable.isChecked()
        ai_sec['threshold'] = self.confidence_spin.value()
        changes['ai_tagger'] = ai_sec

        # 5. Advanced Validation
        adv_sec = self.settings.get('advanced') or {}
        adv_sec['worker_threads'] = self.worker_spin.value()
        changes['advanced'] = adv_sec

        # 6. Hotkeys Validation
        hotkeys: dict[str, dict[str, Any]] = {}
        seen_keys: set[str] = set()

        for row in range(self.hotkey_table.rowCount()):
            key_item = self.hotkey_table.item(row, 0)
            raw_key = key_item.text().strip() if key_item else ""
            if not raw_key:
                continue

            key = raw_key.upper()
            if len(key) != 1:
                QMessageBox.warning(self, "Validation Error", f"Hotkey '{raw_key}' must be a single character.")
                return

            if key in RESERVED_HOTKEYS:
                QMessageBox.warning(self, "Validation Error", f"Hotkey '{key}' is reserved by the application ({', '.join(sorted(RESERVED_HOTKEYS))}).")
                return

            if key in seen_keys:
                QMessageBox.warning(self, "Validation Error", f"Duplicate hotkey binding detected for key '{key}'.")
                return
            seen_keys.add(key)

            action_combo = self.hotkey_table.cellWidget(row, 1)
            action = action_combo.currentText() if action_combo else "move"

            folder_widget = self.hotkey_table.cellWidget(row, 2)
            folder_edit = folder_widget.layout().itemAt(0).widget() if folder_widget else None
            folder = folder_edit.text().strip() if folder_edit else ""

            if folder:
                if os.path.isfile(folder):
                    QMessageBox.warning(self, "Validation Error", f"Target path for hotkey '{key}' points to a file, not a directory: {folder}")
                    return
                if not os.path.exists(folder):
                    QMessageBox.warning(self, "Validation Error", f"Target folder for hotkey '{key}' does not exist: {folder}")
                    return

            chk_widget = self.hotkey_table.cellWidget(row, 3)
            advance_chk = chk_widget.layout().itemAt(0).widget() if chk_widget else None
            auto_advance = advance_chk.isChecked() if advance_chk else True

            hotkeys[key] = {
                "action": action,
                "folder": os.path.normpath(folder) if folder else "",
                "auto_advance": auto_advance
            }

        changes['hotkeys'] = hotkeys

        # Atomic commit of all section changes
        try:
            self.settings.apply_changes(changes)
        except SettingsPersistenceError as e:
            logger.error(f"Failed to persist settings from UI dialog: {e}")
            QMessageBox.critical(self, "Persistence Error", f"Failed to save settings to disk: {e}")
            return
        except Exception as e:
            logger.error(f"Unexpected error applying settings: {e}")
            QMessageBox.critical(self, "Save Error", f"An unexpected error occurred while saving settings: {e}")
            return

        QMessageBox.information(self, "Success", "Settings saved successfully.")
        self.accept()
