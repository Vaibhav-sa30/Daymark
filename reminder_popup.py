"""Small always-on-top desktop reminder used by the Daymark daemon."""

import sys
import tkinter as tk
import ctypes


def show_reminder(title: str, start_time: str) -> None:
    root = tk.Tk()
    root.title("Daymark reminder")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#f7f6f1")

    width, height = 340, 190
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")

    card = tk.Frame(root, bg="#f7f6f1", padx=20, pady=16,
                    highlightbackground="#3d6b52", highlightthickness=2)
    card.pack(fill="both", expand=True)

    tk.Label(card, text="DAYMARK  ·  STARTING NOW", bg="#f7f6f1", fg="#3d6b52",
             font=("Segoe UI", 9, "bold")).pack(anchor="w")
    tk.Label(card, text=title, bg="#f7f6f1", fg="#26332e",
             font=("Segoe UI", 15, "bold"), wraplength=292, justify="left",
             anchor="w").pack(fill="x", anchor="w", pady=(9, 2))
    tk.Label(card, text=f"Your {start_time} block is underway.", bg="#f7f6f1",
             fg="#70776f", font=("Segoe UI", 10)).pack(anchor="w")

    buttons = tk.Frame(card, bg="#f7f6f1")
    buttons.pack(fill="x", side="bottom", pady=(12, 0))

    def close():
        root.destroy()

    tk.Button(buttons, text="Dismiss", command=close, relief="flat", cursor="hand2",
              bg="#e8e9e2", fg="#34423a", activebackground="#dfe2d8",
              font=("Segoe UI", 9), padx=12, pady=5).pack(side="right")
    tk.Button(buttons, text="Got it", command=close, relief="flat", cursor="hand2",
              bg="#3d6b52", fg="white", activebackground="#315941",
              font=("Segoe UI", 9, "bold"), padx=14, pady=5).pack(side="right", padx=(0, 8))

    def bring_to_front():
        try:
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.focus_force()
            hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetWindowPos(hwnd, -1, x, y, width, height, 0x0040)
            ctypes.windll.user32.BringWindowToTop(hwnd)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            # Tk's topmost flag remains a fallback if foreground activation is denied.
            root.attributes("-topmost", True)
            root.lift()

    def repeat_alert():
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        if root.winfo_exists():
            root.after(2200, repeat_alert)

    root.after(250, bring_to_front)
    root.after(450, repeat_alert)
    root.mainloop()


if __name__ == "__main__":
    show_reminder(sys.argv[1] if len(sys.argv) > 1 else "Scheduled block",
                  sys.argv[2] if len(sys.argv) > 2 else "now")
