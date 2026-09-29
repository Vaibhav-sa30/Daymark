"""
Daymark Windows Desktop Accountability Daemon
- System tray icon with right-click menu
- Opens Daymark in app-mode browser on click
- Keeps server.py alive in background
- Native Windows toast notifications for meetings & accountability checks
"""

import sys
import os
import time
import json
import threading
import subprocess
import datetime
import urllib.request

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

SERVER_PORT = 4173
LOCAL_URL = f"http://localhost:{SERVER_PORT}"
DIR_PATH = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(DIR_PATH, "calendar_cache.json")
EVENTS_FILE = os.path.join(DIR_PATH, "daymark_events.json")
REMINDER_POPUP = os.path.join(DIR_PATH, "reminder_popup.py")
_INSTANCE_LOCK = None


def acquire_instance_lock():
    """Keep duplicate desktop daemons from spawning competing Telegram bots."""
    global _INSTANCE_LOCK
    try:
        import msvcrt
        lock_path = os.path.join(DIR_PATH, "_daemon.lock")
        handle = open(lock_path, "a+b")
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        _INSTANCE_LOCK = handle
        return True
    except Exception as exc:
        print(f"[daemon] Could not acquire instance lock: {exc}")
        return False


# ── Tray icon image (drawn programmatically — no asset needed) ──────────────
def make_icon_image(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Dark green rounded square background
    margin = 4
    d.rounded_rectangle([margin, margin, size - margin, size - margin],
                         radius=14, fill="#3d6b52")
    # White "D" mark
    cx, cy = size // 2, size // 2
    d.ellipse([cx - 14, cy - 16, cx + 14, cy + 16], outline="white", width=4)
    d.rectangle([cx - 14, cy - 16, cx, cy + 16], fill="#3d6b52")
    d.line([cx - 10, cy, cx - 10, cy], fill="white", width=3)
    return img


# ── Open Daymark in app-mode browser ────────────────────────────────────────
def open_daymark(_=None):
    url = LOCAL_URL
    for browser_exe, flag in [
        ("msedge.exe", f'--app="{url}" --window-size=1280,820'),
        ("chrome.exe", f'--app="{url}" --window-size=1280,820'),
    ]:
        try:
            result = subprocess.run(["where", browser_exe], capture_output=True, text=True)
            if result.returncode == 0:
                subprocess.Popen(f'start "" "{result.stdout.strip().splitlines()[0]}" {flag}',
                                 shell=True)
                return
        except Exception:
            pass
    # Fallback: default browser
    import webbrowser
    webbrowser.open(url)


# ── Windows toast notification ───────────────────────────────────────────────
def toast(title, message):
    clean = lambda s: s.replace('"', '`"').replace("'", "`'")[:120]
    ps = f"""
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
    $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $n = $t.GetElementsByTagName('text')
    $n.Item(0).AppendChild($t.CreateTextNode("{clean(title)}")) > $null
    $n.Item(1).AppendChild($t.CreateTextNode("{clean(message)}")) > $null
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Daymark").Show([Windows.UI.Notifications.ToastNotification]::new($t))
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, timeout=5,
            creationflags=0x08000000,
            startupinfo=si,
        )
    except Exception:
        pass


def show_event_popup(title, start_time):
    """Show a native, always-on-top reminder independently of the webview."""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        subprocess.Popen(
            [sys.executable, REMINDER_POPUP, str(title), str(start_time)],
            creationflags=0x08000000,
            startupinfo=si,
            cwd=DIR_PATH,
        )
    except Exception as e:
        print(f"[daemon] Could not show event popup: {e}")


# ── Ensure server is running ─────────────────────────────────────────────────
def ensure_server():
    try:
        urllib.request.urlopen(f"{LOCAL_URL}/api/status", timeout=2)
        return
    except Exception:
        pass
    server_script = os.path.join(DIR_PATH, "server.py")
    subprocess.Popen(
        [sys.executable, server_script, str(SERVER_PORT)],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
        cwd=DIR_PATH
    )
    time.sleep(1.5)


# ── Background watch loop ────────────────────────────────────────────────────
def watch_loop():
    last_hourly = 0
    notified_meetings = set()
    notified_daymark_events = set()
    ram_alert_sent = False        # tracks whether we've already alerted this breach
    RAM_ALERT_GB   = 9.0          # alert threshold
    RAM_CLEAR_GB   = 8.5          # hysteresis — clear alert once usage drops below this

    while True:
        try:
            ensure_server()
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # Meeting pre-reminders (30 min before)
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                for ev in cdata.get("events", []):
                    if ev.get("date") != today_str:
                        continue
                    t = ev.get("time", "")
                    if not t or t == "All Day":
                        continue
                    try:
                        eh, em = map(int, t.split(":"))
                        ev_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
                        delta = (ev_dt - now).total_seconds() / 60
                        key = f"{ev.get('id')}-30min"
                        if 28 <= delta <= 32 and key not in notified_meetings:
                            notified_meetings.add(key)
                            toast(
                                f"Meeting in 30 min — {ev['title'][:50]}",
                                f"Starts at {t}. Wrap up and prep now."
                            )
                    except Exception:
                        pass

            # Daymark event start reminders. This runs in the background daemon,
            # so the popup still appears when the Daymark window is behind another app.
            if os.path.exists(EVENTS_FILE):
                with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                    daymark_events = json.load(f)
                for ev in daymark_events if isinstance(daymark_events, list) else []:
                    if ev.get("date") != today_str:
                        continue
                    event_time = ev.get("time", "")
                    if not event_time or event_time == "All Day":
                        continue
                    try:
                        eh, em = map(int, event_time.split(":")[:2])
                        event_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
                        seconds_until = (event_dt - now).total_seconds()
                        key = f"{ev.get('id', ev.get('title'))}-{today_str}-{event_time}"
                        if -300 < seconds_until <= 0 and key not in notified_daymark_events:
                            notified_daymark_events.add(key)
                            show_event_popup(ev.get("title", "Scheduled block"), event_time)
                            toast("Daymark block starting", f"{ev.get('title', 'Scheduled block')} — {event_time}")
                    except Exception:
                        pass

            # Hourly accountability nudge (9am–9pm)
            if time.time() - last_hourly > 3600 and 9 <= now.hour <= 21:
                last_hourly = time.time()
                toast("Daymark Check-in", "How's progress on your primary outcome? Open Daymark to log.")

            # RAM guard — keep 4GB free for local AI model
            try:
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong),
                                 ("dwMemoryLoad", ctypes.c_ulong),
                                 ("ullTotalPhys", ctypes.c_ulonglong),
                                 ("ullAvailPhys", ctypes.c_ulonglong),
                                 ("ullTotalPageFile", ctypes.c_ulonglong),
                                 ("ullAvailPageFile", ctypes.c_ulonglong),
                                 ("ullTotalVirtual", ctypes.c_ulonglong),
                                 ("ullAvailVirtual", ctypes.c_ulonglong),
                                 ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(stat)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                used_gb  = (stat.ullTotalPhys - stat.ullAvailPhys) / (1024**3)
                avail_gb = stat.ullAvailPhys / (1024**3)

                if used_gb >= RAM_ALERT_GB and not ram_alert_sent:
                    ram_alert_sent = True
                    toast(
                        f"⚠ RAM at {used_gb:.1f} GB — Free Some Up",
                        f"Only {avail_gb:.1f} GB free. Close heavy apps to keep 4 GB available for Daymark AI."
                    )
                elif used_gb < RAM_CLEAR_GB and ram_alert_sent:
                    ram_alert_sent = False  # reset — will alert again next time it spikes
            except Exception:
                pass

        except Exception as e:
            print(f"[daemon] watch error: {e}")

        time.sleep(60)


# ── Install to Windows Startup ───────────────────────────────────────────────
def install_startup():
    startup = os.path.join(os.environ["APPDATA"],
                           r"Microsoft\Windows\Start Menu\Programs\Startup")
    vbs = os.path.join(DIR_PATH, "launch_daymark.vbs")
    lnk = os.path.join(startup, "DaymarkAssistant.lnk")
    ps = f"""
    $s = New-Object -ComObject WScript.Shell
    $l = $s.CreateShortcut("{lnk}")
    $l.TargetPath = "{vbs}"
    $l.WorkingDirectory = "{DIR_PATH}"
    $l.Description = "Daymark Assistant"
    $l.WindowStyle = 7
    $l.Save()
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=True, creationflags=0x08000000, startupinfo=si
    )
    print(f"Installed to Windows Startup: {lnk}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not acquire_instance_lock():
        print("[daemon] Another Daymark daemon is already running")
        return
    ensure_server()

    # Start Telegram bot if configured
    tg_config = os.path.join(DIR_PATH, "telegram_config.json")
    tg_bot    = os.path.join(DIR_PATH, "telegram_bot.py")
    if os.path.exists(tg_config) and os.path.exists(tg_bot):
        try:
            with open(tg_config, "r") as f:
                tg_cfg = json.load(f)
            token = tg_cfg.get("bot_token", "")
            if token and token != "PASTE_YOUR_BOT_TOKEN_HERE":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                subprocess.Popen(
                    [sys.executable, tg_bot],
                    creationflags=0x08000000,
                    startupinfo=si,
                    cwd=DIR_PATH,
                    stdout=open(os.path.join(DIR_PATH, "_telegram.log"), "w", encoding="utf-8"),
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
                print("[daemon] Telegram bot started")
        except Exception as e:
            print(f"[daemon] Telegram bot failed to start: {e}")

    if not HAS_TRAY:
        print("[daemon] pystray/pillow not available — running without tray icon")
        toast("Daymark Active", "Running in background. Server is live.")
        watch_loop()
        return

    icon_img = make_icon_image(64)

    def on_open(_icon, _item):
        open_daymark()

    def on_quit(_icon, _item):
        _icon.stop()
        sys.exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Open Daymark", on_open, default=True),
        pystray.MenuItem("Quit", on_quit),
    )

    tray = pystray.Icon(
        name="Daymark",
        icon=icon_img,
        title="Daymark — Click to open",
        menu=menu,
    )

    # Run watch loop in background thread
    t = threading.Thread(target=watch_loop, daemon=True)
    t.start()

    toast("Daymark Active", "Running in system tray. Click the icon to open.")
    tray.run()  # blocks — tray lives until quit


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--install-startup":
        install_startup()
    else:
        main()
