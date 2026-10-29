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
recoveryPy = fso.BuildPath(scriptDir, ".\.venv\Scripts\python.exe")
If fso.FileExists(pointer) Then
    Set pointerFile = fso.OpenTextFile(pointer, 1, False)
    candidatePy = Trim(pointerFile.ReadAll)
    pointerFile.Close
    If fso.FileExists(candidatePy) Then recoveryPy = candidatePy
End If
activeTransaction = fso.BuildPath(scriptDir, ".git\aplus-update\active-request.txt")
If fso.FileExists(activeTransaction) Then
    Set activeFile = fso.OpenTextFile(activeTransaction, 1, False)
    recoveryRequest = Trim(activeFile.ReadAll)
    activeFile.Close
    recoveryWorker = fso.BuildPath(fso.GetParentFolderName(recoveryRequest), "worker.py")
    If Not fso.FileExists(recoveryPy) Or Not fso.FileExists(recoveryWorker) Then
        MsgBox U("68c0 6d4b 5230 672a 5b8c 6210 7684 8f6f 4ef6 66f4 65b0 ff0c 4f46 6062 590d 5165 53e3 4e0d 5b8c 6574 3002 4e3a 4fdd 62a4 884c 60c5 548c 4ea4 6613 8bb0 5f55 ff0c 672c 6b21 672a 542f 52a8 ff0c 8bf7 8054 7cfb 7ef4 62a4 8005 3002"), 48, U("41 70 6c 75 73 5b89 5168 6062 590d")
        WScript.Quit 20
    End If
    recoveryCode = ws.Run("""" & recoveryPy & """ """ & recoveryWorker & """ --recover """ & recoveryRequest & """", 0, True)
    If recoveryCode <> 0 Then
        MsgBox U("8f6f 4ef6 66f4 65b0 4ecd 5728 786e 8ba4 6216 9700 8981 4eba 5de5 5904 7406 3002 4e3a 907f 514d 65b0 65e7 7248 672c 6df7 7528 ff0c 672c 6b21 672a 542f 52a8 3002"), 48, U("41 70 6c 75 73 5b89 5168 6062 590d")
        WScript.Quit recoveryCode
    End If
End If

Function U(value)
    U = ""
    For Each code In Split(value, " ")
        U = U & ChrW(CLng("&H" & code))
    Next
End Function
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
