#!/usr/bin/env python3
"""
BitNet Desk — a private, CPU-only desktop assistant.

Runs Microsoft's BitNet b1.58 2B4T (1.58-bit ternary weights) through bitnet.cpp's
llama-server, bound to 127.0.0.1. Nothing leaves your machine. Standard library only.
"""
import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- configuration
HERE = Path(__file__).resolve().parent
BITNET_DIR = Path(os.environ.get("BITNET_DIR", HERE / "BitNet"))
MODEL = Path(os.environ.get(
    "BITNET_MODEL",
    BITNET_DIR / "models" / "BitNet-b1.58-2B-4T" / "ggml-model-i2_s.gguf"))
HOST = "127.0.0.1"                      # never 0.0.0.0 — keeps the model local-only
PORT = int(os.environ.get("BITNET_PORT", "8080"))
BASE = f"http://{HOST}:{PORT}"
CTX = int(os.environ.get("BITNET_CTX", "4096"))
THREADS = int(os.environ.get("BITNET_THREADS", str(max(1, (os.cpu_count() or 4) // 2))))
MAX_NEW_TOKENS = int(os.environ.get("BITNET_MAX_TOKENS", "768"))
TEMPERATURE = float(os.environ.get("BITNET_TEMP", "0.6"))

SYSTEM_PROMPT = os.environ.get("BITNET_SYSTEM", (
    "You are a helpful, concise assistant running entirely offline on the user's computer. "
    "Answer clearly. If you are not sure about a fact, say so instead of guessing."))

MAX_PROMPT_CHARS = CTX * 3 - MAX_NEW_TOKENS * 4   # rough chars/token budget, leaves room to reply
MAX_FILE_CHARS = 6000
STOP = ["<|eot_id|>", "<|end_of_text|>", "\nUser:"]


# ---------------------------------------------------------------- model plumbing
def build_prompt(system, history):
    """BitNet b1.58 2B4T chat format. The server adds <|begin_of_text|> itself."""
    parts = [f"System: {system}<|eot_id|>"]
    for role, text in history:
        parts.append(f"{'User' if role == 'user' else 'Assistant'}: {text}<|eot_id|>")
    parts.append("Assistant: ")
    return "".join(parts)


def fit_history(system, history):
    """Drop the oldest turns until the prompt fits the context window."""
    h = list(history)
    while len(h) > 1 and len(build_prompt(system, h)) > MAX_PROMPT_CHARS:
        h.pop(0)
    if h and len(build_prompt(system, h)) > MAX_PROMPT_CHARS:   # one huge message
        role, text = h[-1]
        h[-1] = (role, text[-MAX_PROMPT_CHARS // 2:])
    return h


def server_up():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def find_server_bin():
    for p in (BITNET_DIR / "build/bin/llama-server",
              BITNET_DIR / "build/bin/Release/llama-server.exe",
              BITNET_DIR / "build/bin/llama-server.exe"):
        if p.exists():
            return p
    return None


def start_server(log_path):
    exe = find_server_bin()
    if exe is None:
        raise RuntimeError(f"llama-server not found under {BITNET_DIR}/build/bin — run setup first.")
    if not MODEL.exists():
        raise RuntimeError(f"Model not found: {MODEL} — run setup first.")
    log = open(log_path, "w")
    cmd = [str(exe), "-m", str(MODEL), "-c", str(CTX), "-t", str(THREADS),
           "-ngl", "0", "--host", HOST, "--port", str(PORT)]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, **kwargs)


def stream_completion(prompt, on_token, stop_event):
    body = json.dumps({
        "prompt": prompt, "n_predict": MAX_NEW_TOKENS, "temperature": TEMPERATURE,
        "top_p": 0.9, "repeat_penalty": 1.1, "stop": STOP,
        "stream": True, "cache_prompt": True,
    }).encode()
    req = urllib.request.Request(BASE + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            if stop_event.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = json.loads(line[5:].strip())
            if data.get("content"):
                on_token(data["content"])
            if data.get("stop"):
                return data.get("timings", {})
    return {}


def read_text_file(path):
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > MAX_FILE_CHARS
    return p.name, text[:MAX_FILE_CHARS], truncated


# ---------------------------------------------------------------- desktop UI
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
                if not server_up():
                    self.server_proc = start_server(HERE / "server.log")
                    t0 = time.time()
                    while not server_up():
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

            self.history = fit_history(SYSTEM_PROMPT, self.history)
            prompt = build_prompt(SYSTEM_PROMPT, self.history)
            self.busy, self.reply = True, ""
            self.stop_event.clear()
            self.send_btn.config(text="Stop")
            self.status.config(text="Thinking…")
            threading.Thread(target=self.generate, args=(prompt,), daemon=True).start()

        def generate(self, prompt):
            try:
                t = stream_completion(prompt, lambda tok: self.q.put(("tok", tok)), self.stop_event)
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
                self.attachment = read_text_file(path)
            except Exception as e:
                messagebox.showerror("Attach", f"Couldn't read that file:\n{e}")
                return
            name, _, cut = self.attachment
            self.attach_lbl.config(text=f"📎 {name} will be sent with your next message"
                                        + (f" (first {MAX_FILE_CHARS} characters)" if cut else ""))

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
                        self.status.config(text=f"Ready · BitNet b1.58 2B4T · CPU · {THREADS} threads · "
                                                f"localhost only")
                        self.write("Model loaded locally. Nothing you type leaves this computer.\n\n", "note")
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


if __name__ == "__main__":
    run_app()
