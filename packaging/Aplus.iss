#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif
#ifndef Payload
  #error Payload must be supplied by the build script
#endif
#ifndef Output
  #error Output must be supplied by the build script
#endif
[Setup]
AppId={{7B56A3BD-706C-4F60-AC18-0453099FA226}
AppName=Aplus
AppVersion={#AppVersion}
AppPublisher=Aplus Project
DefaultDirName={localappdata}\Programs\Aplus
DefaultGroupName=Aplus
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir={#Output}
OutputBaseFilename=AplusSetup-{#AppVersion}-x64
SetupIconFile=..\icon_aplus.ico
UninstallDisplayIcon={app}\Aplus.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
AppMutex=Local\AplusDesktopWorkbench,Local\AplusInstalledLauncher
SetupMutex=Local\AplusInstalledSetup
SetupLogging=yes
[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce
[Files]
Source: "{#Payload}\AplusApp\*"; DestDir: "{app}\app\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\Aplus.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\aplus-install.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
[Icons]
Name: "{autodesktop}\Aplus"; Filename: "{app}\Aplus.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\Aplus"; Filename: "{app}\Aplus.exe"; WorkingDir: "{app}"
[Run]
Filename: "{app}\Aplus.exe"; Description: "打开 Aplus，接续已有数据或开始使用"; Flags: nowait postinstall skipifsilent
[Code]
function MoveFileEx(ExistingFile, NewFile: String; Flags: Cardinal): Boolean;
  external 'MoveFileExW@kernel32.dll stdcall';

function WebViewPresent: Boolean;
var Version: String; Key: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := False;
  if RegQueryStringValue(HKLM32,Key,'pv',Version) then
    if (Version <> '') and (Version <> '0.0.0.0') then begin Result := True; Exit; end;
  if RegQueryStringValue(HKLM64,Key,'pv',Version) then
    if (Version <> '') and (Version <> '0.0.0.0') then begin Result := True; Exit; end;
  if RegQueryStringValue(HKCU,Key,'pv',Version) then
    if (Version <> '') and (Version <> '0.0.0.0') then Result := True;
end;

function SafeTarget(Path: String): Boolean;
begin
  Result := not FileExists(AddBackslash(Path)+'workbench.sqlite3') and
            not FileExists(AddBackslash(Path)+'runtime\workbench.sqlite3');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var FindRec: TFindRec;
begin
  Result := True;
  if WizardSilent then Exit;
  if CurPageID = wpSelectDir then begin
    if not SafeTarget(WizardDirValue) then begin
      MsgBox('这里存有 Aplus 数据。请选择独立的程序安装目录；原数据会在首次启动时安全接续。',mbError,MB_OK);
      Result := False;
    end else if DirExists(WizardDirValue) and not FileExists(AddBackslash(WizardDirValue)+'aplus-install.json') then begin
      if FindFirst(AddBackslash(WizardDirValue)+'*',FindRec) then begin
        try
          repeat
            if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
              MsgBox('所选目录已有其他文件。为保护旧程序和数据，请使用默认目录或新的空目录。',mbError,MB_OK);
              Result := False;
              Break;
            end;
          until not FindNext(FindRec);
        finally FindClose(FindRec); end;
      end;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var FindRec: TFindRec;
begin
  Result := '';
  if not SafeTarget(ExpandConstant('{app}')) then
    Result := '安装目标中存在用户数据，已停止安装。请选择独立的程序目录。';
  if (Result = '') and DirExists(ExpandConstant('{app}')) and
      not FileExists(ExpandConstant('{app}\aplus-install.json')) then begin
    if FindFirst(ExpandConstant('{app}\*'),FindRec) then begin
      try
        repeat
          if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
            Result := '安装目标不是空目录或已识别安装，请选择独立的新目录。';
            Break;
          end;
        until not FindNext(FindRec);
      finally FindClose(FindRec); end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer; OldPointer: AnsiString; Report, Pointer: String;
begin
  if CurStep = ssPostInstall then begin
    if not WebViewPresent then begin
      if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
         '/silent /install','',SW_HIDE,ewWaitUntilTerminated,Code) then
        RaiseException('图表运行组件无法安装。请保持联网后重试；原数据不受影响。');
      if not WebViewPresent then
        RaiseException('图表运行组件尚未就绪。请保持联网后重试；原数据不受影响。');
    end;
    Report := ExpandConstant('{tmp}\aplus-self-test.json');
    if not Exec(ExpandConstant('{app}\app\{#AppVersion}\AplusApp.exe'),
      '--self-test "'+Report+'"', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('新版预检无法启动，原数据与原版本指针保持不变。');
    if (Code <> 0) or not FileExists(Report) then
      RaiseException('新版完整性预检未通过，原数据与原版本指针保持不变。');
    Pointer := ExpandConstant('{app}\current.txt');
    if LoadStringFromFile(Pointer,OldPointer) then
      if Trim(OldPointer) <> '{#AppVersion}' then
        SaveStringToFile(ExpandConstant('{app}\previous.txt'),OldPointer,False);
    if not SaveStringToFile(Pointer+'.new','{#AppVersion}',False) then
      RaiseException('无法准备版本指针，原版本继续可用。');
    if not MoveFileEx(Pointer+'.new',Pointer,1 or 8) then
      RaiseException('无法切换版本指针，原版本继续可用。');
  end;
end;
