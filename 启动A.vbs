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
        MsgBox "检测到未完成的软件更新，但恢复入口不完整。为保护行情和交易记录，本次未启动，请联系维护者。", 48, "Aplus安全恢复"
        WScript.Quit 20
    End If
    recoveryCode = ws.Run("""" & recoveryPy & """ """ & recoveryWorker & """ --recover """ & recoveryRequest & """", 0, True)
    If recoveryCode <> 0 Then
        MsgBox "软件更新仍在确认或需要人工处理。为避免新旧版本混用，本次未启动。", 48, "Aplus安全恢复"
        WScript.Quit recoveryCode
    End If
End If
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
