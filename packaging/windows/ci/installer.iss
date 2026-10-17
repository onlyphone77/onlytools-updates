; Installateur Windows d'Only Tools (compilé par GitHub Actions avec Inno Setup)
#define AppVer GetEnv("APP_VERSION")
[Setup]
AppName=Only Tools
AppVersion={#AppVer}
AppPublisher=Only Phone
DefaultDirName={localappdata}\OnlyTools
DefaultGroupName=Only Tools
PrivilegesRequired=lowest
OutputDir=..\..\..\dist
OutputBaseFilename=OnlyTools-Setup
SetupIconFile=..\..\..\build\OnlyTools\onlyphone\assets\icon.ico
UninstallDisplayIcon={app}\onlyphone\assets\icon.ico
Compression=lzma2
SolidCompression=yes
DisableProgramGroupPage=yes
WizardStyle=modern

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Files]
Source: "..\..\..\build\OnlyTools\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\Only Tools"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m onlyphone.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\onlyphone\assets\icon.ico"
Name: "{group}\Only Tools"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m onlyphone.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\onlyphone\assets\icon.ico"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m onlyphone.desktop"; WorkingDir: "{app}"; Description: "Lancer Only Tools"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=Only Tools est installé.%n%nPour les iPhone : installez « Apple Devices » depuis le Microsoft Store.%nPour les Android : activez le débogage USB sur le téléphone.
