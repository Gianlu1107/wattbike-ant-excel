; Wattbike Logger — installer Windows (Inno Setup)
; Compilare con: iscc scripts\windows\WattbikeLogger.iss
; Variabili da CI: MyAppVersion, SourceExe, OutputDir

#ifndef MyAppVersion
  #define MyAppVersion "1.3.4"
#endif
#ifndef SourceExe
  #define SourceExe "..\..\dist\WattbikeLogger-windows-x64.exe"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\out"
#endif

[Setup]
AppId={{A7C3E8F1-9B2D-4E6A-8C1F-2D5E7A9B0C3D}
AppName=Wattbike Logger
AppVersion={#MyAppVersion}
AppVerName=Wattbike Logger {#MyAppVersion}
AppPublisher=Gianluca
AppPublisherURL=https://github.com/Gianlu1107/wattbike-ant-excel
AppSupportURL=https://github.com/Gianlu1107/wattbike-ant-excel/issues
DefaultDirName={localappdata}\WattbikeLogger
DefaultGroupName=Wattbike Logger
DisableProgramGroupPage=no
AllowNoIcons=yes
LicenseFile=..\..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=WattbikeLogger-windows-x64-Setup
SetupIconFile=
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Wattbike Logger
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany=Gianluca
VersionInfoDescription=Wattbike ANT+ Logger Setup
VersionInfoProductName=Wattbike Logger
DisableWelcomePage=no
WizardSizePercent=120
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "WattbikeLogger.exe"; Flags: ignoreversion

[Icons]
Name: "{group}\Wattbike Logger"; Filename: "{app}\WattbikeLogger.exe"
Name: "{group}\{cm:UninstallProgram,Wattbike Logger}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Wattbike Logger"; Filename: "{app}\WattbikeLogger.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WattbikeLogger.exe"; Description: "Avvia Wattbike Logger"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
