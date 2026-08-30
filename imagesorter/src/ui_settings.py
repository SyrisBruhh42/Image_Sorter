import os
from typing import Dict, Any
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QLabel, QFileDialog, QTabWidget, QFormLayout, QCheckBox,
    QComboBox, QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QProgressDialog
)
from PyQt6.QtCore import Qt
from src.settings_manager import SettingsManager
from src.hardware_scan import scan_hardware
from src.ai_tagger import ModelDownloader
from src.paths import get_data_dir
from src.logger import logger

class SettingsWindow(QWidget):
    """
    Settings interface with WCAG AAA accessibility options and input validation.
    """
    def __init__(self, settings_manager: SettingsManager) -> None:
        super().__init__()
        self.settings = settings_manager
        self.setWindowTitle("Image Sorter Settings")
        self.resize(800, 600)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Image Sorter Configuration Window")
        self.setAccessibleDescription("Tabbed settings interface to configure directories, hotkeys, AI tagging, and system performance.")
        self.init_ui()

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

    def init_general_tab(self) -> None:
        """Initializes the General & UI options tab."""
        layout = QFormLayout(self.tab_general)

        # Source Directory
        src_layout = QHBoxLayout()
        self.src_edit = QLineEdit(self.settings.get('directories', 'source') or "")
        self.src_edit.setAccessibleName("Source Directory Path Input")
        self.src_edit.setAccessibleDescription("Specifies the source directory path to scan images from.")
        self.src_edit.setToolTip("The directory where the application will scan for supported images.")
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
        self.chk_ai_enable.setChecked(self.settings.get('ai_tagger', 'enabled') or False)

        self.btn_download_model = QPushButton("Download Model")
        self.btn_download_model.setAccessibleName("Download AI Model Button")
        self.btn_download_model.setAccessibleDescription("Downloads required MobileNetV2 ONNX model into data directory.")
        self.btn_download_model.setToolTip("Download the required ONNX model for the AI Auto-Tagger to function.")
        self.btn_download_model.clicked.connect(self.download_ai_model)

        model_dir = str(get_data_dir() / "models")
        model_exists = os.path.exists(os.path.join(model_dir, "mobilenetv2.onnx")) or os.path.exists("models/mobilenetv2.onnx")
        self.chk_ai_enable.setEnabled(model_exists)

        if model_exists:
            self.btn_download_model.setText("Model Downloaded")
            self.btn_download_model.setEnabled(False)

        ai_layout.addWidget(self.chk_ai_enable)
        ai_layout.addWidget(self.btn_download_model)
        layout.addRow("AI Tagger:", ai_layout)

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
            hw = scan_hardware()
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
        folder = QFileDialog.getExistingDirectory(self, "Select Directory", line_edit.text())
        if folder:
            line_edit.setText(os.path.normpath(folder))

    def add_hotkey_row(self, key: str = "", action: str = "move", folder: str = "", auto_advance: bool = True) -> None:
        """Adds a new row to the hotkey table."""
        row = self.hotkey_table.rowCount()
        self.hotkey_table.insertRow(row)

        key_item = QTableWidgetItem(key)
        self.hotkey_table.setItem(row, 0, key_item)

        action_combo = QComboBox()
        action_combo.addItems(["move", "copy"])
        action_combo.setCurrentText(action)
        action_combo.setAccessibleName(f"Action for hotkey {key}")
        self.hotkey_table.setCellWidget(row, 1, action_combo)

        folder_widget = QWidget()
        folder_layout = QHBoxLayout(folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)

        folder_edit = QLineEdit(folder)
        folder_edit.setAccessibleName(f"Target folder for hotkey {key}")
        folder_btn = QPushButton("...")
        folder_btn.setFixedWidth(30)
        folder_btn.setAccessibleName(f"Browse target folder for hotkey {key}")
        folder_btn.clicked.connect(lambda: self.browse_folder(folder_edit))

        folder_layout.addWidget(folder_edit)
        folder_layout.addWidget(folder_btn)

        self.hotkey_table.setCellWidget(row, 2, folder_widget)

        advance_chk = QCheckBox()
        advance_chk.setChecked(auto_advance)
        advance_chk.setAccessibleName(f"Auto Advance for hotkey {key}")
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

    def download_ai_model(self) -> None:
        """Initiates model download."""
        reply = QMessageBox.question(self, 'Download Model', 'This will download approx 15MB. Continue?',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.progress = QProgressDialog("Downloading model...", "Cancel", 0, 100, self)
            self.progress.setWindowModality(Qt.WindowModality.WindowModal)

            model_dir = str(get_data_dir() / "models")
            self.downloader = ModelDownloader(model_dir)
            self.downloader.progress.connect(self.progress.setValue)
            self.downloader.finished.connect(self.on_download_finished)
            self.downloader.start()

    def on_download_finished(self, success: bool, msg: str) -> None:
        """Handles model download completion."""
        self.progress.close()
        if success:
            QMessageBox.information(self, "Success", "Model downloaded and verified successfully! You can now enable AI tagging.")
            self.btn_download_model.setText("Model Downloaded")
            self.btn_download_model.setEnabled(False)
            self.chk_ai_enable.setEnabled(True)
        else:
            QMessageBox.critical(self, "Error", f"Failed to download model: {msg}")

    def save_settings(self) -> None:
        """Validates and saves settings."""
        src_dir = self.src_edit.text()
        if src_dir and not os.path.exists(src_dir):
            QMessageBox.warning(self, "Validation Error", f"Source directory does not exist: {src_dir}")
            return

        self.settings.set('directories', 'source', os.path.normpath(src_dir) if src_dir else "")

        trash_dir = self.trash_edit.text()
        self.settings.set('directories', 'trash', os.path.normpath(trash_dir) if trash_dir else "")

        ui_settings = self.settings.get('ui') or {}
        ui_settings['fullscreen'] = self.chk_fullscreen.isChecked()
        ui_settings['theme'] = self.theme_combo.currentText()
        ui_settings['font_size'] = self.font_spin.value()
        self.settings.update_section('ui', ui_settings)

        self.settings.set('ai_tagger', 'enabled', self.chk_ai_enable.isChecked())
        self.settings.set('metadata', 'write_exif', self.chk_exif.isChecked())
        self.settings.set('metadata', 'write_sidecar', self.chk_sidecar.isChecked())

        adv_settings = self.settings.get('advanced') or {}
        adv_settings['worker_threads'] = self.worker_spin.value()
        self.settings.update_section('advanced', adv_settings)

        hotkeys: Dict[str, Dict[str, Any]] = {}
        for row in range(self.hotkey_table.rowCount()):
            key_item = self.hotkey_table.item(row, 0)
            if not key_item or not key_item.text().strip():
                continue
            key = key_item.text().strip().upper()

            action_combo = self.hotkey_table.cellWidget(row, 1)
            action = action_combo.currentText()

            folder_widget = self.hotkey_table.cellWidget(row, 2)
            folder_edit = folder_widget.layout().itemAt(0).widget()
            folder = folder_edit.text()

            if folder and not os.path.exists(folder):
                QMessageBox.warning(self, "Validation Error", f"Target folder for hotkey '{key}' does not exist: {folder}")
                return

            chk_widget = self.hotkey_table.cellWidget(row, 3)
            advance_chk = chk_widget.layout().itemAt(0).widget()
            auto_advance = advance_chk.isChecked()

            hotkeys[key] = {"action": action, "folder": os.path.normpath(folder) if folder else "", "auto_advance": auto_advance}

        self.settings.update_section('hotkeys', hotkeys)

        QMessageBox.information(self, "Success", "Settings saved successfully.")
        self.close()
