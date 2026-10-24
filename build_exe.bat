@echo off
setlocal
cd /d "%~dp0"

echo [1/2] Checking application icon...
if not exist "app.ico" (
    python -c "from PySide6.QtWidgets import QApplication; import sys; from files_win import icons; app = QApplication(sys.argv); pm = icons.app_icon().pixmap(256, 256); pm.save('icon.png'); from PIL import Image; img = Image.open('icon.png'); img.save('app.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if exist "icon.png" del /f /q "icon.png"
)

echo [2/2] Building Files.exe with PyInstaller...
python -m PyInstaller --noconfirm --clean --windowed --onefile --name "Files" --icon "app.ico" --paths "." Files.pyw

if %ERRORLEVEL% EQU 0 (
    copy /y "dist\Files.exe" "Files.exe" >nul
    if errorlevel 1 (
        echo.
        echo Built dist\Files.exe, but couldn't replace Files.exe - close Files and run this again.
        goto :end
    )
    echo.
    echo ========================================================
    echo Build successful! Executable is ready at:
    echo %~dp0Files.exe
    echo ========================================================
) else (
    echo.
    echo Build failed with error code %ERRORLEVEL%.
)

:end
endlocal
