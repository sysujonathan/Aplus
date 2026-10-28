Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

On Error Resume Next
runtimeHome = ws.RegRead("HKCU\Environment\A_WORKBENCH_HOME")
If Err.Number = 0 And Len(runtimeHome) > 0 Then
    ws.Environment("PROCESS")("A_WORKBENCH_HOME") = runtimeHome
End If
Err.Clear
On Error GoTo 0

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pointer = fso.BuildPath(scriptDir, ".git\aplus-update\current-python.txt")
py = ""
If fso.FileExists(pointer) Then
    Set pointerFile = fso.OpenTextFile(pointer, 1, False)
    managedPy = Trim(pointerFile.ReadAll)
    pointerFile.Close
    If fso.FileExists(managedPy) Then
        managedPyw = Replace(managedPy, "python.exe", "pythonw.exe", 1, -1, 1)
        If fso.FileExists(managedPyw) Then
            py = managedPyw
        Else
            py = managedPy
        End If
    End If
End If
If Len(py) = 0 Then py = fso.BuildPath(scriptDir, ".\.venv\Scripts\pythonw.exe")
entry = fso.BuildPath(scriptDir, "launch_dashboard.py")

If fso.FileExists(py) Then
    ws.Run """" & py & """ """ & entry & """", 0, False
Else
    ws.Run "pythonw """ & entry & """", 0, False
End If
