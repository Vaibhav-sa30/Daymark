Set WshShell = CreateObject("WScript.Shell")
strPath = WshShell.CurrentDirectory
WshShell.Run "pythonw """ & strPath & "\app_window.py""", 0, False
