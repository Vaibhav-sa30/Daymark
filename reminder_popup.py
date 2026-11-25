"""
Daymark reminder popup — always-on-top centered alert.
Spawned by daemon.py for meetings, agenda, idle reminders, etc.
"""

import sys
import tkinter as tk
import ctypes
import json
import urllib.request


def show_reminder(title: str, start_time: str, message: str = "", mode: str = "event") -> None:
    root = tk.Tk()
    root.withdraw()   # hide until fully built to avoid flicker

    root.title("Daymark · Anything Updates?" if mode == "portfolio_checkin" else "Daymark")
    root.resizable(False, False)
    root.configure(bg="#f7f6f1")
    root.attributes("-topmost", True)

    width, height = (520, 285) if mode == "portfolio_checkin" else (400, 250)
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")

    # ── Card ─────────────────────────────────────────────────────
    card = tk.Frame(root, bg="#f7f6f1", padx=22, pady=18,
                    highlightbackground="#3d6b52", highlightthickness=2)
    card.pack(fill="both", expand=True)

    eyebrow = ("DAYMARK  ·  PORTFOLIO CHECK-IN" if mode == "portfolio_checkin"
               else "DAYMARK  ·  AGENDA" if mode == "agenda" else "DAYMARK  ·  REMINDER")
    tk.Label(card, text=eyebrow, bg="#f7f6f1", fg="#3d6b52",
             font=("Segoe UI", 8, "bold")).pack(anchor="w")

    tk.Label(card, text=title, bg="#f7f6f1", fg="#1e2e26",
             font=("Segoe UI", 16, "bold"), wraplength=460 if mode == "portfolio_checkin" else 350,
             justify="left").pack(fill="x", anchor="w", pady=(8, 2))

    body = message or f"Your {start_time} block is starting."
    body_label = tk.Label(card, text=body, bg="#f7f6f1", fg="#4a5549",
                          font=("Segoe UI", 10), wraplength=460 if mode == "portfolio_checkin" else 350,
                          justify="left")
    body_label.pack(fill="x", anchor="w", pady=(6, 0))

    # ── Buttons ───────────────────────────────────────────────────
    btn_row = tk.Frame(card, bg="#f7f6f1")
    btn_row.pack(fill="x", side="bottom", pady=(14, 0))

    sound_on = {"v": True, "count": 0}

    def stop_sound():
        sound_on["v"] = False

    def dismiss():
        root.destroy()

    tk.Button(btn_row, text="Stop sound", command=stop_sound,
              relief="flat", cursor="hand2",
              bg="#e8e9e2", fg="#34423a", activebackground="#dde0d8",
              font=("Segoe UI", 9), padx=10, pady=5).pack(side="left")

    if mode == "portfolio_checkin":
        def answer_checkin(response):
            try:
                payload = json.dumps({"response": response, "source": "daymark_popup"}).encode("utf-8")
                request = urllib.request.Request(
                    "http://127.0.0.1:4173/api/portfolio/checkin",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as result:
                    if result.status < 200 or result.status >= 300:
                        raise RuntimeError(f"Daymark returned HTTP {result.status}")
                dismiss()
            except Exception:
                body_label.configure(text="I couldn’t save that check-in yet. Keep this window open and try again.", fg="#a23b32")

        tk.Button(btn_row, text="Yes, I have an update",
                  command=lambda: answer_checkin("has_update"), relief="flat", cursor="hand2",
                  bg="#3d6b52", fg="white", activebackground="#315941",
                  font=("Segoe UI", 9, "bold"), padx=12, pady=7).pack(side="right", padx=(7, 0))
        tk.Button(btn_row, text="Nothing to update",
                  command=lambda: answer_checkin("nothing_to_update"), relief="flat", cursor="hand2",
                  bg="#e8e9e2", fg="#34423a", activebackground="#dde0d8",
                  font=("Segoe UI", 9), padx=10, pady=7).pack(side="right", padx=(7, 0))
        tk.Button(btn_row, text="Remind me later",
                  command=lambda: answer_checkin("remind_later"), relief="flat", cursor="hand2",
                  bg="#e8e9e2", fg="#34423a", activebackground="#dde0d8",
                  font=("Segoe UI", 9), padx=10, pady=7).pack(side="right")
    else:
        tk.Button(btn_row, text="Got it", command=dismiss,
                  relief="flat", cursor="hand2",
                  bg="#3d6b52", fg="white", activebackground="#315941",
                  font=("Segoe UI", 9, "bold"), padx=16, pady=5).pack(side="right")

        tk.Button(btn_row, text="Dismiss", command=dismiss,
                  relief="flat", cursor="hand2",
                  bg="#e8e9e2", fg="#34423a", activebackground="#dde0d8",
                  font=("Segoe UI", 9), padx=12, pady=5).pack(side="right", padx=(0, 8))

    # ── Force to front ────────────────────────────────────────────
    def force_front():
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "Daymark")
            if hwnd:
                # Flash taskbar to grab attention
                fi = ctypes.c_uint(3)   # FLASHW_ALL
                ctypes.windll.user32.FlashWindow(hwnd, True)
                ctypes.windll.user32.ShowWindow(hwnd, 9)    # SW_RESTORE
                ctypes.windll.user32.SetWindowPos(hwnd, -1, x, y, width, height, 0x0040)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        root.deiconify()
        root.lift()
        root.focus_force()

    # ── Beep loop ─────────────────────────────────────────────────
    def beep_loop():
        if not sound_on["v"] or sound_on["count"] >= 6 or not root.winfo_exists():
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        sound_on["count"] += 1
        root.after(2000, beep_loop)

    root.bind("<Escape>", lambda _: dismiss())
    root.protocol("WM_DELETE_WINDOW", dismiss)

    # Show window and bring to front after a short delay
    root.after(100, force_front)
    root.after(300, beep_loop)

    root.mainloop()


if __name__ == "__main__":
    show_reminder(
        sys.argv[1] if len(sys.argv) > 1 else "Scheduled block",
        sys.argv[2] if len(sys.argv) > 2 else "now",
        sys.argv[3] if len(sys.argv) > 3 else "",
        sys.argv[4] if len(sys.argv) > 4 else "event",
    )
