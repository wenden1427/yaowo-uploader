Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
RootDir = fso.GetParentFolderName(WScript.ScriptFullName)
PythonPath = RootDir & "\python-portable\python\pythonw.exe"
ScriptPath = RootDir & "\main.py"
WshShell.Run """" & PythonPath & """ """ & ScriptPath & """", 0, False
