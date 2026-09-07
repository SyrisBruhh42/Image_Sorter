# KDE Plasma Platform Acceptance Matrix (Ubuntu 24.04 LTS)

## Overview & Target Environment
- **Operating System:** Ubuntu 24.04 LTS (Noble Numbat)
- **Desktop Environment:** KDE Plasma 6.x / KDE Plasma 5.27 LTS
- **Display Server:** KWin (Wayland) & KWin (X11)
- **Application:** ImageSorter Enterprise v1.0.0
- **Purpose:** Systematic validation of user interface rendering, system integration, file operations, multi-monitor behavior, scaling, and interruption resilience under KDE Plasma.

---

## Pre-Requisites & Test Fixture Preparation

Before executing the acceptance matrix, create an isolated directory with temporary test image fixtures using the following commands:

```bash
# Create isolated test fixture directory
mkdir -p /tmp/imagesorter_accept_fixtures/{sample_folder,trash_folder,external_mnt}

# Generate temporary image fixtures (RGB/RGBA/JPEG)
python3 -c "
from PIL import Image, ImageDraw
import os

base = '/tmp/imagesorter_accept_fixtures/sample_folder'
os.makedirs(base, exist_ok=True)

# Generate 5 sample images with distinct colors and text
colors = ['red', 'green', 'blue', 'yellow', 'magenta']
for i, color in enumerate(colors):
    img = Image.new('RGB', (1920, 1080), color=color)
    draw = ImageDraw.Draw(img)
    draw.text((100, 100), f'Fixture Image {i+1} ({color})', fill='white')
    img.save(os.path.join(base, f'fixture_{i+1:02d}_{color}.jpg'), 'JPEG')

print('Test fixtures created successfully under /tmp/imagesorter_accept_fixtures')
"
```

Also run the diagnostic script to attach environment information to the test run report:
```bash
python3 scripts/diagnose_linux_p07.py --json > /tmp/imagesorter_accept_fixtures/diag_report.json
```

---

## Platform Acceptance Matrix

| ID | Category | Specific Condition / Test Description | Test Execution Procedure | Expected Outcome | Status | Supporting Evidence & Notes |
|:---|:---|:---|:---|:---|:---:|:---|
| **KDE-01** | Display Session | **Wayland Native Session** | Launch ImageSorter under KDE Plasma Wayland session (`WAYLAND_DISPLAY` set). `QT_QPA_PLATFORM=wayland;xcb`. | App initializes without platform plugin errors or visual artifacting. Crisp window borders. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Terminal output, KWin Wayland debug log, diagnostic report JSON. |
| **KDE-02** | Display Session | **X11 / XCB Session** | Launch ImageSorter under KDE Plasma X11 session (`DISPLAY` set, `WAYLAND_DISPLAY` unset). | App initializes using `xcb` backend without crashes or missing font/icon rendering. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Terminal log from `x11` environment session. |
| **KDE-03** | Fractional Scaling | **100% DPI Scaling (Standard)** | Set Plasma Display Scaling to 100% (96 DPI). Open ImageSorter with sample fixtures. | Image previews render cleanly. Text and UI icons are un-distorted and sharp. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Screenshot of main viewer window at 100%. |
| **KDE-04** | Fractional Scaling | **150% Fractional Scaling** | Set Plasma Display Scaling to 150% (Wayland / X11 forced scaling). Open ImageSorter. | UI components scale proportionally without text truncation, layout overlap, or pixmap blurring. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Screenshot of main viewer and settings modal at 150%. |
| **KDE-05** | High-DPI Scaling | **200% DPI Scaling (4K HiDPI)** | Set Plasma Display Scaling to 200%. Open ImageSorter with high-resolution images. | High-DPI pixel ratio (`devicePixelRatio`) correctly applied to preview canvas and icons. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Screenshot at 200% DPI. |
| **KDE-06** | Multi-Monitor | **Multi-Monitor Drag & DPI Span** | Move ImageSorter window between Primary (e.g. 150% 4K) and Secondary (100% 1080p) monitors. | Window adapts to display screen changes smoothly without crashing or corrupting Qt canvas. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Multi-monitor display layout diagram / screenshot. |
| **KDE-07** | Window Manager | **Taskbar Icon & Grouping** | Pin ImageSorter to Plasma Panel / Task Manager (`Plasma Task Manager`). Launch multiple instances. | Application icon (`imagesorter.png`) displays properly on panel and groups correctly under single launcher. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Screenshot of Plasma taskbar showing pinned/grouped icon. |
| **KDE-08** | File Manager | **Dolphin "Open With" Integration** | In Dolphin File Manager, right-click an image file -> Open With -> ImageSorter. | ImageSorter launches and opens selected image immediately as active item in viewer. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | `imagesorter.desktop` file location verification and Dolphin screenshot. |
| **KDE-09** | System Integration| **Native KDE File Dialogs** | Open Settings or File Picker within ImageSorter. Check dialog style. | File chooser uses KDE Plasma native file dialogs (`KFileDialog` / `QFileDialog` native integration) matching system theme. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Screenshot of open file/folder dialog. |
| **KDE-10** | Shortcuts & HUD | **Keyboard Navigation & HUD** | Press `Space` (Next), `Backspace`/`Left` (Prev), `Delete` (Trash), `Ctrl+Z` (Undo) in viewer. | Shortcuts trigger navigation and operations instantly without losing focus to background panels. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Key press sequence log / event trace. |
| **KDE-11** | Plasma Themes | **Breeze Dark & Light Themes** | Toggle Plasma Global Theme between Breeze Light and Breeze Dark while ImageSorter is running. | UI colors adjust or maintain readable contrast across dialogs, tag lists, and main preview pane. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Side-by-side screenshots in Breeze Light and Breeze Dark. |
| **KDE-12** | File Operations | **Copy, Move, Trash & Undo** | Execute Move (`M`), Copy (`C`), Trash (`Delete`), and Undo (`Ctrl+Z`) on `/tmp/imagesorter_accept_fixtures/sample_folder/`. | Operations modify files transactionally; `Send2Trash` sends files to Plasma Trash; Undo restores exact original paths. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Directory file listing before and after operations. |
| **KDE-13** | Storage Paths | **Removable Storage / External Mounts** | Load image directory located on external USB drive or FUSE mount (e.g. `/media/$USER/EXT_DRIVE`). | Reads images, writes EXIF/sidecar metadata, and moves/copies files cleanly across filesystem boundaries. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Terminal session output showing mount path operations. |
| **KDE-14** | Recovery | **Interruption & Crash Recovery** | Simulate abnormal termination (`kill -9`) during background worker batch processing or corrupt `settings.json`. | ImageSorter detects corrupt settings on next launch, creates safety backup (`settings.json.corrupt.*`), and loads clean defaults without state corruption. | `[ ] PASS`<br>`[ ] FAIL`<br>`[X] NOT RUN` | Verification of backup file creation and startup log. |

---

## Execution Guidelines for QA / Joe

1. **Environment Setup:** Ensure testing machine is running clean Ubuntu 24.04 LTS with KDE Plasma (`plasma-desktop` / `kubuntu-desktop`).
2. **Fixture Generation:** Execute the fixture preparation script provided above before starting tests.
3. **Execution Recording:**
   - Mark each item `[X] PASS` or `[X] FAIL` as tests are performed.
   - For failed items, attach terminal output or screenshot in `Supporting Evidence & Notes`.
   - Run `python3 scripts/diagnose_linux_p07.py` and attach the diagnostic log alongside results.
