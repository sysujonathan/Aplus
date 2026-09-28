Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
py = fso.BuildPath(scriptDir, ".\.venv\Scripts\pythonw.exe")
entry = fso.BuildPath(scriptDir, "launch_dashboard.py")

If fso.FileExists(py) Then
    ws.Run """" & py & """ """ & entry & """", 0, False
Else
    ws.Run "pythonw """ & entry & """", 0, False
End If
