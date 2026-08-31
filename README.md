# Image Sorter Enterprise

High-Throughput Image Triage & AI Tagging Suite built with Python and PyQt6.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

---

## 🚀 Overview

**Image Sorter Enterprise** is a zero-latency, cross-platform desktop application designed for high-quantity image generators, professional photographers, and digital archivists. Built on PyQt6 and ONNX Runtime, it delivers non-destructive file operations, multi-threaded image preloading, exposure clipping diagnostic tools, and AI-powered auto-tagging.

---

## ✨ Feature Matrix

| Feature | Description |
| :--- | :--- |
| **Zero-Latency Rendering** | Sub-millisecond image navigation backed by asynchronous `ImageLoader` background preloading queues. |
| **Transactional File Operations** | Bounded `QThreadPool` for moves, copies, and deletions with full `UndoToken` bidirectional rollback support (`Ctrl+Z`). |
| **AI Auto-Tagging Engine** | Decoupled ONNX Runtime engine supporting MobileNetV2 with automatic hardware execution provider prioritization (TensorRT > CUDA > ROCm > OpenVINO > DirectML > CoreML > CPU). |
| **EXIF Metadata Synergy** | Non-destructive, atomic EXIF tag writing (`piexif`) with safe temp-file swapping. |
| **Diagnostic Tools** | Real-time exposure clipping inspector highlights blown-out highlights (`>250`) and crushed shadows (`<5`). |
| **Cross-Platform Path Resolution** | Strict compliance with XDG Base Directory specs on POSIX/Linux, `%APPDATA%` on Windows, and `Application Support` on macOS, with optional `portable.flag` override. |

---

## 🏗️ Architecture Diagram

```
+-----------------------------------------------------------------------------------+
|                                 USER INTERFACE                                    |
|   MainViewer (QMainWindow) <=========================> SettingsWindow (QDialog)   |
+-----------------------------------------------------------------------------------+
       |                                      |                                |
       v                                      v                                v
+----------------------+           +--------------------+           +--------------------+
|     ImageLoader      |           |    QueueWorker     |           |   SettingsManager  |
|  (Background QThread)|           | (Bounded QThread)  |           | (Thread-Safe RLock)|
+----------------------+           +--------------------+           +--------------------+
       |                                      |                                |
       v                                      v                                v
+----------------------+           +--------------------+           +--------------------+
|  Preloaded Image     |           | Non-Destructive    |           | XDG / AppData /    |
|  Frame Cache         |           | File Ops & EXIF    |           | Portable Path Res  |
+----------------------+           +--------------------+           +--------------------+
                                              |
                                              v
                                   +--------------------+
                                   |  AITagger (ONNX)   |
                                   | Provider Auto-Scan |
                                   +--------------------+
```

---

## 📦 Installation

### Prerequisites
* Python `>= 3.9`

### Editable Installation (Development Mode)
```bash
git clone https://github.com/SyrisBruhh42/Image_Sorter.git
cd Image_Sorter
pip install -e .
```

To install with development and testing dependencies:
```bash
pip install -e ".[dev]"
```

---

## ⚡ Quick Start & Execution

Launch the application using either the CLI command or module execution:

```bash
# Direct CLI entry point
imagesorter

# Python module invocation (run headless offscreen or standard)
QT_QPA_PLATFORM=offscreen python3 -m imagesorter.main --help
```

---

## ⌨️ Keyboard Shortcuts Reference

Image Sorter features a comprehensive, keyboard-driven UI designed for high-speed triage:

| Action | Shortcut | Description |
| :--- | :--- | :--- |
| **Next Image** | `Right` / `D` / `Space` | Advance to the next image in source directory. |
| **Previous Image** | `Left` / `A` | Move to the previous image. |
| **Move to Trash** | `Delete` / `X` | Move active image to configured staging trash / system bin. |
| **Exposure Clipping** | `C` | Toggle over/under-exposure overlay mask. |
| **Lock Zoom** | `L` | Lock current zoom level and pan position across image transitions. |
| **Zen Mode** | `Z` | Toggle full-screen distraction-free viewing mode. |
| **Undo Action** | `Ctrl + Z` | Rollback the last file operation (move, delete, tag). |
| **Open Settings** | `S` | Open Settings and AI Auto-Tagging configuration dialog. |
| **Zoom In / Out** | `+` / `-` or `Ctrl + Mouse Wheel` | Adjust viewport zoom. |
| **Reset Zoom** | `0` or `Ctrl + 0` | Reset image view to fit container. |

---

## 🤖 AI Auto-Tagging Setup (ONNX Runtime)

1. Open the **Settings Window** (`S`).
2. Enable **AI Auto-Tagging Engine**.
3. Select or download the desired ONNX tagging model (e.g., MobileNetV2 tagger). Model weights are optional; when downloaded, artifacts are validated via SHA-256 checksums before target placement. Model weights are saved directly to your OS application data directory (`~/.local/share/ImageSorter/models` on Linux).
4. Configure tag confidence thresholds (`0.0 - 1.0`).
5. Hardware execution acceleration is dynamically probed:
   * **NVIDIA GPU**: TensorRT / CUDA
   * **AMD GPU**: ROCm / MIGraphX
   * **Intel GPU/CPU**: OpenVINO
   * **Windows GPU**: DirectML
   * **Apple Silicon**: CoreML
   * **Fallback**: Multi-threaded CPU Execution Provider

---

## 🛠️ Standalone Executable & AppImage Build Guide

The project includes an automated `build.py` script powered by PyInstaller and Freedesktop standards.

### 1. PyInstaller Single-Directory Executable
```bash
python3 build.py
```
Outputs are stored in `dist/ImageSorter/`.

### 2. Testing and Coverage Verification
To run unit tests headlessly with coverage report:
```bash
QT_QPA_PLATFORM=offscreen pytest --cov=imagesorter --cov-report=term-missing tests/
```

### 2. Linux AppImage Generation
After running `python3 build.py` on Linux, execute the generated setup script:
```bash
./dist/build_appimage.sh
```
If `appimagetool` is installed on your PATH, it compiles `ImageSorter-x86_64.AppImage`.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. Copyright (c) 2026 SyrisBruhh42.
