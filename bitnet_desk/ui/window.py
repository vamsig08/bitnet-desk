"""Tkinter desktop UI."""
import queue
import threading
import time
from pathlib import Path

from .. import actions
from .. import config as cfg
from .. import llm, server
from ..actions import permissions, pipeline
from . import cards


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
            self.ready = False
            self.reply = ""
            self.pending = None            # (proposal, run button, deny button, status label)

            root.title("BitNet Desk — private, offline")
            root.geometry("820x640")
            root.minsize(520, 400)

            self.chat = scrolledtext.ScrolledText(root, wrap="word", state="disabled",
                                                  font=("Helvetica", 12), padx=10, pady=8)
            self.chat.pack(fill="both", expand=True, padx=8, pady=(8, 4))
            self.chat.tag_config("user", foreground="#1f4e8c", font=("Helvetica", 12, "bold"))
            self.chat.tag_config("bot", foreground="#2d6a2d", font=("Helvetica", 12, "bold"))
            self.chat.tag_config("note", foreground="#888888", font=("Helvetica", 10, "italic"))
            self.chat.tag_config("ok", foreground="#2d6a2d")
            self.chat.tag_config("fail", foreground="#b00020")
            self.links = 0

            self.attach_lbl = tk.Label(root, text="", fg="#555", anchor="w")
            self.attach_lbl.pack(fill="x", padx=10)

            bottom = tk.Frame(root)
            bottom.pack(fill="x", padx=8, pady=(0, 4))
            self.entry = tk.Text(bottom, height=3, wrap="word", font=("Helvetica", 12))
            self.entry.pack(side="left", fill="x", expand=True)
            self.entry.bind("<Return>", self.on_enter)
            for seq in cards.APPROVE_EVENTS:       # must beat the entry's own <Return> binding
                self.entry.bind(seq, self.on_approve_key)
                root.bind(seq, self.on_approve_key)
            root.bind("<Escape>", self.on_deny_key)
            btns = tk.Frame(bottom)
            btns.pack(side="right", padx=(6, 0))
            self.send_btn = tk.Button(btns, text="Send", width=8, command=self.send)
            self.send_btn.pack(fill="x")
            tk.Button(btns, text="Attach…", width=8, command=self.attach).pack(fill="x")
            tk.Button(btns, text="Clear", width=8, command=self.clear).pack(fill="x")
            tk.Button(btns, text="Save…", width=8, command=self.save).pack(fill="x")

            self.status = tk.Label(root, text="Starting local model…", anchor="w", fg="#666")
            self.status.pack(fill="x", padx=10, pady=(0, 6))

            root.protocol("WM_DELETE_WINDOW", self.on_close)
            for err in cfg.CONFIG_ERRORS + permissions.config_problems():
                self.write(f"Config: {err}\n\n", "note")
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
            if not msg:
                return
            if self.pending:                   # a new message skips the open card
                self.resolve_card(False, "Skipped — nothing was run.")
            if not self.attachment and self.try_action(msg):
                return
            if not self.ready:
                self.write("The model is still loading — commands like “open Safari” already work.\n\n", "note")
                return
            self.entry.delete("1.0", "end")
            if not self.attachment and pipeline.wants_router(msg):
                self.write("You: ", "user")
                self.write(msg + "\n\n")
                self.busy = True
                self.status.config(text="Working out whether that's a command…")
                threading.Thread(target=self.route, args=(msg,), daemon=True).start()
                return
            self.start_chat(msg)

        def start_chat(self, msg, user_written=False):
            shown = msg
            if self.attachment and not user_written:
                name, text, _ = self.attachment
                msg = f'Here is the content of the file "{name}":\n"""\n{text}\n"""\n\n{msg}'
                shown = f"[📎 {name}] {shown}"
                self.attachment = None
                self.attach_lbl.config(text="")
            self.history.append(("user", msg))
            if not user_written:
                self.write("You: ", "user")
                self.write(shown + "\n\n")
            self.write("Assistant: ", "bot")

            self.history, prompt = llm.prepare(self.history)
            self.busy, self.reply = True, ""
            self.stop_event.clear()
            self.send_btn.config(text="Stop")
            self.status.config(text="Thinking…")
            threading.Thread(target=self.generate, args=(prompt,), daemon=True).start()

        # -- actions (fast path; the permission gate decides what runs)
        def try_action(self, msg):
            try:
                outcome = actions.handle(msg)
            except Exception as e:             # an action bug must never break chat
                self.write(f"[action error: {e}]\n\n", "note")
                return False
            if outcome.kind == "chat":
                return False
            self.entry.delete("1.0", "end")
            self.write("You: ", "user")
            self.write(msg + "\n\n")
            if outcome.kind == "ask":
                self.show_card(outcome.proposal)
            else:
                self.show_result(outcome.proposal.plan, outcome.result)
            return True

        def route(self, msg):
            """Background thread: ask the model which tool fits (only for command-verb messages)."""
            try:
                outcome = pipeline.handle_model(msg)
            except Exception:                  # router unavailable: answer in chat instead
                outcome = None
            self.q.put(("routed", (msg, outcome)))

        def on_routed(self, msg, outcome):
            self.busy = False
            if outcome is None or outcome.kind == "chat":
                self.start_chat(msg, user_written=True)
            elif outcome.kind == "ask":
                self.show_card(outcome.proposal)
            elif outcome.kind == "done":
                self.show_result(outcome.proposal.plan, outcome.result)
                self.status.config(text="Ready")
            else:                              # refused: the user named something that isn't there
                hint = f" Did you mean: {', '.join(outcome.suggestions)}?" if outcome.suggestions else ""
                self.write(f"✗ {outcome.message}{hint}\n\n", "fail")
                self.status.config(text="Ready")

        def just_chat(self):
            """Card button on model suggestions: drop the action, answer the message in chat."""
            if not self.pending:
                return
            text = self.pending[0].text
            self.resolve_card(False, "Answered in chat instead — nothing was run.")
            self.start_chat(text, user_written=True)

        def show_result(self, plan, result):
            if result.ok and plan.answer:
                self.write("Assistant: ", "bot")
                self.write(result.message + "\n\n")
                return
            self.write(cards.outcome_line(result) + "\n", "ok" if result.ok else "fail")
            if result.items:
                self.write_items(result.items)
            elif result.output:
                self.write(result.output + "\n", "note")
            self.write("\n")

        def write_items(self, paths):
            """Clickable paths; a click opens it through the permission gate like any other action."""
            from ..actions.resolve import tilde
            for path in paths:
                self.links += 1
                tag = f"link{self.links}"
                self.chat.tag_config(tag, foreground="#1f4e8c", underline=True)
                self.chat.tag_bind(tag, "<Button-1>", lambda e, p=path: self.open_item(p))
                self.chat.tag_bind(tag, "<Enter>", lambda e: self.chat.config(cursor="pointinghand"))
                self.chat.tag_bind(tag, "<Leave>", lambda e: self.chat.config(cursor=""))
                self.write("  " + tilde(path), tag)
                self.write("\n")

        def open_item(self, path):
            try:
                plan = actions.TOOLS["open_path"].make_plan({"path": path})
            except actions.ActionError as e:
                self.write(f"✗ {e.message}\n\n", "fail")
                return
            proposal = permissions.Proposal(f"open {path}", "click", plan)
            if self.pending:
                self.resolve_card(False, "Skipped — nothing was run.")
            if permissions.needs_approval(plan):
                self.show_card(proposal)
            else:
                self.show_result(plan, permissions.run(proposal))

        def show_card(self, proposal):
            frame, run_btn, deny_btn, status = cards.build_card(
                self.chat, cards.card_for(proposal),
                on_run=lambda: self.resolve_card(True), on_deny=lambda: self.resolve_card(False),
                on_chat=self.just_chat if proposal.route == "model" else None)
            self.chat.configure(state="normal")
            self.chat.window_create("end", window=frame)
            self.chat.insert("end", "\n\n")
            self.chat.configure(state="disabled")
            self.chat.see("end")
            self.pending = (proposal, run_btn, deny_btn, status)
            self.status.config(text=f"Waiting for your approval — {cards.APPROVE_KEY} to run, Esc to deny")

        def resolve_card(self, approved, denied_text="Denied — nothing was run."):
            if not self.pending:
                return
            proposal, run_btn, deny_btn, status = self.pending
            self.pending = None
            for button in run_btn.master.winfo_children():     # Run, Deny and Just chat
                button.config(state="disabled")
            if not approved:
                permissions.deny(proposal)
                status.config(text=denied_text, fg="#888888")
                self.status.config(text="Ready" if self.ready else "Starting local model…")
                return
            status.config(text="Running…", fg="#666666")

            def work():
                try:
                    result = permissions.run(proposal, approved=True,
                                             progress=lambda text: self.q.put(("progress", (status, text))))
                except Exception as e:
                    result = actions.Result(False, f"Couldn't run it: {e}")
                self.q.put(("action", (status, result)))
            threading.Thread(target=work, daemon=True).start()

        def on_approve_key(self, event):
            if self.pending:
                self.resolve_card(True)
            else:
                self.send()
            return "break"

        def on_deny_key(self, event):
            if self.pending:
                self.resolve_card(False)
                return "break"
            return None

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
            if self.pending:
                self.resolve_card(False)
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
                        self.ready = True
                        self.status.config(text=f"Ready · BitNet b1.58 2B4T · CPU · {cfg.THREADS} threads · "
                                                f"localhost only")
                        self.write("Model loaded locally. The model never sends anything you type anywhere; only actions "
                                   "you approve (like a download) use the network.\n\n", "note")
                    elif kind == "fatal":
                        self.status.config(text="Model failed to start")
                        messagebox.showerror("BitNet Desk", val)
                    elif kind == "tok":
                        self.reply += val
                        self.write(val)
                    elif kind in ("done", "err"):
                        self.finish(kind, val)
                    elif kind == "routed":
                        self.on_routed(*val)
                    elif kind == "progress":
                        status, text = val
                        try:
                            status.config(text=text)
                        except tk.TclError:
                            pass
                    elif kind == "action":
                        status, result = val
                        text = cards.outcome_line(result) + (f"\n{result.output}" if result.output else "")
                        if result.items:
                            self.write_items(result.items)
                            self.write("\n")
                        try:
                            status.config(text=text, fg="#2d6a2d" if result.ok else "#b00020")
                        except tk.TclError:        # card was cleared meanwhile; the log has it
                            pass
                        self.status.config(text="Ready" if self.ready else "Starting local model…")
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
