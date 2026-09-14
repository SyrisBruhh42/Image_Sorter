from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("ImageSorter")
MainViewer = None  # Injectable viewer factory; runtime import happens after profile parsing.


def configure_linux_platform() -> None:
    """Configures Linux display server fallback strategy if QT_QPA_PLATFORM is unset."""
    if not sys.platform.startswith("linux"):
        return

    if "QT_QPA_PLATFORM" in os.environ:
        logger.info(
            f"Preserving existing QT_QPA_PLATFORM='{os.environ['QT_QPA_PLATFORM']}'"
        )
        return

    wayland_display = os.environ.get("WAYLAND_DISPLAY", "").strip()
    x11_display = os.environ.get("DISPLAY", "").strip()

    if wayland_display and x11_display:
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
        logger.info("Set QT_QPA_PLATFORM='wayland;xcb' for Wayland/X11 display server synergy.")
    elif wayland_display:
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        logger.info("Set QT_QPA_PLATFORM='wayland' for Wayland display server.")
    elif x11_display:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        logger.info("Set QT_QPA_PLATFORM='xcb' for X11 display server.")
    else:
        logger.info("Neither WAYLAND_DISPLAY nor DISPLAY found. Leaving QT_QPA_PLATFORM unset for Qt default selection.")


def main(argv: list[str] | None = None) -> int:
    """Main application entry point.

    Returns:
        int: Exit status code (0 for success, 1 for failure).
    """
    global MainViewer
    args = list(sys.argv if argv is None else argv)
    if any(flag in args[1:] for flag in ("--profile-root", "--reader-job", "--mutation-service", "--component-job", "--native-fixture-job")):
        from .bootstrap import entry
        return entry(args)

    if any(arg in args[1:] for arg in ("-h", "--help")):
        print("Usage: imagesorter [options] [file_or_directory ...]\n\nOptions:\n  -h, --help  Show this help message and exit")
        return 0

    configure_linux_platform()
    if "--diagnostic-receipt" in args:
        index = args.index("--diagnostic-receipt")
        from .diagnostics import configure
        configure(args[index + 1])
        del args[index:index + 2]
    exit_after_ready = "--acceptance-exit-after-ready" in args
    if exit_after_ready:
        args.remove("--acceptance-exit-after-ready")
    scenario_name = None
    if "--diagnostic-scenario" in args:
        if not os.environ.get("IMAGESORTER_PROFILE_ROOT"):
            raise ValueError("Native diagnostic scenarios require --profile-root")
        index = args.index("--diagnostic-scenario")
        scenario_name = args[index + 1]
        del args[index:index + 2]
    from .launch_requests import parse_launch_paths
    from .paths import get_resource_dir
    from .settings_manager import SettingsManager
    if MainViewer is None:
        from .ui_main import MainViewer

    try:
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication(args)

        app.setApplicationName("Image Sorter")
        app.setOrganizationName("SyrisBruhh42")

        # Desktop ID wiring according to Freedesktop spec (imagesorter.desktop)
        app.setDesktopFileName("imagesorter")

        # Set Application Window Icon
        res_dir = get_resource_dir()
        icon_path = res_dir / "imagesorter.png"
        if not icon_path.exists():
            icon_path = res_dir / "imagesorter.ico"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))

        scenario = None
        if scenario_name:
            from .native_scenarios import prepare
            scenario = prepare(scenario_name)
        settings = SettingsManager()

        # Parse positional launch arguments (files and folders)
        initial_paths = parse_launch_paths(args[1:])
        if scenario:
            initial_paths = scenario["images"]

        if initial_paths:
            viewer = MainViewer(settings, initial_paths=initial_paths)
        else:
            viewer = MainViewer(settings)

        from .diagnostics import record
        record("launch_received", arguments=args[1:], normalized_paths=initial_paths,
               desktop_file_name=app.desktopFileName(), application_icon_present=not app.windowIcon().isNull())

        viewer._acceptance_exit_after_ready = exit_after_ready
        viewer.show()
        if scenario:
            from .native_scenarios import install
            install(viewer, scenario)
        import signal

        from PyQt6.QtCore import QTimer
        # Keep Python's signal bridge serviced by the Qt event loop.
        signal_timer = QTimer(app)
        signal_timer.timeout.connect(lambda: None)
        signal_timer.start(50)
        previous_term = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: viewer.close())

        try:
            exit_code = int(app.exec())
            return int(getattr(viewer, "_scenario_exit_code", exit_code))
        finally:
            signal.signal(signal.SIGTERM, previous_term)
    except Exception as e:
        logger.exception("Qt initialization failed", exc_info=e)
        sys.stderr.write(
            f"Error: Qt application initialization failed: {e}\n"
            "If running in a headless or test environment, try setting QT_QPA_PLATFORM=offscreen.\n"
            "If running in a desktop environment, check that DISPLAY or WAYLAND_DISPLAY is correctly set.\n"
        )
        return 1


if __name__ == "__main__":
    from .bootstrap import entry
    raise SystemExit(entry())
