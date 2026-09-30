; Instalador de TakeMyNotes (Inno Setup 6). Lo compila build.bat después del .exe:
;   ISCC installer.iss   ->   dist\TakeMyNotes-Setup-<versión>.exe
;
; Instala POR USUARIO en %LOCALAPPDATA%\Programs\TakeMyNotes, sin pedir admin. No es por
; comodidad: la app guarda notas\ y settings.json junto al .exe (BASE en takemynotes.py), y en
; Program Files no podría escribirlos.

#define AppName "TakeMyNotes"
#define AppVersion "0.8.0"
#define AppExe "TakeMyNotes.exe"

[Setup]
; El AppId identifica la app entre versiones: no cambiarlo nunca, o se instala otra al lado.
AppId={{43E6774D-9FD2-4405-9C02-A72AF7587592}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
; la app se cierra sola con --quit (ver [Code]); el Restart Manager no sabe preguntarle al widget
; si está grabando
CloseApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso en el escritorio"; Flags: unchecked
Name: "startup"; Description: "Abrir el widget al iniciar Windows"; Flags: unchecked

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; los datos del usuario: se crean acá y el desinstalador NO los borra
Name: "{app}\notas"; Flags: uninsneveruninstall

[Icons]
; La carpeta del menú Inicio. Cada acceso es el mismo .exe con otro argumento (ver el final de
; takemynotes.py); si esa parte ya está abierta, la trae al frente en vez de abrir otra.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  Comment: "Mostrar el widget de grabación"
Name: "{group}\Sesiones"; Filename: "{app}\{#AppExe}"; Parameters: "--window"; \
  Comment: "Abrir la ventana con las reuniones grabadas"
Name: "{group}\Configuración"; Filename: "{app}\{#AppExe}"; Parameters: "--settings"; \
  Comment: "API keys, tema, prueba de audio"
Name: "{group}\Cerrar {#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--quit"; \
  Comment: "Cierra el widget y la ventana (pregunta si estás grabando)"
Name: "{group}\Carpeta de notas"; Filename: "{app}\notas"; \
  Comment: "Transcripciones, audio y capturas en disco"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; cerrarla antes de borrar el .exe: si no, queda en uso y el desinstalador no lo puede quitar
Filename: "{app}\{#AppExe}"; Parameters: "--quit"; RunOnceId: "CerrarApp"; \
  Flags: waituntilterminated skipifdoesntexist

[Code]
// Al actualizar: cerrar la versión que está corriendo antes de reemplazar el .exe.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Exe: String;
begin
  Result := '';
  Exe := ExpandConstant('{app}\{#AppExe}');
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;
