"""Entry point: python -m files_win [folder]"""

import os
import sys


def main():
    if sys.platform == "win32":
        import ctypes
        try:  # own taskbar group/icon instead of python.exe's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FilesForWindows.Files")
        except (AttributeError, OSError):
            pass
        try:  # attach to user's interactive desktop if launched from background sandbox
            h_desk = ctypes.windll.user32.OpenDesktopW("Default", 0, False, 0x10000000)
            if h_desk:
                ctypes.windll.user32.SetThreadDesktop(h_desk)
        except (AttributeError, OSError):
            pass

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("Files")
    qapp.setOrganizationName("FilesForWindows")
    qapp.setStyle("Fusion")

    from . import app, icons, model, operations, theme
    qapp.setWindowIcon(icons.app_icon())
    qapp.setFont(theme.ui_font())
    model.init_services()
    operations.init()
    state = app.AppState(qapp)
    app.state = state
    state.apply_theme()

    def on_error(title, message):
        from .widgets import alert
        parent = qapp.activeWindow() or (state.windows[-1] if state.windows else None)
        alert(parent, title, message)

    operations.operations.error.connect(on_error)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    location = os.path.abspath(args[0]) if args and os.path.isdir(args[0]) else None
    state.new_window(location)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
