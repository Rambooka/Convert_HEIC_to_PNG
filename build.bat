@echo off
REM Builds HEIC_to_PNG.exe into the dist folder.
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat || goto :err
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :err

if not exist app.ico python make_icon.py || goto :err

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name HEIC_to_PNG ^
  --icon app.ico ^
  --add-data "app.ico;." ^
  --add-data "app.png;." ^
  --collect-all tkinterdnd2 ^
  --collect-all pillow_heif ^
  heic_to_png.py || goto :err

echo.
echo Built: %~dp0dist\HEIC_to_PNG.exe
pause
exit /b 0

:err
echo Build failed.
pause
exit /b 1
