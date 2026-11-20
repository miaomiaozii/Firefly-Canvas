Option Explicit
Dim shell, files, toolDir, ps, launcher, arguments
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
toolDir = files.GetParentFolderName(WScript.ScriptFullName)
ps = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
launcher = files.BuildPath(toolDir, "firefly-desktop.ps1")
arguments = ""
If WScript.Arguments.Count = 1 Then
    If LCase(WScript.Arguments(0)) = "/autostart" Then arguments = " -AutoStart"
End If
' WScript and a hidden PowerShell keep the console from flashing on shortcut launch.
shell.Run """" & ps & """ -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & launcher & """" & arguments, 0, False
