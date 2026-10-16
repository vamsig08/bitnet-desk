"""Tkinter desktop UI."""
import queue
import threading
import time
from pathlib import Path

from .. import config as cfg
from .. import llm, server


def run_app():
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    class App:
        def __init__(self, root):
            self.root = root
            self.q = queue.Queue()
            self.history = []
            self.attachment = None
            self.server_proc = None
            self.stop_event = threading.Event()
            self.busy = False
            self.reply = ""

            root.title("BitNet Desk — private, offline")
            root.geometry("820x640")
            root.minsize(520, 400)

            self.chat = scrolledtext.ScrolledText(root, wrap="word", state="disabled",
                                                  font=("Helvetica", 12), padx=10, pady=8)
            self.chat.pack(fill="both", expand=True, padx=8, pady=(8, 4))
            self.chat.tag_config("user", foreground="#1f4e8c", font=("Helvetica", 12, "bold"))
            self.chat.tag_config("bot", foreground="#2d6a2d", font=("Helvetica", 12, "bold"))
            self.chat.tag_config("note", foreground="#888888", font=("Helvetica", 10, "italic"))

            self.attach_lbl = tk.Label(root, text="", fg="#555", anchor="w")
            self.attach_lbl.pack(fill="x", padx=10)

            bottom = tk.Frame(root)
            bottom.pack(fill="x", padx=8, pady=(0, 4))
            self.entry = tk.Text(bottom, height=3, wrap="word", font=("Helvetica", 12))
            self.entry.pack(side="left", fill="x", expand=True)
            self.entry.bind("<Return>", self.on_enter)
            btns = tk.Frame(bottom)
            btns.pack(side="right", padx=(6, 0))
            self.send_btn = tk.Button(btns, text="Send", width=8, command=self.send, state="disabled")
            self.send_btn.pack(fill="x")
            tk.Button(btns, text="Attach…", width=8, command=self.attach).pack(fill="x")
            tk.Button(btns, text="Clear", width=8, command=self.clear).pack(fill="x")
            tk.Button(btns, text="Save…", width=8, command=self.save).pack(fill="x")

            self.status = tk.Label(root, text="Starting local model…", anchor="w", fg="#666")
            self.status.pack(fill="x", padx=10, pady=(0, 6))

            root.protocol("WM_DELETE_WINDOW", self.on_close)
            threading.Thread(target=self.boot, daemon=True).start()
            self.root.after(30, self.poll)
            self.entry.focus_set()

        # -- server lifecycle
        def boot(self):
            try:
                if not server.server_up():
                    self.server_proc = server.start_server(cfg.SERVER_LOG)
                    t0 = time.time()
                    while not server.server_up():
                        if self.server_proc.poll() is not None:
                            raise RuntimeError("Model server exited — see server.log")
                        if time.time() - t0 > 180:
                            raise RuntimeError("Model server took too long to start — see server.log")
                        time.sleep(0.5)
                self.q.put(("ready", None))
            except Exception as e:
                self.q.put(("fatal", str(e)))

        def on_close(self):
            self.stop_event.set()
            if self.server_proc and self.server_proc.poll() is None:
                self.server_proc.terminate()
            self.root.destroy()

        # -- transcript helpers
        def write(self, text, tag=None):
            self.chat.configure(state="normal")
            self.chat.insert("end", text, tag)
            self.chat.configure(state="disabled")
            self.chat.see("end")

        # -- actions
        def on_enter(self, event):
            if event.state & 0x1:          # Shift+Enter → newline
                return None
            self.send()
            return "break"

        def send(self):
            if self.busy:                  # button doubles as Stop
                self.stop_event.set()
                return
            msg = self.entry.get("1.0", "end").strip()
            if not msg or self.send_btn["state"] == "disabled":
                return
            self.entry.delete("1.0", "end")
            shown = msg
            if self.attachment:
                name, text, _ = self.attachment
                msg = f'Here is the content of the file "{name}":\n"""\n{text}\n"""\n\n{msg}'
                shown = f"[📎 {name}] {shown}"
                self.attachment = None
                self.attach_lbl.config(text="")
            self.history.append(("user", msg))
            self.write("You: ", "user")
            self.write(shown + "\n\n")
            self.write("Assistant: ", "bot")

            self.history, prompt = llm.prepare(self.history)
            self.busy, self.reply = True, ""
            self.stop_event.clear()
            self.send_btn.config(text="Stop")
            self.status.config(text="Thinking…")
            threading.Thread(target=self.generate, args=(prompt,), daemon=True).start()

        def generate(self, prompt):
            try:
                t = llm.stream_completion(prompt, lambda tok: self.q.put(("tok", tok)), self.stop_event)
                self.q.put(("done", t))
            except Exception as e:
                self.q.put(("err", str(e)))

        def attach(self):
            path = filedialog.askopenfilename(
                title="Attach a text file",
                filetypes=[("Text-like files", "*.txt *.md *.py *.js *.ts *.json *.csv *.html *.css "
                            "*.yaml *.yml *.toml *.ini *.log *.sh *.c *.cpp *.h *.java *.go *.rs"),
                           ("All files", "*.*")])
            if not path:
                return
            try:
                self.attachment = llm.read_text_file(path)
            except Exception as e:
                messagebox.showerror("Attach", f"Couldn't read that file:\n{e}")
                return
            name, _, cut = self.attachment
            self.attach_lbl.config(text=f"📎 {name} will be sent with your next message"
                                        + (f" (first {cfg.MAX_FILE_CHARS} characters)" if cut else ""))

        def clear(self):
            if self.busy:
                return
            self.history.clear()
            self.chat.configure(state="normal")
            self.chat.delete("1.0", "end")
            self.chat.configure(state="disabled")

        def save(self):
            path = filedialog.asksaveasfilename(defaultextension=".md",
                                                filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
            if not path:
                return
            lines = [f"**{'You' if r == 'user' else 'Assistant'}:** {t}\n" for r, t in self.history]
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self.status.config(text=f"Saved to {path}")

        # -- event loop bridge
        def poll(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == "ready":
                        self.send_btn.config(state="normal")
                        self.status.config(text=f"Ready · BitNet b1.58 2B4T · CPU · {cfg.THREADS} threads · "
                                                f"localhost only")
                        self.write("Model loaded locally. Nothing you type leaves this computer.\n\n", "note")
                        for err in cfg.CONFIG_ERRORS:
                            self.write(f"Config: {err}\n\n", "note")
                    elif kind == "fatal":
                        self.status.config(text="Model failed to start")
                        messagebox.showerror("BitNet Desk", val)
                    elif kind == "tok":
                        self.reply += val
                        self.write(val)
                    elif kind in ("done", "err"):
                        self.finish(kind, val)
            except queue.Empty:
                pass
            self.root.after(30, self.poll)

        def finish(self, kind, val):
            self.busy = False
            self.send_btn.config(text="Send")
            reply = self.reply.strip()
            if kind == "err":
                self.write(f"\n[error: {val}]", "note")
                self.status.config(text="Error talking to the local model")
            else:
                if reply:
                    self.history.append(("assistant", reply))
                if self.stop_event.is_set():
                    self.write(" [stopped]", "note")
                tps = (val or {}).get("predicted_per_second")
                self.status.config(text=f"Ready · {tps:.1f} tokens/s" if tps else "Ready")
            self.write("\n\n")

    root = tk.Tk()
    App(root)
    root.mainloop()
