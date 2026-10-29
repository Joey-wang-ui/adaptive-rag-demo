; Per-user Windows x64 installer. No Python, Node, Ollama or WeChat prerequisite.
[Setup]
AppId={{ABF7C513-F193-4A18-9BC6-1589CD454E29}
AppName=Jiahui Shipment Audit
AppVersion=1.0.0
DefaultDirName={localappdata}\Programs\JiahuiShipmentAudit
DefaultGroupName=Jiahui Shipment Audit
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=JiahuiShipmentAudit-Setup-Windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\JiahuiShipmentAudit.exe

[Files]
Source: "..\dist\JiahuiShipmentAudit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Icons]
Name: "{group}\Jiahui Shipment Audit"; Filename: "{app}\JiahuiShipmentAudit.exe"
Name: "{autodesktop}\Jiahui Shipment Audit"; Filename: "{app}\JiahuiShipmentAudit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JiahuiShipmentAudit.exe"; Description: "Launch Jiahui Shipment Audit"; Flags: nowait postinstall skipifsilent
