"""Native, masked Windows dialog for storing integration secrets."""

import tkinter as tk
from tkinter import messagebox

import portfolio_credentials


FIELDS = (
    ("supabase_anon_key", "Supabase public anon key"),
    ("supabase_ingest_token", "Daymark ingest token"),
    ("github_token", "GitHub fine-grained token"),
)


def main():
    root = tk.Tk()
    root.title("Daymark · Private Connections")
    root.resizable(False, False)
    root.configure(bg="#f7f6f1")
    width, height = 520, 330
    root.update_idletasks()
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.attributes("-topmost", True)

    card = tk.Frame(root, bg="#fffefa", padx=22, pady=18)
    card.pack(fill="both", expand=True, padx=12, pady=12)
    tk.Label(card, text="PRIVATE CONNECTIONS", bg="#fffefa", fg="#3d6b52",
             font=("Segoe UI", 9, "bold")).pack(anchor="w")
    tk.Label(card, text="Credentials go straight to Windows Credential Manager.",
             bg="#fffefa", fg="#4a5549", font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 12))
    entries = {}
    saved = []
    for key, label in FIELDS:
        row = tk.Frame(card, bg="#fffefa")
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label, bg="#fffefa", fg="#657064", width=25,
                 anchor="w", font=("Segoe UI", 9)).pack(side="left")
        entry = tk.Entry(row, show="•", width=35, font=("Segoe UI", 10), relief="solid", bd=1)
        entry.pack(side="left", fill="x", expand=True)
        entries[key] = entry
        try:
            if portfolio_credentials.get_secret(key):
                saved.append(label)
        except Exception:
            pass
    tk.Label(card, text="Leave a field blank to keep its saved value.", bg="#fffefa",
             fg="#8a8c83", font=("Segoe UI", 8)).pack(anchor="w", pady=(5, 12))
    status = tk.Label(card, text="", bg="#fffefa", fg="#3d6b52", font=("Segoe UI", 9))
    status.pack(anchor="w")

    def save():
        try:
            for key, entry in entries.items():
                value = entry.get()
                if value.strip():
                    portfolio_credentials.set_secret(key, value)
                entry.delete(0, tk.END)
            status.configure(text="Saved securely. Values are no longer shown.")
        except Exception as exc:
            messagebox.showerror("Could not save credentials", f"Windows Credential Manager reported {type(exc).__name__}.", parent=root)

    buttons = tk.Frame(card, bg="#fffefa")
    buttons.pack(fill="x", side="bottom", pady=(12, 0))
    tk.Button(buttons, text="Save securely", command=save, relief="flat", cursor="hand2",
              bg="#3d6b52", fg="white", activebackground="#315941",
              font=("Segoe UI", 9, "bold"), padx=14, pady=7).pack(side="right")
    tk.Button(buttons, text="Close", command=root.destroy, relief="flat", cursor="hand2",
              bg="#e8e9e2", fg="#34423a", font=("Segoe UI", 9), padx=14, pady=7).pack(side="right", padx=8)
    if saved:
        status.configure(text="Already saved: " + ", ".join(saved))
    root.mainloop()


if __name__ == "__main__":
    main()
