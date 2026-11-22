@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python make_icon.py || exit /b 1
python test_parse.py || exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --uac-admin --icon icon.ico --add-data "icon.png;." --name ZapretOneClick --collect-submodules proxy --hidden-import h2 --hidden-import httpx --hidden-import PIL._tkinter_finder --collect-data certifi app.py || exit /b 1
echo.
echo Готово: dist\ZapretOneClick.exe
