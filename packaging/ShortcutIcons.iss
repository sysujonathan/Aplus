{ Cosmetic repair only: no new links, no execution, no global icon-cache reset. }
{ Unicode COM declarations follow Inno's Examples/CodeAutomation2.iss.
  Keep unused vtable slots: removing them changes every following method. }
type
  IAplusShellLinkW = interface(IUnknown)
    '{000214F9-0000-0000-C000-000000000046}'
    procedure GetPath(pszFile: String; cchMaxPath, NullFindData: Integer; Flags: DWORD); safecall;
    procedure UnusedGetIDList;
    procedure UnusedSetIDList;
    procedure GetDescription(pszName: String; cchMaxName: Integer); safecall;
    procedure SetDescription(pszName: String); safecall;
    procedure GetWorkingDirectory(pszDir: String; cchMaxPath: Integer); safecall;
    procedure SetWorkingDirectory(pszDir: String); safecall;
    procedure GetArguments(pszArgs: String; cchMaxPath: Integer); safecall;
    procedure SetArguments(pszArgs: String); safecall;
    function GetHotkey: Word; safecall;
    procedure SetHotkey(wHotkey: Word); safecall;
    function GetShowCmd: Integer; safecall;
    procedure SetShowCmd(iShowCmd: Integer); safecall;
    procedure GetIconLocation(pszIconPath: String; cchIconPath: Integer; out IconIndex: Integer); safecall;
    procedure SetIconLocation(pszIconPath: String; IconIndex: Integer); safecall;
    procedure SetRelativePath(pszPathRel: String; Reserved: DWORD); safecall;
    procedure Resolve(Wnd: HWND; Flags: DWORD); safecall;
    procedure SetPath(pszFile: String); safecall;
  end;
  IAplusPersist = interface(IUnknown)
    '{0000010C-0000-0000-C000-000000000046}'
    procedure GetClassID(var ClassID: TGUID); safecall;
  end;
  IAplusPersistFile = interface(IAplusPersist)
    '{0000010B-0000-0000-C000-000000000046}'
    procedure IsDirty; safecall;
    procedure Load(Filename: String; Mode: Longint); safecall;
    procedure Save(Filename: String; Remember: BOOL); safecall;
    procedure SaveCompleted(Filename: String); safecall;
    function GetCurFile: String; safecall;
  end;

procedure SHChangeNotify(Event: Integer; Flags: Cardinal; Item: String; Unused: Integer);
  external 'SHChangeNotify@shell32.dll stdcall';
function ExpandShortcutEnvironment(Source, Destination: String; Size: Integer): Integer;
  external 'ExpandEnvironmentStringsW@kernel32.dll stdcall';

function ShortcutBufferValue(Buffer: String): String;
begin
  if Pos(#0,Buffer) = 0 then RaiseException('Shortcut path exceeds buffer');
  { All buffers are freshly zero-filled. Paths cannot contain NUL; removing
    the unused NULs avoids slicing through non-BMP UTF-16 characters. }
  Result := Buffer;
  StringChangeEx(Result,#0,'',True);
end;

function ManagedShortcutIcon(Location, Target: String): Boolean;
var Name, Prefix: String;
begin
  Name := Trim(Location);
  Result := Name = '';
  if Result then Exit;
  if (Length(Name) > 1) and (Name[1] = '"') and (Name[Length(Name)] = '"') then
    Name := Copy(Name,2,Length(Name)-2);
  Name := ExpandFileName(Name);
  Prefix := AddBackslash(ExtractFileDir(Target))+'app\';
  Result := (CompareText(Name,ExpandFileName(Target)) = 0) or
    ((CompareText(Copy(Name,1,Length(Prefix)),Prefix) = 0) and
     (CompareText(ExtractFileName(Name),'icon_aplus.ico') = 0));
end;

function RefreshExistingShortcutIcons(Desktop, Target, Icon: String): Integer;
var FindRec: TFindRec; Obj: IUnknown; Link: IAplusShellLinkW; Persist: IAplusPersistFile;
    Path, ExistingTarget, ExistingIcon, Expanded: String; IconIndex, Size: Integer;
begin
  Result := 0;
  if not DirExists(Desktop) or not FileExists(Target) or not FileExists(Icon) then Exit;
  try
    if FindFirst(AddBackslash(Desktop)+'*.lnk',FindRec) then begin
      try
        repeat
          { Do not follow directory/reparse entries or overwrite read-only links. }
          if (FindRec.Attributes and ($10 or $400 or $1)) = 0 then begin
            try
              Path := AddBackslash(Desktop)+FindRec.Name;
              Obj := CreateComObject(StringToGuid('{00021401-0000-0000-C000-000000000046}'));
              Link := IAplusShellLinkW(Obj);
              Persist := IAplusPersistFile(Obj);
              Persist.Load(Path,2);
              ExistingTarget := StringOfChar(#0,32768);
              Link.GetPath(ExistingTarget,32768,0,4); { SLGP_RAWPATH, no Resolve }
              ExistingTarget := ShortcutBufferValue(ExistingTarget);
              Expanded := StringOfChar(#0,32768);
              Size := ExpandShortcutEnvironment(ExistingTarget,Expanded,32768);
              if (Size <= 0) or (Size > 32768) then RaiseException('Shortcut environment expansion failed');
              ExistingTarget := ShortcutBufferValue(Expanded);
              if (ExistingTarget <> '') and
                 (CompareText(ExpandFileName(ExistingTarget),ExpandFileName(Target)) = 0) then begin
                ExistingIcon := StringOfChar(#0,32768);
                Link.GetIconLocation(ExistingIcon,32768,IconIndex);
                ExistingIcon := ShortcutBufferValue(ExistingIcon);
                if (IconIndex = 0) and ManagedShortcutIcon(ExistingIcon,Target) then begin
                  if CompareText(ExistingIcon,Icon) <> 0 then begin
                    { Preserve TargetPath, Arguments, WorkingDirectory and all user text. }
                    Link.SetIconLocation(Icon,0);
                    Persist.Save(Path,True);
                    Result := Result + 1;
                  end;
                  { SHCNE_UPDATEITEM / SHCNF_PATHW|SHCNF_FLUSH, this link only. }
                  SHChangeNotify($2000,$1005,Path,0);
                end;
              end;
            except
              Log('APLUS shortcut icon refresh skipped an unreadable or locked link.');
            end;
          end;
        until not FindNext(FindRec);
      finally FindClose(FindRec); end;
    end;
  except
    { Cosmetic failures must never undo a verified install or change its pointer. }
    Log('APLUS shortcut icon refresh unavailable; installation retained.');
  end;
end;
