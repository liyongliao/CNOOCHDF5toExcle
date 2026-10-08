; Standalone desktop bundle, installed for the current user only.
#ifndef Architecture
  #error Architecture must be x64 or x86
#endif
#ifndef SourceDir
  #error SourceDir must point to the complete PyInstaller desktop folder
#endif
#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif

[Setup]
AppId=H5ToExcelConverter-{#Architecture}{code:GetInstallSuffix}
AppName=井下压力转换工具
AppVersion={#AppVersion}
AppVerName=井下压力转换工具 ({#Architecture}) {#AppVersion}
AppPublisher=井下压力数据工具
DefaultDirName={localappdata}\Programs\H5ToExcelConverter-{#Architecture}
DefaultGroupName={code:GetShortcutName}
PrivilegesRequired=lowest
UsePreviousAppDir=no
UsePreviousGroup=no
UsePreviousLanguage=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
WizardStyle=modern dynamic
MinVersion=10.0
#if Architecture == "x64"
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#elif Architecture == "x86"
ArchitecturesAllowed=x86compatible
#else
  #error Unsupported Architecture
#endif
OutputBaseFilename=H5ToExcelConverter-Setup-{#Architecture}
Compression=lzma2/max
SolidCompression=yes
DiskSpanning=no
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\H5ToExcelConverter.exe
UninstallDisplayName=井下压力转换工具 ({#Architecture})
VersionInfoVersion={#AppVersion}
VersionInfoDescription=井下压力转换工具安装程序 ({#Architecture})

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{code:GetShortcutName}\{code:GetShortcutName}"; Filename: "{app}\H5ToExcelConverter.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\{code:GetShortcutName}"; Filename: "{app}\H5ToExcelConverter.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\H5ToExcelConverter.exe"; Description: "打开井下压力转换工具"; Flags: nowait postinstall skipifsilent

[Code]
function GetInstallSuffix(Param: String): String;
var
  TestId: String;
begin
  TestId := ExpandConstant('{param:CITEST|}');
  if TestId = '' then
    Result := ''
  else
    Result := '-ci-' + TestId;
end;

function GetShortcutName(Param: String): String;
begin
  if ExpandConstant('{param:CITEST|}') = '' then
    Result := '井下压力转换工具 ({#Architecture})'
  else
    Result := 'H5ToExcelConverter-{#Architecture}' + GetInstallSuffix('');
end;

function InitializeSetup: Boolean;
var
  TestId: String;
  Index: Integer;
begin
  Result := True;
  TestId := ExpandConstant('{param:CITEST|}');
  if TestId = '' then
    Exit;
  { CI uses a unique uninstall key and shortcut names, preserving existing installations. }
  if Length(TestId) <> 36 then
  begin
    Result := False;
    Exit;
  end;
  for Index := 1 to Length(TestId) do
    if Pos(TestId[Index], '0123456789abcdef-') = 0 then
    begin
      Result := False;
      Exit;
    end;
end;
