@echo off
REM Genera dist\TakeMyNotes.exe (standalone, sin consola). Usa .venv para no
REM contaminar el Python del sistema.
if not exist .venv\Scripts\python.exe (
  python -m venv .venv || goto :err
)
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller==6.21.0 || goto :err
.venv\Scripts\python -m PyInstaller --onefile --noconsole --name TakeMyNotes ^
  --add-data "ui;ui" --collect-all webview takemynotes.py || goto :err
echo.
echo Listo: dist\TakeMyNotes.exe
echo (La API key se pone dentro de la app, en Configuracion.)
pause
exit /b 0

:err
echo.
echo FALLO el build. Revisa el error de arriba.
pause
exit /b 1
