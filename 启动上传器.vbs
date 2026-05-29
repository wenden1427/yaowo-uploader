Set WshShell = CreateObject("WScript.Shell")
PythonPath = "E:\ai-test\python-portable\python\python.exe"
ScriptPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\main.py"
WshShell.Run """" & PythonPath & """ """ & ScriptPath & """", 0, False
