# Image Sorter Enterprise

High-Throughput Image Triage & AI Tagging Suite.

## Architectural Overview & Key Capabilities

Image Sorter Enterprise is designed for high-quantity image generators and professional photographers. The architecture focuses on extreme performance, non-destructive file operations, and a zero-latency user experience.

*   **GPU Acceleration**: Hardware-accelerated processing via `onnxruntime` for real-time AI auto-tagging.
*   **Atomic I/O**: Secure, non-destructive file operations with a robust undo stack (Ctrl+Z) for moving and copying.
*   **Zero-Latency Preloading**: Leverages background `QThread`/`QThreadPool` workers to preload adjacent images seamlessly.
*   **Enterprise HUD**: Clean, customizable on-screen overlay providing real-time data on file paths, tagging status, and system operations without cluttering the main view.
*   **Clipping Analysis**: Real-time exposure analysis to detect over-exposed and under-exposed pixels dynamically using NumPy for extreme speed.

## Keyboard Shortcuts Reference

| Action | Shortcut | Description |
| :--- | :--- | :--- |
| **Next Image (Skip)** | `Space` / `Right Arrow` | Skip current image and load the next. |
| **Previous Image** | `Left Arrow` / `Backspace` | Go back to the previously viewed image. |
| **Delete to Trash** | `Delete` | Send the current image to the designated Trash folder. |
| **Undo Last Action** | `Ctrl + Z` | Revert the last file move or copy operation. |
| **Settings Menu** | `S` | Open the configuration panel. |
| **Reload Directory** | `R` | Refresh the source folder to detect new images. |
| **Toggle Zen Mode** | `Z` | Hide all UI elements for an immersive, distraction-free view. |
| **Lock Pan/Zoom** | `L` | Retain zoom and pan coordinates when switching images. |
| **Clipping Warning** | `C` | Toggle red/blue overlay for over/under-exposed areas. |
| **Exit Application** | `Esc` | Safely close background threads and exit (or exit full screen / Zen Mode). |
| **Custom Hotkeys** | User Defined | Perform custom Move/Copy actions to user-defined directories. |

## Installation & Build Instructions

### Prerequisites
*   Python 3.10+
*   Virtual environment (recommended)

### Environment Setup

```bash
# 1. Clone the repository
git clone <repository_url>
cd Image_Sorter/imagesorter

# 2. Create and activate a virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Building the Executable

This project includes a fully automated deterministic build system using PyInstaller.

```bash
# Build the default folder bundle (recommended for debugging or portable use)
python build.py

# Build a standalone single-file executable
python build.py --standalone
```

*Note: The executable output will be generated inside `imagesorter/dist/`.*

## Configuration and Settings Schema Reference

The application uses `settings.json` for configuration. Settings are fully customizable through the UI, but can be manually edited.

```json
{
    "directories": {
        "source": "Path to the folder containing unsorted images.",
        "trash": "Path to the folder where deleted images are moved."
    },
    "hotkeys": {
        "1": {
            "action": "move",
            "folder": "C:/Path/To/Folder",
            "auto_advance": true
        }
    },
    "ui": {
        "fullscreen": false,
        "show_tags": true,
        "theme": "Dark",
        "font_size": 24,
        "tooltips_enabled": true
    },
    "metadata": {
        "write_exif": true,
        "write_sidecar": false
    },
    "ai_tagger": {
        "enabled": false,
        "model_path": "models/model.onnx",
        "threshold": 0.5
    },
    "advanced": {
        "hardware_acceleration": true
    }
}
```

*   **`hotkeys`**: Map a key string to a dictionary containing `action` ("move" or "copy"), target `folder`, and whether to `auto_advance` to the next image after the action.
*   **`ui`**: Configures layout choices, themes ("Dark", "Light", "High Contrast"), and visual accessibility.
*   **`metadata`**: Non-destructive controls deciding how custom AI tags are saved (embedded EXIF via `piexif` or `.txt` sidecars).
*   **`ai_tagger`**: Enables auto-tagging utilizing ONNX Runtime. Point `model_path` to your trained `.onnx` model inside the `models/` directory.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
