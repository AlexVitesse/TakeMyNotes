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

REM Instalador con carpeta en el menu Inicio (installer.iss). Opcional: solo si esta Inno Setup 6
REM (https://jrsoftware.org/isdl.php, o: winget install JRSoftware.InnoSetup).
set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" ^
            "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do if exist %%P set "ISCC=%%~P"
if defined ISCC (
  "%ISCC%" /Q installer.iss || goto :err
  echo Instalador: dist\TakeMyNotes-Setup-*.exe
) else (
  echo Sin Inno Setup 6: no se genera el instalador, solo el .exe.
)
pause
exit /b 0

:err
echo.
echo FALLO el build. Revisa el error de arriba.
pause
exit /b 1
