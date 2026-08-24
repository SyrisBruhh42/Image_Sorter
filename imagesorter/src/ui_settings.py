import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QTabWidget, QFormLayout, QCheckBox,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QProgressDialog
)
from PyQt6.QtCore import Qt
from src.ai_tagger import ModelDownloader
from src.hardware_scan import scan_hardware

class SettingsWindow(QWidget):
    def __init__(self, settings_manager):
        super().__init__()
        self.settings = settings_manager
        self.setWindowTitle("Image Sorter Settings")
        self.resize(800, 600)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()

        # General Tab
        self.tab_general = QWidget()
        self.init_general_tab()
        self.tabs.addTab(self.tab_general, "General")

        # Hotkeys Tab
        self.tab_hotkeys = QWidget()
        self.init_hotkeys_tab()
        self.tabs.addTab(self.tab_hotkeys, "Hotkeys")

        # AI Tab
        self.tab_ai = QWidget()
        self.init_ai_tab()
        self.tabs.addTab(self.tab_ai, "AI & Metadata")

        # Advanced/Hardware Tab
        self.tab_advanced = QWidget()
        self.init_advanced_tab()
        self.tabs.addTab(self.tab_advanced, "Advanced")

        layout.addWidget(self.tabs)

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Save Settings")
        self.btn_save.clicked.connect(self.save_settings)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)

        layout.addLayout(btn_layout)

    def init_general_tab(self):
        layout = QFormLayout(self.tab_general)

        # Source Directory
        src_layout = QHBoxLayout()
        self.src_edit = QLineEdit(self.settings.get('directories', 'source') or "")
        self.src_btn = QPushButton("Browse...")
        self.src_btn.clicked.connect(lambda: self.browse_folder(self.src_edit))
        src_layout.addWidget(self.src_edit)
        src_layout.addWidget(self.src_btn)
        layout.addRow("Source Directory:", src_layout)

        # Trash Directory
        trash_layout = QHBoxLayout()
        self.trash_edit = QLineEdit(self.settings.get('directories', 'trash') or "")
        self.trash_btn = QPushButton("Browse...")
        self.trash_btn.clicked.connect(lambda: self.browse_folder(self.trash_edit))
        trash_layout.addWidget(self.trash_edit)
        trash_layout.addWidget(self.trash_btn)
        layout.addRow("Trash Directory:", trash_layout)

        # UI Options
        self.chk_fullscreen = QCheckBox("Start in Fullscreen Mode")
        self.chk_fullscreen.setChecked(self.settings.get('ui', 'fullscreen'))
        layout.addRow("UI Mode:", self.chk_fullscreen)

    def init_hotkeys_tab(self):
        layout = QVBoxLayout(self.tab_hotkeys)

        self.hotkey_table = QTableWidget(0, 4)
        self.hotkey_table.setHorizontalHeaderLabels(["Hotkey", "Action (move/copy)", "Target Folder", "Auto-Advance"])
        self.hotkey_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.hotkey_table)

        btn_layout = QHBoxLayout()
        self.btn_add_hotkey = QPushButton("Add Hotkey")
        self.btn_add_hotkey.clicked.connect(self.add_hotkey_row)
        self.btn_del_hotkey = QPushButton("Remove Selected")
        self.btn_del_hotkey.clicked.connect(self.remove_hotkey_row)
        btn_layout.addWidget(self.btn_add_hotkey)
        btn_layout.addWidget(self.btn_del_hotkey)
        layout.addLayout(btn_layout)

        self.load_hotkeys_to_table()

    def init_ai_tab(self):
        layout = QFormLayout(self.tab_ai)

        ai_layout = QHBoxLayout()
        self.chk_ai_enable = QCheckBox("Enable AI Auto-Tagging")
        self.chk_ai_enable.setChecked(self.settings.get('ai_tagger', 'enabled'))

        self.btn_download_model = QPushButton("Download Model")
        self.btn_download_model.clicked.connect(self.download_ai_model)

        # Check if model exists
        model_exists = os.path.exists("models/mobilenetv2.onnx")
        self.chk_ai_enable.setEnabled(model_exists)

        if model_exists:
            self.btn_download_model.setText("Model Downloaded")
            self.btn_download_model.setEnabled(False)

        ai_layout.addWidget(self.chk_ai_enable)
        ai_layout.addWidget(self.btn_download_model)

        layout.addRow("AI:", ai_layout)

        self.chk_exif = QCheckBox("Write tags to EXIF data")
        self.chk_exif.setChecked(self.settings.get('metadata', 'write_exif'))
        layout.addRow("Metadata:", self.chk_exif)

        self.chk_sidecar = QCheckBox("Write tags to sidecar .txt file")
        self.chk_sidecar.setChecked(self.settings.get('metadata', 'write_sidecar'))
        layout.addRow("", self.chk_sidecar)

    def init_advanced_tab(self):
        layout = QFormLayout(self.tab_advanced)

        self.btn_scan = QPushButton("Run Hardware Optimization Scan")
        self.btn_scan.clicked.connect(self.run_hardware_scan)
        layout.addRow("", self.btn_scan)

        self.lbl_scan_result = QLabel("No scan run yet.")
        self.lbl_scan_result.setWordWrap(True)
        layout.addRow("", self.lbl_scan_result)

    def run_hardware_scan(self):
        hw = scan_hardware()
        report = (
            f"<b>CPU Cores:</b> {hw['cpu_cores']}<br>"
            f"<b>RAM:</b> {hw['memory_total_gb']} GB<br>"
            f"<b>ONNX Providers:</b> {', '.join(hw['onnx_providers'])}<br><br>"
            f"<b>Recommendations:</b><br>"
            f"AI Provider: {hw['suggestions']['ai_provider']}<br>"
            f"Worker Threads: {hw['suggestions']['queue_threads']}"
        )
        self.lbl_scan_result.setText(report)
        QMessageBox.information(self, "Hardware Scan", "Hardware scanned successfully. Recommendations updated.")

    def browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Directory")
        if folder:
            line_edit.setText(folder)

    def add_hotkey_row(self, key="", action="move", folder="", auto_advance=True):
        row = self.hotkey_table.rowCount()
        self.hotkey_table.insertRow(row)

        self.hotkey_table.setItem(row, 0, QTableWidgetItem(key))

        action_combo = QComboBox()
        action_combo.addItems(["move", "copy"])
        action_combo.setCurrentText(action)
        self.hotkey_table.setCellWidget(row, 1, action_combo)

        folder_widget = QWidget()
        folder_layout = QHBoxLayout(folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)

        folder_edit = QLineEdit(folder)
        folder_btn = QPushButton("...")
        folder_btn.setFixedWidth(30)
        folder_btn.clicked.connect(lambda: self.browse_folder(folder_edit))

        folder_layout.addWidget(folder_edit)
        folder_layout.addWidget(folder_btn)

        self.hotkey_table.setCellWidget(row, 2, folder_widget)

        advance_chk = QCheckBox()
        advance_chk.setChecked(auto_advance)
        # Center the checkbox
        chk_widget = QWidget()
        chk_layout = QHBoxLayout(chk_widget)
        chk_layout.addWidget(advance_chk)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        self.hotkey_table.setCellWidget(row, 3, chk_widget)

    def remove_hotkey_row(self):
        curr = self.hotkey_table.currentRow()
        if curr >= 0:
            self.hotkey_table.removeRow(curr)

    def load_hotkeys_to_table(self):
        hotkeys = self.settings.get('hotkeys')
        if not hotkeys:
            return

        for key, config in hotkeys.items():
            self.add_hotkey_row(key, config.get("action", "move"), config.get("folder", ""), config.get("auto_advance", True))

    def download_ai_model(self):
        reply = QMessageBox.question(self, 'Download Model', 'This will download approx 15MB. Continue?',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.progress = QProgressDialog("Downloading model...", "Cancel", 0, 100, self)
            self.progress.setWindowModality(Qt.WindowModality.WindowModal)

            self.downloader = ModelDownloader("models")
            self.downloader.progress.connect(self.progress.setValue)
            self.downloader.finished.connect(self.on_download_finished)
            self.downloader.start()

    def on_download_finished(self, success, msg):
        self.progress.close()
        if success:
            QMessageBox.information(self, "Success", "Model downloaded successfully! You can now enable AI tagging.")
            self.btn_download_model.setText("Model Downloaded")
            self.btn_download_model.setEnabled(False)
            self.chk_ai_enable.setEnabled(True)
        else:
            QMessageBox.critical(self, "Error", f"Failed to download model: {msg}")

    def save_settings(self):
        # General
        self.settings.set('directories', 'source', self.src_edit.text())
        self.settings.set('directories', 'trash', self.trash_edit.text())
        self.settings.set('ui', 'fullscreen', self.chk_fullscreen.isChecked())

        # AI
        self.settings.set('ai_tagger', 'enabled', self.chk_ai_enable.isChecked())
        self.settings.set('metadata', 'write_exif', self.chk_exif.isChecked())
        self.settings.set('metadata', 'write_sidecar', self.chk_sidecar.isChecked())

        # Hotkeys
        hotkeys = {}
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

            chk_widget = self.hotkey_table.cellWidget(row, 3)
            advance_chk = chk_widget.layout().itemAt(0).widget()
            auto_advance = advance_chk.isChecked()

            hotkeys[key] = {"action": action, "folder": folder, "auto_advance": auto_advance}

        self.settings.update_section('hotkeys', hotkeys)

        QMessageBox.information(self, "Success", "Settings saved successfully.")
        self.close()
