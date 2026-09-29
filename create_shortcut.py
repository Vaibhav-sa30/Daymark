"""
Run once to create pinnable Daymark shortcuts on Desktop and Start Menu.
"""
import os, sys, subprocess

DIR_PATH = os.path.dirname(os.path.abspath(__file__))
ICON_ICO  = os.path.join(DIR_PATH, "icon.ico")
APP_SCRIPT = os.path.join(DIR_PATH, "app_window.py")
PYTHONW   = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.exists(PYTHONW):
    PYTHONW = sys.executable

DESKTOP = os.path.join(os.environ.get("USERPROFILE",""), "OneDrive", "Desktop")
if not os.path.exists(DESKTOP):
    DESKTOP = os.path.join(os.environ.get("USERPROFILE",""), "Desktop")
START_MENU = os.path.join(os.environ.get("APPDATA",""),
                          r"Microsoft\Windows\Start Menu\Programs")

def make_shortcut(dest_dir, name="Daymark.lnk"):
    lnk = os.path.join(dest_dir, name)
    ps = (
        f'$s=New-Object -ComObject WScript.Shell;'
        f'$l=$s.CreateShortcut("{lnk}");'
        f'$l.TargetPath="{PYTHONW}";'
        f'$l.Arguments=\'"{APP_SCRIPT}"\';'
        f'$l.WorkingDirectory="{DIR_PATH}";'
        f'$l.Description="Daymark Execution Assistant";'
        f'$l.IconLocation="{ICON_ICO}";'
        f'$l.WindowStyle=1;'   # 1 = normal window, shown in foreground
        f'$l.Save()'
    )
    r = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command",ps],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  Created: {lnk}")
    else:
        print(f"  Failed ({name}): {r.stderr.strip()[:120]}")

print("Creating shortcuts...")
make_shortcut(DESKTOP)
make_shortcut(START_MENU)
print("\nDone!")
print("  1. Double-click Daymark on your Desktop to launch")
print("  2. While it's running, right-click the taskbar button → Pin to taskbar")
