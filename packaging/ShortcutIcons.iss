{ Cosmetic repair only: no new links, no execution, no global icon-cache reset. }
procedure SHChangeNotify(Event: Integer; Flags: Cardinal; Item: String; Unused: Integer);
  external 'SHChangeNotify@shell32.dll stdcall';

function ManagedShortcutIcon(Location, Target: String): Boolean;
var Name, Prefix: String; P: Integer;
begin
  Name := Trim(Location);
  P := Length(Name);
  while P > 0 do begin
    if Name[P] = ',' then Break;
    P := P - 1;
  end;
  if P > 0 then Name := Trim(Copy(Name,1,P-1));
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
var FindRec: TFindRec; Shell, Link: Variant; Path, ExistingTarget, ExistingIcon, Wanted: String;
begin
  Result := 0;
  if not DirExists(Desktop) or not FileExists(Target) or not FileExists(Icon) then Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Wanted := Icon+',0';
    if FindFirst(AddBackslash(Desktop)+'*.lnk',FindRec) then begin
      try
        repeat
          { Do not follow directory/reparse entries or overwrite read-only links. }
          if (FindRec.Attributes and ($10 or $400 or $1)) = 0 then begin
            try
              Path := AddBackslash(Desktop)+FindRec.Name;
              Link := Shell.CreateShortcut(Path);
              ExistingTarget := Link.TargetPath;
              ExistingTarget := Shell.ExpandEnvironmentStrings(ExistingTarget);
              if (ExistingTarget <> '') and
                 (CompareText(ExpandFileName(ExistingTarget),ExpandFileName(Target)) = 0) then begin
                ExistingIcon := Link.IconLocation;
                if ManagedShortcutIcon(ExistingIcon,Target) then begin
                  if CompareText(ExistingIcon,Wanted) <> 0 then begin
                    { Preserve TargetPath, Arguments, WorkingDirectory and all user text. }
                    Link.IconLocation := Wanted;
                    Link.Save;
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
