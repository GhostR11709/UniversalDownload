Option Explicit
Dim shell, root, pythonw
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
shell.CurrentDirectory = root
If CreateObject("Scripting.FileSystemObject").FileExists(pythonw) Then
  shell.Run """" & pythonw & """ -m server", 0, False
Else
  shell.Run "py -3 -m server", 0, False
End If
