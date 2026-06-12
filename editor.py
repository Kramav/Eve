"""
Eve — Editor
Run:  python editor.py
Changes save instantly. No restart needed.
"""
import json
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

COMMANDS_FILE = Path(__file__).parent / "custom_commands.json"
APPS_FILE     = Path(__file__).parent / "apps.json"
ALIASES_FILE  = Path(__file__).parent / "aliases.json"

# Must match BUILTIN_MAP keys in core/dispatcher.py
BUILTINS = [
    ("get_time",         "Tell me the time"),
    ("get_date",         "Tell me the date"),
    ("volume_up",        "Volume up"),
    ("volume_down",      "Volume down"),
    ("toggle_mute",      "Mute / unmute"),
    ("play_pause",       "Play / pause media"),
    ("next_track",       "Next track"),
    ("prev_track",       "Previous track"),
    ("screenshot",       "Take a screenshot"),
    ("list_reminders",   "List my reminders"),
    ("cancel_reminders", "Cancel all reminders"),
    ("open_editor",      "Open command editor"),
    ("sleep",            "Put PC to sleep"),
    ("shutdown",         "Shut down PC"),
    ("cancel_shutdown",  "Cancel shutdown"),
]
_KEY_TO_LABEL = {k: l for k, l in BUILTINS}
_LABEL_TO_KEY = {l: k for k, l in BUILTINS}

BG      = "#1a1a2e"
ROW_ODD = "#16213e"
ROW_EVN = "#0f3460"
FG      = "#e0e0e0"
MUTED   = "#888899"
ACCENT  = "#7289da"
GREEN   = "#43b581"
RED     = "#f04747"

BUILT_IN = [
    ("open [app]",                         "Launch any app in your Apps list"),
    ("close [app]",                        "Close a running app"),
    ("search for [query]",                 "DuckDuckGo search in browser"),
    ("what time is it",                    "Speak the current time"),
    ("what's the date / what day is it",   "Speak today's date"),
    ("remind me in [N] minutes to [task]", "Set a timed reminder"),
    ("set timer for [N] minutes",          "Set a countdown timer"),
    ("what are my reminders",              "List pending reminders"),
    ("cancel all reminders",               "Clear all reminders"),
    ("volume up / volume down",            "Adjust system volume"),
    ("mute / unmute",                      "Toggle mute"),
    ("pause / play / resume",              "Media play/pause"),
    ("next song / previous song",          "Skip tracks"),
    ("take a screenshot",                  "Save PNG to Desktop"),
    ("open command editor",                "Open this editor"),
    #("shut down",                          "Schedule shutdown (30s delay)"),
    #("cancel shutdown",                    "Cancel pending shutdown"),
    #("go to sleep",                        "Put PC to sleep"),
]


# ── Storage ────────────────────────────────────────────────────────────────────

def _load(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return []
    return []

def _save(path: Path, data: list):
    path.write_text(json.dumps(data, indent=2))


# ── Reusable two-column table with Add / Edit / Delete ─────────────────────────

class _TableTab(tk.Frame):
    def __init__(self, parent, path, col1, col2, dlabel1, dlabel2, dhint=""):
        super().__init__(parent, bg=BG)
        self._path   = path
        self._label1 = dlabel1
        self._label2 = dlabel2
        self._dhint  = dhint

        tbl = tk.Frame(self, bg=BG)
        tbl.pack(fill="both", expand=True, padx=20, pady=(10, 0))

        self._tree = ttk.Treeview(tbl, columns=("a", "b"), show="headings",
                                  style="Custom.Treeview", selectmode="browse")
        self._tree.heading("a", text=col1)
        self._tree.heading("b", text=col2)
        self._tree.column("a", width=300, minwidth=100, stretch=True)
        self._tree.column("b", width=460, minwidth=100, stretch=True)
        self._tree.tag_configure("odd",  background=ROW_ODD)
        self._tree.tag_configure("even", background=ROW_EVN)
        self._tree.bind("<Double-1>", lambda _: self._edit())

        sb = ttk.Scrollbar(tbl, orient="vertical", command=self._tree.yview,
                           style="Vertical.TScrollbar")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=20, pady=8)
        _btn(btns, "+ Add",     self._add,    ACCENT).pack(side="left", padx=(0, 6))
        _btn(btns, "✎  Edit",   self._edit,   GREEN ).pack(side="left", padx=(0, 6))
        _btn(btns, "✕  Delete", self._delete, RED   ).pack(side="left")

        self.refresh()

    def refresh(self):
        for row in self._tree.get_children():
            self._tree.delete(row)
        data = _load(self._path)
        for i, (a, b) in enumerate(data):
            self._tree.insert("", "end", iid=str(i), values=(a, b),
                              tags=("odd" if i % 2 == 0 else "even",))
        if not self._tree.get_children():
            self._tree.insert("", "end", iid="empty",
                              values=('Nothing here yet — click  "+ Add"  to create one', ""),
                              tags=("odd",))

    def _add(self):
        dlg = _Dialog(self.winfo_toplevel(), "Add",
                      self._label1, self._label2, hint=self._dhint)
        if dlg.result:
            data = _load(self._path)
            data.append(list(dlg.result))
            _save(self._path, data)
            self.refresh()

    def _edit(self):
        sel = self._tree.selection()
        if not sel or sel[0] == "empty":
            return
        idx  = int(sel[0])
        data = _load(self._path)
        a, b = data[idx]
        dlg = _Dialog(self.winfo_toplevel(), "Edit",
                      self._label1, self._label2, val1=a, val2=b, hint=self._dhint)
        if dlg.result:
            data[idx] = list(dlg.result)
            _save(self._path, data)
            self.refresh()

    def _delete(self):
        sel = self._tree.selection()
        if not sel or sel[0] == "empty":
            return
        idx  = int(sel[0])
        data = _load(self._path)
        if messagebox.askyesno("Delete", f'Delete  "{data[idx][0]}" ?',
                               parent=self.winfo_toplevel()):
            data.pop(idx)
            _save(self._path, data)
            self.refresh()


# ── Alias tab (phrase → built-in dropdown) ────────────────────────────────────

class _AliasTab(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)

        tbl = tk.Frame(self, bg=BG)
        tbl.pack(fill="both", expand=True, padx=20, pady=(10, 0))

        self._tree = ttk.Treeview(tbl, columns=("phrase", "action"), show="headings",
                                  style="Custom.Treeview", selectmode="browse")
        self._tree.heading("phrase", text="When I say…")
        self._tree.heading("action", text="Runs this built-in")
        self._tree.column("phrase", width=300, minwidth=100, stretch=True)
        self._tree.column("action", width=460, minwidth=100, stretch=True)
        self._tree.tag_configure("odd",  background=ROW_ODD)
        self._tree.tag_configure("even", background=ROW_EVN)
        self._tree.bind("<Double-1>", lambda _: self._edit())

        sb = ttk.Scrollbar(tbl, orient="vertical", command=self._tree.yview,
                           style="Vertical.TScrollbar")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=20, pady=8)
        _btn(btns, "+ Add",     self._add,    ACCENT).pack(side="left", padx=(0, 6))
        _btn(btns, "✎  Edit",   self._edit,   GREEN ).pack(side="left", padx=(0, 6))
        _btn(btns, "✕  Delete", self._delete, RED   ).pack(side="left")

        self.refresh()

    def refresh(self):
        for row in self._tree.get_children():
            self._tree.delete(row)
        for i, (phrase, key) in enumerate(_load(ALIASES_FILE)):
            label = _KEY_TO_LABEL.get(key, key)
            self._tree.insert("", "end", iid=str(i), values=(phrase, label),
                              tags=("odd" if i % 2 == 0 else "even",))
        if not self._tree.get_children():
            self._tree.insert("", "end", iid="empty",
                              values=('Nothing here yet — click  "+ Add"  to create one', ""),
                              tags=("odd",))

    def _add(self):
        dlg = _AliasDialog(self.winfo_toplevel())
        if dlg.result:
            data = _load(ALIASES_FILE)
            data.append(list(dlg.result))
            _save(ALIASES_FILE, data)
            self.refresh()

    def _edit(self):
        sel = self._tree.selection()
        if not sel or sel[0] == "empty":
            return
        idx  = int(sel[0])
        data = _load(ALIASES_FILE)
        phrase, key = data[idx]
        dlg = _AliasDialog(self.winfo_toplevel(), phrase=phrase, key=key)
        if dlg.result:
            data[idx] = list(dlg.result)
            _save(ALIASES_FILE, data)
            self.refresh()

    def _delete(self):
        sel = self._tree.selection()
        if not sel or sel[0] == "empty":
            return
        idx  = int(sel[0])
        data = _load(ALIASES_FILE)
        if messagebox.askyesno("Delete", f'Delete  "{data[idx][0]}" ?',
                               parent=self.winfo_toplevel()):
            data.pop(idx)
            _save(ALIASES_FILE, data)
            self.refresh()


class _AliasDialog(tk.Toplevel):
    def __init__(self, parent, phrase="", key=""):
        super().__init__(parent)
        self.title("Add Alias" if not phrase else "Edit Alias")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry("520x230")
        self.transient(parent)
        self.grab_set()
        self.result = None

        lkw = dict(font=("Segoe UI", 9), fg=MUTED, bg=BG, anchor="w")
        ekw = dict(font=("Segoe UI", 11), bg="#0f3460", fg="white",
                   insertbackground="white", relief="flat",
                   highlightthickness=1, highlightbackground=ACCENT,
                   highlightcolor=ACCENT)

        tk.Label(self, text="When I say:", **lkw).pack(fill="x", padx=24, pady=(18, 2))
        self._phrase = tk.Entry(self, **ekw)
        self._phrase.insert(0, phrase)
        self._phrase.pack(fill="x", padx=24, ipady=5)

        tk.Label(self, text="Runs this built-in:", **lkw).pack(fill="x", padx=24, pady=(12, 2))
        labels = [l for _, l in BUILTINS]
        self._combo = ttk.Combobox(self, values=labels, state="readonly",
                                   font=("Segoe UI", 11))
        self._combo.set(_KEY_TO_LABEL.get(key, labels[0]))
        self._combo.pack(fill="x", padx=24, ipady=3)

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=24, pady=(16, 0))
        _btn(row, "Save",   self._save,   ACCENT   ).pack(side="right")
        _btn(row, "Cancel", self.destroy, "#333355" ).pack(side="right", padx=(0, 8))

        self._phrase.focus_set()
        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self.destroy())
        self.wait_window()

    def _save(self):
        phrase = self._phrase.get().strip()
        key    = _LABEL_TO_KEY.get(self._combo.get())
        if phrase and key:
            self.result = (phrase, key)
            self.destroy()


# ── Main window ────────────────────────────────────────────────────────────────

class CommandEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Eve — Editor")
        self.configure(bg=BG)
        self.geometry("880x640")
        self.minsize(640, 460)
        self._styles()
        self._build()

    def _styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Custom.Treeview",
                    background=ROW_ODD, foreground=FG, rowheight=30,
                    fieldbackground=ROW_ODD, font=("Segoe UI", 10), borderwidth=0)
        s.configure("Custom.Treeview.Heading",
                    background="#0d1b2a", foreground=ACCENT,
                    font=("Segoe UI", 9, "bold"), relief="flat", padding=(8, 6))
        s.map("Custom.Treeview",
              background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.configure("Ref.Treeview",
                    background="#111122", foreground=MUTED, rowheight=24,
                    fieldbackground="#111122", font=("Segoe UI", 9), borderwidth=0)
        s.configure("Ref.Treeview.Heading",
                    background="#0d1b2a", foreground="#555577",
                    font=("Segoe UI", 8, "bold"), relief="flat", padding=(8, 4))
        s.map("Ref.Treeview", background=[("selected", "#111122")])
        s.configure("Eve.TNotebook", background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
        s.configure("Eve.TNotebook.Tab", background="#0d1b2a", foreground=MUTED,
                    font=("Segoe UI", 10), padding=(16, 8))
        s.map("Eve.TNotebook.Tab",
              background=[("selected", BG)], foreground=[("selected", ACCENT)])
        s.configure("Vertical.TScrollbar", background="#0d1b2a",
                    troughcolor=BG, arrowcolor=MUTED, borderwidth=0)
        s.configure("TCombobox", fieldbackground="#0f3460", background="#0d1b2a",
                    foreground="white", selectbackground=ACCENT, arrowcolor=MUTED)
        s.map("TCombobox", fieldbackground=[("readonly", "#0f3460")],
              foreground=[("readonly", "white")])

    def _build(self):
        tk.Label(self, text="Eve — Editor",
                 font=("Segoe UI", 15, "bold"), fg=ACCENT, bg=BG
                 ).pack(anchor="w", padx=24, pady=(18, 2))
        tk.Label(self, text="Changes save instantly. No restart needed.",
                 font=("Segoe UI", 9), fg=MUTED, bg=BG
                 ).pack(anchor="w", padx=24, pady=(0, 10))

        nb = ttk.Notebook(self, style="Eve.TNotebook")
        nb.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # ── Tab 1: Voice Commands ──────────────────────────────────────────────
        t1 = tk.Frame(nb, bg=BG)
        nb.add(t1, text="  Voice Commands  ")

        _TableTab(
            t1,
            path    = COMMANDS_FILE,
            col1    = "When I say…",
            col2    = "Eve runs this",
            dlabel1 = "When I say:",
            dlabel2 = "Run this  (app name, path, or shell command):",
            dhint   = 'Examples:  "spotify"    "notepad"    "code C:\\Projects\\Mine"',
        ).pack(fill="both", expand=True)

        tk.Frame(t1, bg="#2a2a4a", height=1).pack(fill="x", padx=20, pady=(4, 0))
        tk.Label(t1, text="BUILT-IN COMMANDS  —  always available",
                 font=("Segoe UI", 8, "bold"), fg="#555577", bg=BG, anchor="w"
                 ).pack(fill="x", padx=20, pady=(8, 4))

        ref_frame = tk.Frame(t1, bg=BG)
        ref_frame.pack(fill="both", padx=20, pady=(0, 12))
        ref = ttk.Treeview(ref_frame, columns=("p", "d"), show="headings",
                           style="Ref.Treeview", selectmode="none", height=6)
        ref.heading("p", text="Phrase")
        ref.heading("d", text="What it does")
        ref.column("p", width=300, stretch=True)
        ref.column("d", width=460, stretch=True)
        ref_sb = ttk.Scrollbar(ref_frame, orient="vertical", command=ref.yview,
                               style="Vertical.TScrollbar")
        ref.configure(yscrollcommand=ref_sb.set)
        ref.pack(side="left", fill="both", expand=True)
        ref_sb.pack(side="right", fill="y")
        for phrase, desc in BUILT_IN:
            ref.insert("", "end", values=(phrase, desc))

        # ── Tab 2: Apps ────────────────────────────────────────────────────────
        t2 = tk.Frame(nb, bg=BG)
        nb.add(t2, text="  Apps  ")

        tk.Label(t2, text='Say  "open [name]"  to launch any app in this list.',
                 font=("Segoe UI", 9), fg=MUTED, bg=BG, anchor="w"
                 ).pack(fill="x", padx=22, pady=(10, 0))

        _TableTab(
            t2,
            path    = APPS_FILE,
            col1    = "App name  (say this)",
            col2    = "Opens this",
            dlabel1 = "App name  (what you say):",
            dlabel2 = "Executable, path, or URI:",
            dhint   = 'Examples:  "spotify"    "C:\\Program Files\\App\\app.exe"    "ms-settings:"',
        ).pack(fill="both", expand=True)

        tk.Label(t2,
                 text="Tip: right-click any desktop shortcut → Properties to find the full path.",
                 font=("Segoe UI", 8), fg="#555577", bg=BG, anchor="w"
                 ).pack(fill="x", padx=22, pady=(0, 12))

        # ── Tab 3: Aliases ─────────────────────────────────────────────────────
        t3 = tk.Frame(nb, bg=BG)
        nb.add(t3, text="  Aliases  ")

        tk.Label(t3,
                 text="Add your own phrase for any built-in command. Pick the action from the dropdown.",
                 font=("Segoe UI", 9), fg=MUTED, bg=BG, anchor="w"
                 ).pack(fill="x", padx=22, pady=(10, 0))

        _AliasTab(t3).pack(fill="both", expand=True)


# ── Add / Edit dialog ─────────────────────────────────────────────────────────

class _Dialog(tk.Toplevel):
    def __init__(self, parent, title, label1, label2, val1="", val2="", hint=""):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry("520x250")
        self.transient(parent)
        self.grab_set()
        self.result = None

        lkw = dict(font=("Segoe UI", 9), fg=MUTED, bg=BG, anchor="w")
        ekw = dict(font=("Segoe UI", 11), bg="#0f3460", fg="white",
                   insertbackground="white", relief="flat",
                   highlightthickness=1, highlightbackground=ACCENT,
                   highlightcolor=ACCENT)

        tk.Label(self, text=label1, **lkw).pack(fill="x", padx=24, pady=(18, 2))
        self._e1 = tk.Entry(self, **ekw)
        self._e1.insert(0, val1)
        self._e1.pack(fill="x", padx=24, ipady=5)

        tk.Label(self, text=label2, **lkw).pack(fill="x", padx=24, pady=(12, 2))
        self._e2 = tk.Entry(self, **ekw)
        self._e2.insert(0, val2)
        self._e2.pack(fill="x", padx=24, ipady=5)

        if hint:
            tk.Label(self, text=hint, font=("Segoe UI", 8),
                     fg="#555577", bg=BG).pack(anchor="w", padx=25, pady=(4, 0))

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=24, pady=(14, 0))
        _btn(row, "Save",   self._save,   ACCENT   ).pack(side="right")
        _btn(row, "Cancel", self.destroy, "#333355" ).pack(side="right", padx=(0, 8))

        self._e1.focus_set()
        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self.destroy())
        self.wait_window()

    def _save(self):
        a, b = self._e1.get().strip(), self._e2.get().strip()
        if a and b:
            self.result = (a, b)
            self.destroy()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _btn(parent, label, cmd, color):
    return tk.Button(parent, text=label, command=cmd, bg=color, fg="white",
                     font=("Segoe UI", 9, "bold"), relief="flat",
                     padx=14, pady=6, cursor="hand2",
                     activebackground=color, activeforeground="white", bd=0)


if __name__ == "__main__":
    CommandEditor().mainloop()
