"""The command box: a small always-on-top window the global hotkey shows from any app.

It runs messages through the same pipeline and permission gate as the main window (the App does
that); this module is just the window and when it gets out of the way.
"""
import os

from . import mac
from .transcript import Transcript

WIDTH = 640
MAX_OUTPUT_PX = 320             # about 12 lines of text, then it scrolls


def hide_after(result, plan):
    """After an action run from the box: hide it if doing the thing was the whole point, keep it
    open when there's something to read (an answer like the time, found files, output, an error)."""
    return bool(result.ok and not plan.answer and not result.items and not result.output)


class CommandBox:
    def __init__(self, root, on_submit, on_approve, on_escape, on_open_window, approve_events, hint):
        import tkinter as tk
        self.root = root
        self.on_submit = on_submit
        self.prev_pid = None                # the app that was in front before we showed up
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title("")
        try:                                # a thin title bar (needed to take the keyboard), no buttons
            w.attributes("-stylemask", ("titled",))
            w.attributes("-buttons", ())
        except tk.TclError:
            pass
        w.attributes("-topmost", True)
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.hide)

        self.entry = tk.Entry(w, font=("Helvetica", 18), relief="flat", highlightthickness=0, bd=6)
        self.entry.pack(fill="x", padx=10, pady=(8, 2))
        row = tk.Frame(w)
        row.pack(fill="x", padx=12)
        self.status = tk.Label(row, text=hint, fg="#888888", font=("Helvetica", 10), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        more = tk.Label(row, text="Open in window ⌘O", fg="#1f4e8c", font=("Helvetica", 10), cursor="pointinghand")
        more.pack(side="right")
        more.bind("<Button-1>", lambda e: on_open_window())
        self.out_frame = tk.Frame(w, width=WIDTH, height=1)
        self.out_frame.pack_propagate(False)
        self.out = tk.Text(self.out_frame, wrap="word", state="disabled", font=("Helvetica", 12), padx=8,
                           pady=4, relief="flat", highlightthickness=0)
        self.out.pack(fill="both", expand=True)
        self.out_frame.pack(fill="x", padx=6, pady=(2, 8))
        self.view = Transcript(self.out, on_change=self._schedule_fit)
        self._fit_pending = False

        self.entry.bind("<Return>", self._submit)
        for seq in approve_events:          # must beat the entry's own <Return> binding
            self.entry.bind(seq, on_approve)
            w.bind(seq, on_approve)
        w.bind("<Escape>", on_escape)
        w.bind("<Command-o>", lambda e: on_open_window())
        self.fit()

    # -- showing and hiding
    def visible(self):
        return self.win.winfo_viewable()

    def show(self):
        front = mac.frontmost_pid()
        if front != os.getpid():
            self.prev_pid = front
        w = self.win
        x = (w.winfo_screenwidth() - WIDTH) // 2
        y = int(w.winfo_screenheight() * 0.2)
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        w.lift()
        mac.activate()
        w.focus_force()
        self.entry.focus_force()
        self.entry.select_range(0, "end")

    def hide(self, after_action=False):
        """Hide and give the keyboard back to the app you were in. After an action (e.g. "open
        Safari") that app may now be in front instead, so only step in if we still are."""
        prev = self.prev_pid
        if prev and not after_action:
            mac.activate(prev)          # first: once the box is gone, macOS gives the front to the next window
        self.win.withdraw()
        if prev and after_action:
            self.root.after(400, lambda: self._give_back(prev))

    def _give_back(self, prev):
        if not self.visible() and mac.frontmost_pid() == os.getpid():    # not if you've reopened it
            mac.activate(prev)

    def toggle(self):
        if self.visible():
            self.hide()
        else:
            self.show()

    def set_status(self, text):
        self.status.config(text=text)

    # -- input and size
    def _submit(self, event):
        text = self.entry.get().strip()
        if text and self.on_submit(text):
            self.entry.delete(0, "end")
        return "break"

    def _schedule_fit(self):
        if not self._fit_pending:
            self._fit_pending = True
            self.win.after_idle(self.fit)

    def fit(self):
        """Grow the output area with its content (hidden when empty), up to MAX_OUTPUT_PX."""
        self._fit_pending = False
        empty = not self.out.get("1.0", "end").strip() and not self.out.window_names()
        if empty:
            px = 1
        else:
            self.out.update_idletasks()
            lines = self.out.count("1.0", "end", "update", "ypixels")   # update: measure wrapped lines now
            px = min(MAX_OUTPUT_PX, (lines[0] if isinstance(lines, tuple) else lines or 0) + 12)
        self.out_frame.configure(height=px)
        if self.visible():
            self.win.geometry("")           # let the window follow its content's size
