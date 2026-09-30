@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creando entorno virtual .venv...
    python -m venv .venv
)
echo Instalando dependencias...
.venv\Scripts\python -m pip install -r requirements.txt
echo Lanzando TakeMyNotes...
start "" .venv\Scripts\pythonw.exe takemynotes.py
exit
