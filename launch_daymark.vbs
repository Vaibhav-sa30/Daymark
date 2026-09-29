' Daymark Silent Desktop Launcher
' Runs without opening a terminal / command prompt window
Set WshShell = CreateObject("WScript.Shell")
strPath = WshShell.CurrentDirectory
WshShell.Run "cmd /c """ & strPath & "\launch_daymark.bat""", 0, False
