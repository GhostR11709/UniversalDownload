Option Explicit
Dim shell, root, namedServer, pythonw
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
namedServer = root & "\UD Server.exe"
pythonw = root & "\.venv\Scripts\pythonw.exe"
shell.CurrentDirectory = root
If CreateObject("Scripting.FileSystemObject").FileExists(namedServer) Then
  shell.Run """" & namedServer & """", 0, False
ElseIf CreateObject("Scripting.FileSystemObject").FileExists(pythonw) Then
  shell.Run """" & pythonw & """ -m server", 0, False
Else
  shell.Run "py -3 -m server", 0, False
End If
