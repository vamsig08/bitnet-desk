"""Tkinter desktop UI: the main window, the command box (global hotkey) and the menu-bar icon."""
import queue
import threading
import time
from pathlib import Path

from .. import actions
from .. import config as cfg
from .. import llm, login, server
from ..actions import misses, permissions, pipeline
from . import cards, mac
from .commandbox import CommandBox, hide_after
from .menubar import MenuBar
from .transcript import Tee, Transcript


def hotkey_setting():
    """(HotKey or None, problem or None) from [ui] hotkey."""
    if cfg.HOTKEY in (False, "", "off", "none", "false"):
        return None, None
    try:
        return mac.parse_hotkey(cfg.HOTKEY), None
    except ValueError as e:
        return None, f"[ui] hotkey: {e} — no hotkey"


def is_wrong(msg):
    return msg.lower() == "/wrong" or msg.lower().startswith("/wrong ")


def run_app(background=False):
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
            self.pending = None            # (proposal, run button, deny button, status label, view, from box)

            root.title("BitNet Desk — private, offline")
            root.geometry("820x640")
            root.minsize(520, 400)

            self.chat = scrolledtext.ScrolledText(root, wrap="word", state="disabled",
                                                  font=("Helvetica", 12), padx=10, pady=8)
            self.chat.pack(fill="both", expand=True, padx=8, pady=(8, 4))
            self.main_view = Transcript(self.chat)
            self.out = self.main_view      # where the current exchange is written
            self.from_box = False          # …and whether it came from the command box
            self.last_miss = None          # (what the user typed, what the app did) for /wrong
            self.last_user_text = ""

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

            for err in cfg.CONFIG_ERRORS + permissions.config_problems():
                self.write(f"Config: {err}\n\n", "note")
            self.setup_always_there()
            root.protocol("WM_DELETE_WINDOW", self.on_close)
            threading.Thread(target=self.boot, daemon=True).start()
            self.root.after(30, self.poll)
            if background:
                root.withdraw()
            else:
                self.entry.focus_set()

        # -- hotkey, command box, menu bar
        def setup_always_there(self):
            hotkey, problem = hotkey_setting()
            self.hotkey = None
            label = hotkey.label if hotkey else "no hotkey"
            self.box = CommandBox(self.root, on_submit=self.box_submit, on_approve=self.box_approve_key,
                                  on_escape=self.box_escape, on_open_window=self.box_open_window,
                                  approve_events=cards.APPROVE_EVENTS,
                                  hint=f"{label} hides · {cards.APPROVE_KEY} runs a card · Esc closes")
            warnings = [problem] if problem else []
            if hotkey:
                self.hotkey = mac.GlobalHotKey(hotkey, lambda: self.q.put(("hotkey", None)))   # no Tk calls in there
                state = self.hotkey.register()
                if state == "ok":
                    others = mac.find_clashes(hotkey)
                    if others:
                        warnings.append(f"{hotkey.label} is also used by {', '.join(others)} — both will react. "
                                        "Change that shortcut, or set [ui] hotkey in config.toml.")
                elif state != "unsupported":
                    warnings.append(f"Couldn't register {hotkey.label} ({state}) — set [ui] hotkey in config.toml.")
                    self.hotkey = None
                else:
                    self.hotkey = None
            self.login_var = tk.BooleanVar(value=login.is_enabled())
            items = [(f"Show command box ({label})" if self.hotkey else "Show command box", self.box.show),
                     ("Open window", self.show_window), None]
            items += [("note", "⚠ " + w.split(" — ")[0]) for w in warnings]
            items += [("check", "Launch at login", self.login_var, self.toggle_login), None,
                      ("Quit BitNet Desk", self.quit)]
            self.menubar = MenuBar(self.root, "BitNet Desk", items)
            for w in warnings:
                self.write(f"Hotkey: {w}\n\n", "note")
            # macOS: ⌘Q / Dock › Quit, and a click on the Dock icon
            self.root.createcommand("tk::mac::Quit", self.quit)
            self.root.createcommand("tk::mac::ReopenApplication", self.show_window)

        def show_window(self):
            self.root.deiconify()
            self.root.lift()
            mac.activate()
            self.entry.focus_set()

        def toggle_login(self):
            try:
                self.set_status(login.set_enabled(self.login_var.get()))
            except OSError as e:
                self.login_var.set(login.is_enabled())
                self.set_status(f"Couldn't change launch at login: {e}")

        def box_submit(self, msg):
            if self.busy:
                self.box.set_status("Still working on the last one — wait, or press Stop in the window.")
                return False
            if not is_wrong(msg):
                self.skip_card()               # before clearing: the card lives in the box
                self.box.view.clear()
            return self.submit(msg, Tee(self.box.view, self.main_view), from_box=True)

        def box_approve_key(self, event):
            if self.pending and self.pending[5]:
                self.resolve_card(True)
            else:
                self.box._submit(event)
            return "break"

        def box_escape(self, event):
            if self.pending and self.pending[5]:
                self.resolve_card(False)
            else:
                self.box.hide()
            return "break"

        def box_open_window(self):
            self.box.hide(after_action=True)
            self.show_window()

        def set_status(self, text):
            self.status.config(text=text)
            self.box.set_status(text)

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
            """The window's close button hides it; the server, hotkey and menu-bar icon stay."""
            if self.menubar.ok or self.hotkey:
                self.root.withdraw()
            else:
                self.quit()

        def quit(self):
            self.stop_event.set()
            if self.server_proc and self.server_proc.poll() is None:
                self.server_proc.terminate()
            if self.hotkey:
                self.hotkey.unregister()
            self.menubar.destroy()
            self.root.destroy()

        # -- transcript helpers
        def write(self, text, tag=None):
            self.out.write(text, tag)

        # -- sending
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
            if self.submit(msg, self.main_view, from_box=False):
                self.entry.delete("1.0", "end")

        def submit(self, msg, view, from_box):
            """Handle one message from the window or the box. Returns True if it was taken
            (the input can be cleared), False if it should stay in the input."""
            self.out, self.from_box = view, from_box
            if is_wrong(msg):
                self.report_wrong(msg[len("/wrong"):].strip())
                return True
            self.skip_card()
            if not self.attachment and self.try_action(msg):
                return True
            if not self.ready:
                self.write("The model is still loading — commands like “open Safari” already work.\n\n", "note")
                return False
            if not self.attachment and pipeline.wants_router(msg):
                self.write("You: ", "user")
                self.write(msg + "\n\n")
                self.busy = True
                self.set_status("Working out whether that's a command…")
                threading.Thread(target=self.route, args=(msg,), daemon=True).start()
                return True
            self.start_chat(msg)
            return True

        def skip_card(self):
            if self.pending:                   # a new message skips the open card
                self.resolve_card(False, "Skipped — nothing was run.")

        def start_chat(self, msg, user_written=False):
            shown = msg
            self.last_user_text = msg
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
            self.set_status("Thinking…")
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
            self.write("You: ", "user")
            self.write(msg + "\n\n")
            if outcome.kind == "ask":
                self.show_card(outcome.proposal)
            else:
                self.show_result(outcome.proposal, outcome.result)
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
                self.show_result(outcome.proposal, outcome.result)
                self.set_status("Ready")
            else:                              # refused: the user named something that isn't there
                hint = f" Did you mean: {', '.join(outcome.suggestions)}?" if outcome.suggestions else ""
                self.write(f"✗ {outcome.message}{hint}\n", "fail")
                self.offer_wrong(msg, misses.describe_refusal(outcome.message))
                self.write("\n")
                self.set_status("Ready")

        def just_chat(self):
            """Card button on model suggestions: drop the action, answer the message in chat."""
            if not self.pending:
                return
            text, view, from_box = self.pending[0].text, self.pending[4], self.pending[5]
            self.resolve_card(False, "Answered in chat instead — nothing was run.")
            self.out, self.from_box = view, from_box
            self.start_chat(text, user_written=True)

        def show_result(self, proposal, result):
            if result.ok and proposal.plan.answer:
                self.write("Assistant: ", "bot")
                self.write(result.message + "\n")
            else:
                self.write(cards.outcome_line(result) + "\n", "ok" if result.ok else "fail")
                if result.items:
                    self.write_items(result.items)
                elif result.output:
                    self.write(result.output + "\n", "note")
            self.offer_wrong(proposal.text, misses.describe_action(proposal, "auto", result))
            self.write("\n")
            if self.from_box and hide_after(result, proposal.plan):
                self.box.hide(after_action=True)

        # -- "that was wrong": save real mistakes, later turned into eval cases
        def offer_wrong(self, text, what):
            """A small link under a reply/action; a click saves it to the misses file."""
            self.last_miss = (text, what)
            links = []

            def click():
                if not links:
                    return
                for link in links:
                    link.add(" — saved, thanks", "note")
                links.clear()
                misses.record(text, what)
                self.set_status("Saved as a miss — `python -m bitnet_desk.actions misses` turns it into an eval case")
            links += self.out.link("⚑ that was wrong", click, style="faint")
            self.write("\n")

        def report_wrong(self, note):
            """/wrong [what you expected] — saves the last exchange as a miss."""
            if not self.last_miss:
                self.write("Nothing to report yet — /wrong saves the last reply or action as a miss.\n\n", "note")
                return
            text, what = self.last_miss
            misses.record(text, what, note)
            self.write(f"Saved “{text}” as a miss" + (f" (you expected: {note})" if note else "")
                       + ". `python -m bitnet_desk.actions misses` turns it into an eval case.\n\n", "note")

        def write_items(self, paths, view=None):
            """Clickable paths; a click opens it through the permission gate like any other action."""
            from ..actions.resolve import tilde
            view = view or self.out
            for path in paths:
                view.write("  ")
                view.link(tilde(path), lambda p=path, v=view: self.open_item(p, v))
                view.write("\n")

        def open_item(self, path, view):
            self.out, self.from_box = view, view is not self.main_view
            try:
                plan = actions.TOOLS["open_path"].make_plan({"path": path})
            except actions.ActionError as e:
                self.write(f"✗ {e.message}\n\n", "fail")
                return
            proposal = permissions.Proposal(f"open {path}", "click", plan)
            self.skip_card()
            if permissions.needs_approval(plan):
                self.show_card(proposal)
            else:
                self.show_result(proposal, permissions.run(proposal))

        def show_card(self, proposal):
            frame, run_btn, deny_btn, status = cards.build_card(
                self.out.card_parent, cards.card_for(proposal),
                on_run=lambda: self.resolve_card(True), on_deny=lambda: self.resolve_card(False),
                on_chat=self.just_chat if proposal.route == "model" else None)
            self.out.embed(frame, summary=f"⌛ {proposal.plan.summary} — waiting for you in the command box")
            self.pending = (proposal, run_btn, deny_btn, status, self.out, self.from_box)
            self.set_status(f"Waiting for your approval — {cards.APPROVE_KEY} to run, Esc to deny")

        def resolve_card(self, approved, denied_text="Denied — nothing was run."):
            if not self.pending:
                return
            proposal, run_btn, deny_btn, status, view, from_box = self.pending
            self.pending = None
            for button in run_btn.master.winfo_children():     # Run, Deny and Just chat
                button.config(state="disabled")
            if not approved:
                permissions.deny(proposal)
                status.config(text=denied_text, fg="#888888")
                view.card_status(denied_text, "note")
                self.set_status("Ready" if self.ready else "Starting local model…")
                self.last_miss = (proposal.text, misses.describe_action(proposal, "denied"))
                if denied_text.startswith("Denied"):           # an explicit Deny may mean "wrong action"
                    self.out = view
                    self.offer_wrong(*self.last_miss)
                    self.write("\n")
                return
            status.config(text="Running…", fg="#666666")

            def work():
                try:
                    result = permissions.run(proposal, approved=True,
                                             progress=lambda text: self.q.put(("progress", (status, text))))
                except Exception as e:
                    result = actions.Result(False, f"Couldn't run it: {e}")
                self.q.put(("action", (status, proposal, result, view, from_box)))
            threading.Thread(target=work, daemon=True).start()

        def on_action_done(self, status, proposal, result, view, from_box):
            self.last_miss = (proposal.text, misses.describe_action(proposal, "approved", result))
            text = cards.outcome_line(result) + (f"\n{result.output}" if result.output else "")
            view.card_status(text, "ok" if result.ok else "fail")
            if result.items:
                self.write_items(result.items, view)
                view.write("\n")
            try:
                status.config(text=text, fg="#2d6a2d" if result.ok else "#b00020")
            except tk.TclError:        # card was cleared meanwhile; the log has it
                pass
            self.out = view
            self.offer_wrong(*self.last_miss)
            self.write("\n")
            self.set_status("Ready" if self.ready else "Starting local model…")
            if from_box and hide_after(result, proposal.plan):
                self.root.after(700, lambda: self.box.hide(after_action=True))   # long enough to see ✓

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
            self.main_view.clear()
            self.box.view.clear()

        def save(self):
            path = filedialog.asksaveasfilename(defaultextension=".md",
                                                filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
            if not path:
                return
            lines = [f"**{'You' if r == 'user' else 'Assistant'}:** {t}\n" for r, t in self.history]
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self.set_status(f"Saved to {path}")

        # -- event loop bridge
        def poll(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == "ready":
                        self.ready = True
                        self.set_status(f"Ready · BitNet b1.58 2B4T · CPU · {cfg.THREADS} threads · localhost only")
                        self.main_view.write("Model loaded locally. The model never sends anything you type "
                                             "anywhere; only actions you approve (like a download) use the "
                                             "network.\n\n", "note")
                    elif kind == "fatal":
                        self.set_status("Model failed to start")
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
                        self.on_action_done(*val)
                    elif kind == "hotkey":
                        self.box.toggle()
            except queue.Empty:
                pass
            self.root.after(30, self.poll)

        def finish(self, kind, val):
            self.busy = False
            self.send_btn.config(text="Send")
            reply = self.reply.strip()
            if kind == "err":
                self.write(f"\n[error: {val}]", "note")
                self.set_status("Error talking to the local model")
            else:
                if reply:
                    self.history.append(("assistant", reply))
                if self.stop_event.is_set():
                    self.write(" [stopped]", "note")
                tps = (val or {}).get("predicted_per_second")
                self.set_status(f"Ready · {tps:.1f} tokens/s" if tps else "Ready")
                if reply:
                    self.write("\n")
                    self.offer_wrong(self.last_user_text, misses.describe_chat(reply))
                    self.write("\n")
                    return
            self.write("\n\n")

    root = tk.Tk()
    App(root)
    root.mainloop()
