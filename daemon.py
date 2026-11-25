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

import portfolio_updates as portfolio_store

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
TASKS_FILE = os.path.join(DIR_PATH, "daymark_tasks.json")
ROUTINES_FILE = os.path.join(DIR_PATH, "daymark_routines.json")
TELEGRAM_CONFIG_FILE = os.path.join(DIR_PATH, "telegram_config.json")
TELEGRAM_CHATS_FILE = os.path.join(DIR_PATH, "telegram_chats.json")
TELEGRAM_SENT_FILE = os.path.join(DIR_PATH, "telegram_reminder_state.json")
TELEGRAM_LOG_FILE = os.path.join(DIR_PATH, "_telegram_reminders.log")
REMINDER_POPUP = os.path.join(DIR_PATH, "reminder_popup.py")
_INSTANCE_LOCK = None
_PORTFOLIO_SYNC_LOCK = threading.Lock()


def send_telegram_reminder(message):
    """Send a proactive reminder to chat IDs previously seen by the bot."""
    try:
        with open(TELEGRAM_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        token = str(config.get("bot_token", "")).strip()
        if not token or token == "PASTE_YOUR_BOT_TOKEN_HERE":
            return False
        with open(TELEGRAM_CHATS_FILE, "r", encoding="utf-8") as f:
            chats = json.load(f)
        if not isinstance(chats, list) or not chats:
            log_telegram_reminder("No Telegram chats are registered")
            return False
        allowed = {str(value) for value in config.get("allowed_chat_ids", []) if str(value)}
        targets = [str(chat) for chat in chats if not allowed or str(chat) in allowed]
        if not targets:
            log_telegram_reminder("No registered chats are allowed by Telegram config")
            return False
        sent_any = False
        endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
        for chat_id in targets:
            payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
            request = urllib.request.Request(endpoint, data=payload,
                                             headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    sent_any = sent_any or bool(json.loads(response.read().decode("utf-8")).get("ok"))
            except Exception as exc:
                log_telegram_reminder(f"Delivery failed ({type(exc).__name__})")
        log_telegram_reminder("Reminder delivered" if sent_any else "Telegram did not accept reminder")
        return sent_any
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        log_telegram_reminder("Telegram reminder configuration could not be read")
        return False


def log_telegram_reminder(message):
    try:
        with open(TELEGRAM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def load_sent_telegram_reminders():
    try:
        with open(TELEGRAM_SENT_FILE, "r", encoding="utf-8") as f:
            values = json.load(f)
        return set(values if isinstance(values, list) else [])
    except (OSError, json.JSONDecodeError):
        return set()


def save_sent_telegram_reminders(values):
    temp = TELEGRAM_SENT_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(sorted(values), f, indent=2)
    os.replace(temp, TELEGRAM_SENT_FILE)


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


def show_event_popup(title, start_time, message="", mode="event"):
    """Show the centered Daymark reminder independently of the webview."""
    try:
        # Use pythonw.exe — no console window at all, tkinter gets the interactive desktop
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        popup = subprocess.Popen(
            [pythonw, REMINDER_POPUP, str(title), str(start_time), str(message), str(mode)],
            cwd=DIR_PATH,
            creationflags=0x00000008,   # DETACHED_PROCESS
        )
        print(f"[daemon] Popup launched: {title} (PID {popup.pid})")
    except Exception as e:
        print(f"[daemon] Could not show event popup: {e}")


def get_idle_seconds():
    """Return seconds since the last keyboard/mouse input on this Windows session."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        user32 = ctypes.windll.user32
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        # dwTime is a 32-bit tick count and wraps roughly every 49.7 days.
        tick_count = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = (tick_count - info.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception as exc:
        print(f"[daemon] Could not read Windows idle time: {exc}")
        return 0


def start_portfolio_outbox_sync():
    """Retry queued user check-ins without blocking the reminder watch loop."""
    if not _PORTFOLIO_SYNC_LOCK.acquire(blocking=False):
        return

    def sync():
        try:
            portfolio_store.sync_outbox(limit=10)
        except Exception as exc:
            print(f"[daemon] Portfolio check-in sync failed: {exc}")
        finally:
            _PORTFOLIO_SYNC_LOCK.release()

    threading.Thread(target=sync, name="portfolio-outbox-sync", daemon=True).start()


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
    notified_routines = set()
    notified_agendas = set()
    notified_telegram_events = load_sent_telegram_reminders()
    ram_alert_sent = False        # tracks whether we've already alerted this breach
    RAM_ALERT_GB   = 9.0          # alert threshold
    RAM_CLEAR_GB   = 8.5          # hysteresis — clear alert once usage drops below this
    idle_reminder_sent = False
    IDLE_REMINDER_SECONDS = 30*60
    last_portfolio_sync = 0

    while True:
        try:
            ensure_server()
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # Persistent check-in cadence: one prompt at a time during waking
            # hours, with no burst of missed prompts after sleep or downtime.
            try:
                if portfolio_store.claim_due_checkin(now):
                    show_event_popup(
                        "Anything Updates?", "now",
                        "Anything new in your academic, research, or professional work?",
                        "portfolio_checkin",
                    )
            except Exception as exc:
                print(f"[daemon] Portfolio check-in scheduler error: {exc}")

            if time.monotonic() - last_portfolio_sync >= 60:
                last_portfolio_sync = time.monotonic()
                start_portfolio_outbox_sync()

            idle_seconds = get_idle_seconds()
            if idle_seconds > IDLE_REMINDER_SECONDS and not idle_reminder_sent:
                idle_reminder_sent = True
                print(f"[daemon] Idle reminder triggered after {int(idle_seconds)} seconds")
                show_event_popup(
                    "Read AI News", "now",
                    "You’ve been away from the keyboard for over 1 minute. Time for a quick read.",
                    "event",
                )
            elif idle_seconds <= IDLE_REMINDER_SECONDS:
                # Rearm after the user returns, so each long idle period gets one popup.
                idle_reminder_sent = False

            # The agenda alarm and recurring fundamentals live in Daymark's
            # local schedule, independent of Google Calendar and browser timers.
            routine_config = {"daily_alarm": {"enabled": True, "time": "08:30"}, "routines": []}
            if os.path.exists(ROUTINES_FILE):
                try:
                    with open(ROUTINES_FILE, "r", encoding="utf-8") as f:
                        routine_config.update(json.load(f))
                except Exception as exc:
                    print(f"[daemon] Could not read recurring schedule: {exc}")

            def due_now(time_text, window_seconds=120):
                try:
                    hour, minute = map(int, str(time_text).split(":")[:2])
                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    delta = (target - now).total_seconds()
                    return -window_seconds < delta <= 0
                except (ValueError, TypeError):
                    return False

            alarm = routine_config.get("daily_alarm", {})
            agenda_key = f"agenda-{today_str}"
            if alarm.get("enabled", True) and due_now(alarm.get("time", "08:30")) and agenda_key not in notified_agendas:
                notified_agendas.add(agenda_key)
                lines = []
                weekday = now.strftime("%A").lower()
                for routine in routine_config.get("routines", []):
                    if (weekday in routine.get("days", [])
                            and (not routine.get("start_date") or routine["start_date"] <= today_str)
                            and (not routine.get("end_date") or today_str <= routine["end_date"])):
                        plan = (routine.get("daily_plan") or {}).get(today_str, {})
                        lines.append(f"{plan.get('time', routine.get('time', ''))} — {plan.get('title', routine.get('title', 'Routine'))} ({plan.get('duration', routine.get('duration', 60))} min)")
                if os.path.exists(TASKS_FILE):
                    try:
                        with open(TASKS_FILE, "r", encoding="utf-8") as f:
                            todays_tasks = json.load(f)
                        for task in todays_tasks if isinstance(todays_tasks, list) else []:
                            if task.get("done") or task.get("status") == "done":
                                continue
                            due = task.get("due_date") or task.get("date") or ""
                            if due and due != today_str and today_str not in task.get("title", ""):
                                continue
                            lines.append(f"{task.get('time', 'Any time')} — {task.get('title', 'Action')}")
                    except Exception:
                        pass
                if os.path.exists(EVENTS_FILE):
                    try:
                        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                            todays_events = json.load(f)
                        for event in todays_events if isinstance(todays_events, list) else []:
                            if event.get("date") == today_str:
                                lines.append(f"{event.get('time', 'Any time')} — {event.get('title', 'Scheduled block')}")
                    except Exception:
                        pass
                agenda_text = "\n".join(lines[:7]) or "Your day is clear. Pick one meaningful thing and start there."
                show_event_popup("Your day, at a glance", alarm.get("time", "08:30"), agenda_text, "agenda")

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
                        reminder_key = f"{ev.get('id', ev.get('title'))}-{today_str}-10min"
                        reminder_delta = (event_dt - datetime.timedelta(minutes=10) - now).total_seconds()
                        if -300 < reminder_delta <= 0 and reminder_key not in notified_telegram_events:
                            remaining_minutes = max(1, int((seconds_until + 59) // 60))
                            reminder_text = f"⏰ {ev.get('title', 'Daymark event')} starts in about {remaining_minutes} minutes ({event_time})."
                            if send_telegram_reminder(reminder_text):
                                notified_telegram_events.add(reminder_key)
                                save_sent_telegram_reminders(notified_telegram_events)
                        # If a timed event is created too late for its 10-minute
                        # heads-up, send one at the start instead of silently
                        # skipping Telegram altogether. Avoid a second message
                        # when the advance reminder was delivered successfully.
                        start_key = f"{ev.get('id', ev.get('title'))}-{today_str}-start"
                        if (-300 < seconds_until <= 0 and reminder_key not in notified_telegram_events
                                and start_key not in notified_telegram_events):
                            start_text = f"⏰ {ev.get('title', 'Daymark event')} starts now ({event_time})."
                            if send_telegram_reminder(start_text):
                                notified_telegram_events.add(start_key)
                                save_sent_telegram_reminders(notified_telegram_events)
                        key = f"{ev.get('id', ev.get('title'))}-{today_str}-{event_time}"
                        if -300 < seconds_until <= 0 and key not in notified_daymark_events:
                            notified_daymark_events.add(key)
                            show_event_popup(ev.get("title", "Scheduled block"), event_time,
                                             f"Your {event_time} block is underway.", "event")
                            toast("Daymark block starting", f"{ev.get('title', 'Scheduled block')} — {event_time}")
                    except Exception:
                        pass

            # Fundamental routines get a centered, persistent popup at their
            # local start time, even if the app window is behind another app.
            weekday = now.strftime("%A").lower()
            for routine in routine_config.get("routines", []):
                if weekday not in routine.get("days", []):
                    continue
                if routine.get("start_date") and today_str < routine["start_date"]:
                    continue
                if routine.get("end_date") and today_str > routine["end_date"]:
                    continue
                plan = (routine.get("daily_plan") or {}).get(today_str, {})
                routine_time = plan.get("time", routine.get("time"))
                routine_key = f"{routine.get('id', routine.get('title'))}-{today_str}"
                if routine_key in notified_routines or not due_now(routine_time):
                    continue
                notified_routines.add(routine_key)
                title = plan.get("title", routine.get("title", "Daily routine"))
                duration = plan.get("duration", routine.get("duration", 60))
                message = plan.get("note") or f"Your {duration}-minute {title.lower()} session is underway."
                show_event_popup(title, routine_time or "now", message, "event")

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

        time.sleep(10)


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
                venv_pythonw = os.path.join(DIR_PATH, ".venv", "Scripts", "pythonw.exe")
                bot_python = venv_pythonw if os.path.exists(venv_pythonw) else sys.executable
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                subprocess.Popen(
                    [bot_python, tg_bot],
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
