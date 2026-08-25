import os
from typing import List
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar, QHBoxLayout, QFrame
from PyQt6.QtCore import Qt

class EnterpriseHUD(QWidget):
    """
    EnterpriseHUD is a dedicated overlay widget that displays critical sorting telemetry
    without visual clutter. It features a modern dark glass aesthetic.
    """
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)

        # Ensure HUD doesn't block mouse events for panning/zooming on the underlying view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(20, 20, 20, 0.85);
                color: #ffffff;
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QLabel {
                background-color: transparent;
                border: none;
            }
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.1);
                border: none;
                border-radius: 2px;
                height: 4px;
            }
            QProgressBar::chunk {
                background-color: #55ff55;
                border-radius: 2px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # File Info: Filename.jpg • Image 12 of 450 (2.6%)
        self.lbl_file_info = QLabel("No File")
        self.lbl_file_info.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self.lbl_file_info)

        # Specs: 3840 × 2160 • 8.3 MP • 4.2 MB
        self.lbl_specs = QLabel("")
        self.lbl_specs.setStyleSheet("font-size: 11px; color: #cccccc;")
        layout.addWidget(self.lbl_specs)

        # Status
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("font-size: 12px; color: #55ff55;")
        layout.addWidget(self.lbl_status)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Tags container
        self.lbl_tags = QLabel("Tags: [None]")
        self.lbl_tags.setStyleSheet("font-size: 11px; color: #aaaaaa; font-style: italic;")
        self.lbl_tags.setWordWrap(True)
        layout.addWidget(self.lbl_tags)

        self.setFixedWidth(300)
        self.hide()

    def update_telemetry(self, filename: str, current_idx: int, total_images: int, width: int, height: int, filesize_bytes: int) -> None:
        """Updates the HUD with file info and specs."""
        # File info
        percent = (current_idx / total_images * 100) if total_images > 0 else 0
        self.lbl_file_info.setText(f"{filename} • Image {current_idx} of {total_images} ({percent:.1f}%)")

        # Specs
        mp = (width * height) / 1_000_000
        mb = filesize_bytes / (1024 * 1024)
        self.lbl_specs.setText(f"{width} × {height} • {mp:.1f} MP • {mb:.1f} MB")

    def set_status(self, status: str, show_progress: bool = False) -> None:
        """Updates the status text and progress bar visibility."""
        self.lbl_status.setText(status)
        self.progress_bar.setVisible(show_progress)

    def set_progress(self, percent: int) -> None:
        """Updates the progress bar percentage."""
        self.progress_bar.setValue(percent)

    def set_tags(self, tags: List[str]) -> None:
        """Updates the displayed AI tags."""
        if not tags:
            self.lbl_tags.setText("Tags: [None]")
        else:
            self.lbl_tags.setText(f"Tags: {', '.join(tags)}")
