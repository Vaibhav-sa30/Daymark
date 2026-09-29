"""
Daymark Desktop App
"""

import sys
import os
import time
import subprocess
import urllib.request
import traceback

DIR_PATH  = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(DIR_PATH, "server.py")
DAEMON_PY = os.path.join(DIR_PATH, "daemon.py")
ICON_PATH = os.path.join(DIR_PATH, "icon.ico")
LOG_PATH  = os.path.join(DIR_PATH, "_app.log")
PORT      = 4173
APP_URL   = f"http://localhost:{PORT}"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

def _silent_startupinfo():
    """STARTUPINFO that hides any console window — belt-and-suspenders."""
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si

# ── Log everything so we can debug silently ───────────────────────────────────
import builtins
_orig_print = builtins.print
def _log(*args, **kwargs):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            import datetime
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] " + " ".join(str(a) for a in args) + "\n")
    except Exception:
        pass
    _orig_print(*args, **kwargs)
builtins.print = _log

# ── Helpers ───────────────────────────────────────────────────────────────────
def server_up():
    try:
        urllib.request.urlopen(f"{APP_URL}/api/status", timeout=1)
        return True
    except Exception:
        return False

def start_server():
    log_file = open(os.path.join(DIR_PATH, "_server.log"), "w", encoding="utf-8")
    p = subprocess.Popen(
        [sys.executable, SERVER_PY],
        creationflags=NO_WINDOW,
        startupinfo=_silent_startupinfo(),
        cwd=DIR_PATH,
        stdout=log_file,
        stderr=log_file,
    )
    print(f"Server process PID: {p.pid}")
    return p

def start_daemon():
    subprocess.Popen(
        [sys.executable, DAEMON_PY],
        creationflags=NO_WINDOW,
        startupinfo=_silent_startupinfo(),
        cwd=DIR_PATH,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def wait_for_server(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_up():
            return True
        time.sleep(0.4)
    return False

# ── Single-instance via lock file ─────────────────────────────────────────────
LOCK_FILE = os.path.join(DIR_PATH, "_app.lock")

def acquire_lock():
    """Try to acquire lock. Returns True if we are the first instance."""
    import atexit
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            # Check if that PID is actually a running pythonw process
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True
            )
            if str(pid) in result.stdout and "python" in result.stdout.lower():
                print(f"Already running (PID {pid}), exiting.")
                return False
            # PID is dead or not python — stale lock
            print(f"Stale lock (PID {pid} not running) — clearing.")
        except Exception:
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    def cleanup():
        try: os.remove(LOCK_FILE)
        except Exception: pass
    atexit.register(cleanup)
    return True

# ── Loading HTML ──────────────────────────────────────────────────────────────
LOADING_HTML = """\
<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#fbfaf7;display:flex;flex-direction:column;align-items:center;
     justify-content:center;height:100vh;font-family:Arial,sans-serif}
.mark{width:56px;height:56px;background:#3d6b52;border-radius:16px;display:flex;
      align-items:center;justify-content:center;font-size:34px;color:#fff;
      margin-bottom:20px}
.title{font-size:22px;font-weight:700;color:#26332e;margin-bottom:6px}
.sub{font-size:13px;color:#838980;margin-bottom:32px}
.dots{display:flex;gap:8px}
.dot{width:9px;height:9px;border-radius:50%;background:#a0b09a;
     animation:b 1.2s infinite ease-in-out}
.dot:nth-child(2){animation-delay:.2s}
.dot:nth-child(3){animation-delay:.4s}
@keyframes b{0%,80%,100%{transform:translateY(0);opacity:.4}40%{transform:translateY(-8px);opacity:1}}
</style></head><body>
<div class="mark">D</div>
<div class="title">Daymark</div>
<div class="sub">Starting up...</div>
<div class="dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>
</body></html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Clear log on fresh start
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"=== Daymark starting {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    print(f"Python: {sys.executable}")
    print(f"CWD: {DIR_PATH}")

    if not acquire_lock():
        return

    # Start server
    if not server_up():
        print("Server not running — starting...")
        start_server()
    else:
        print("Server already running.")

    # Start daemon
    start_daemon()

    try:
        import webview
        print(f"pywebview imported OK")

        # Write loading page
        loading_file = os.path.join(DIR_PATH, "_loading.html")
        with open(loading_file, "w", encoding="utf-8") as f:
            f.write(LOADING_HTML)
        loading_url = "file:///" + loading_file.replace("\\", "/")

        window = webview.create_window(
            title="Daymark",
            url=loading_url,
            width=1320,
            height=860,
            min_size=(900, 600),
            resizable=True,
            background_color="#fbfaf7",
        )
        print("Window created")

        def on_ready():
            print("on_ready called — waiting for server...")
            ok = wait_for_server(timeout=25)
            if ok:
                print(f"Server is up — navigating to {APP_URL}")
                time.sleep(0.3)
                window.load_url(APP_URL)
            else:
                print("Server timed out!")
                window.load_html(
                    "<body style='padding:40px;font-family:sans-serif;color:#c0392b'>"
                    "<h2>Server failed to start</h2>"
                    "<p>Check _server.log in the Daymark folder.</p></body>"
                )

        icon = ICON_PATH if os.path.exists(ICON_PATH) else None
        print("Starting webview...")
        webview.start(on_ready, icon=icon, debug=False)
        print("webview exited")

    except Exception as e:
        print(f"CRASH: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
