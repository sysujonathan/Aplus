Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("Shell.Application")
MsgBox U("8bf7 5148 4ece 7cfb 7edf 6258 76d8 5b8c 6574 9000 51fa 20 41 70 6c 75 73 ff0c 518d 9009 62e9 539f 6765 7684 65e5 7528 5b89 88c5 76ee 5f55 3002 8fc7 6e21 52a9 624b 4f1a 4fdd 7559 539f 6570 636e 5e76 5728 6210 529f 540e 81ea 52a8 6253 5f00 8f6f 4ef6 3002"), 64, U("41 70 6c 75 73 20 4e00 6b21 6027 5b89 5168 8fc7 6e21")
Set folder = shell.BrowseForFolder(0, U("9009 62e9 539f 6765 7684 20 41 70 6c 75 73 5f 64 61 69 6c 79 20 6216 5176 4ed6 20 41 70 6c 75 73 20 65e5 7528 5b89 88c5 76ee 5f55"), 0)
If folder Is Nothing Then WScript.Quit 0
root = folder.Self.Path
py = fso.BuildPath(root, ".venv\Scripts\python.exe")
pointer = fso.BuildPath(root, ".git\aplus-update\current-python.txt")
If fso.FileExists(pointer) Then
    Set stream = fso.OpenTextFile(pointer, 1, False)
    candidate = Trim(stream.ReadAll)
    stream.Close
    If fso.FileExists(candidate) Then py = candidate
End If
If Not fso.FileExists(py) Then
    MsgBox U("539f 5b89 88c5 7684 20 50 79 74 68 6f 6e 20 73af 5883 7f3a 5931 ff0c 5df2 505c 6b62 3002 8bf7 4fdd 7559 5b89 88c5 548c 6570 636e ff0c 8054 7cfb 7ef4 62a4 8005 3002"), 48, U("41 70 6c 75 73 20 5b89 5168 8fc7 6e21")
    WScript.Quit 1
End If
On Error Resume Next
runtime = ws.RegRead("HKCU\Environment\A_WORKBENCH_HOME")
If Err.Number = 0 And Len(runtime) > 0 Then ws.Environment("PROCESS")("A_WORKBENCH_HOME") = runtime
Err.Clear
On Error GoTo 0
helper = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "tools\legacy_update_bootstrap.py")
If Not fso.FileExists(helper) Then
    MsgBox U("8fc7 6e21 5de5 5177 4e0d 5b8c 6574 ff0c 8bf7 89e3 538b 5b98 65b9 5de5 5177 5305 540e 8fd0 884c 3002"), 48, U("41 70 6c 75 73 20 5b89 5168 8fc7 6e21")
    WScript.Quit 1
End If
ws.Run """" & py & """ -I """ & helper & """ --root """ & root & """", 0, True

Function U(value)
    U = ""
    For Each code In Split(value, " ")
        U = U & ChrW(CLng("&H" & code))
    Next
End Function
