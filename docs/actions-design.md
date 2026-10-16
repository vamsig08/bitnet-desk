# Action system — design (proposal, not built yet)

Status: **approved 2026-10-16**, with the decisions below. Being built in the steps of §9.

### Decisions (2026-10-16)
1. **Auto-approve list** in config.toml (`[actions] auto_approve`), default
   `["open_app", "open_path", "now"]`. Only read-only and "changes things" tools may be listed;
   `open_url`, `download` and `run_shell` **always ask** — listing them is a config error and is ignored.
   A plan whose risk was escalated (e.g. `open_path` on an executable → shell) always asks too.
2. **The model is not called for every message.** Order: fast path → command-verb check → model
   router. The router runs only if the message starts with a command verb (`open`, `start`, `launch`,
   `download`, `find`, `run`, `play`, `show`) or with `/`; everything else goes straight to chat.
   (Polite openers — "please", "can you", "could you", "hey" — are stripped before the check.)
   The eval has trap phrases on both sides of this check (§8).
3. **No model-written shell commands** in v1: `run_shell` only runs a command typed literally.
4. **`-np 2`**: the server gets two slots (chat and router keep separate prompt caches). Extra RAM is
   measured and recorded in `docs/benchmarks.md` when the router lands (step 4).
5. **Start project opens Terminal**, configurable: `[actions] terminal` (global) and per-project
   `terminal` override.

Goal: one chat box where "open Safari", "start project bitnet-desk", "download https://…",
"find my resume" and "what time is it" just work, every non-read-only action waits for one
approval click, and everything lands in an action log. Few tools, done very well, behind one
permission system.

### Changes made while building (2026-10-16)
Driven by the try-out and the first eval runs (`docs/evals.md`):
- **Below the threshold → chat, not an "are you sure?" card.** All 9 picks under 0.7 were wrong in
  the first run, so asking would only have added clicks.
- **The grammar is built per message.** URLs (`download`, `open_url`), shell commands and paths
  may only be *copied* from the message (`enum`); if there is nothing to copy, the tool isn't
  offered. A plain `pattern` made the model mangle URLs (`report.4pdf`); copying fixed downloads
  completely. The prompt stays fixed, so the prompt cache still works.
- **Grounding has three outcomes:** literal (value in the message) → normal gate; inferred
  ("play some music" → Spotify, a file search that doesn't mention files) → always asks, even if
  the tool is auto-approved; invented → chat. A named target that doesn't exist (an unknown
  project) gets a refusal with suggestions instead.
- `now` from the model refuses questions about other places/time zones; its answer says "local time".
- `/Users/username/…` placeholders from the model map to the real home folder (still must exist).
- Configured project names are listed in the router prompt; without projects the tool isn't offered.
- A `help` command lists what the app can do (from the registry), and the chat system prompt lists
  the same commands and forbids claiming actions it didn't take.
- Model-path cards have a **Just chat** button.

---

## 1. What the pinned server supports (checked, not assumed)

Pinned BitNet `765741d` → `3rdparty/llama.cpp` @ `40ed0f290` (2025-05-21). In
`examples/server/server.cpp` and tested against the live server with the real model:

| Feature | Supported | Notes |
|---|---|---|
| `"json_schema"` on `POST /completion` | **yes** | Converted to a GBNF grammar by `common/json-schema-to-grammar.cpp`. Supports `oneOf`/`anyOf`, `const`, `enum`, `required`, `additionalProperties: false`, `pattern`, `minLength`/`maxLength`, arrays with `minItems`. |
| `"grammar"` (raw GBNF) | **yes** | `root ::= "yes" \| "no"` → `yes`. Sending both `grammar` and `json_schema` → HTTP 400 (by design). |
| `"n_probs"` | **yes** | Each generated token comes back with its top-n candidates in `completion_probabilities`. **Caveat:** these are the model's probabilities *before* the grammar filter (we saw `spotify`, `po`, `play` as candidates where the grammar only allows tool names). So we renormalise over the tokens the grammar allows. |
| `"cache_prompt"` | yes | Reuses the KV cache for a shared prompt prefix. Default off in this version; we should send `true`. |
| `logit_bias` | yes | Not needed for v1. |
| `/v1/chat/completions` `tools` | no | Too old for native tool calling. Not needed: `json_schema` does the job. |

Probe with 4 tools + `none`, temperature 0, M1, 4 threads (prompt ~85 tokens):

| Input | Output | Time |
|---|---|---|
| open spotify | `open_app {"name": "Spotify"}` | 2.3–6.1 s (first call includes prompt eval) |
| go to github.com | `open_url {"url": "github.com"}` | 2.3 s |
| find my tax return pdf | `find_files {"query": "Tax return pdf"}` | 2.8 s |
| write me a poem about cats | `none` | 2.8 s |
| show me the weather in Paris | `open_url {"url": "http://www.weather.com/en/weather/today/l/Paris,FR"}` | 2.8 s — **invented URL** |

Tool-choice probabilities: "open spotify" 0.98, "github" 0.88, "weather" 0.82, "poem → none" 0.81,
"play some music → open" 0.75 / none 0.23. So probabilities do separate clear from fuzzy cases, but
not strongly; the eval has to set the threshold.

Takeaways that shape the design:
1. Grammar-constrained JSON works on our build — the model **can't** produce unparseable output or
   an unknown tool name.
2. The grammar can't stop the model from **inventing values** (the weather URL). Arguments need
   grounding checks in Python.
3. `open_app` and `open_url` share the first token `open`, so the tool-choice probability is spread
   across later tokens. **Tool names must differ at the first token** (checked by a test via
   `/tokenize`) so one token = one decision = one clean confidence number.
4. ~25–40 generated tokens of pretty-printed JSON cost ~2–3 s. A compact hand-written GBNF (no
   whitespace) roughly halves that; start with `json_schema`, measure, switch only if worth it.

---

## 2. Overview

```
user text
   │
   ├─► Fast path (rules, no model, <10 ms) ──match──┐
   │        │ no match                               │
   │        ▼                                        ▼
   │   Model router (1 constrained call) ──tool──► Resolve & ground args ──► Plan (frozen)
   │        │ "chat" / unsure                         │ fails                    │
   │        ▼                                         ▼                          ▼
   │   normal chat reply                      explain + suggestions     Permission gate
   │                                                                   read-only → run
   │                                                                   else → approval card
   │                                                                             │
   └──────────────────────────────────────────────────────────────────────────► Action log
```

New package `bitnet_desk/actions/`:

```
actions/
  registry.py     Tool, Risk, Plan; the list of tools
  fastpath.py     ordered rules → (tool, args) or None
  router.py       model path: prompt, json_schema, confidence
  resolve.py      shared arg resolution + grounding (apps, paths, projects, URLs)
  permissions.py  the gate: decide auto-run vs approval; run approved plans
  log.py          append-only JSONL action log + reader
  eval.py         python -m bitnet_desk.actions eval
  tools/          one module per tool
```

All model access still goes through `llm.py` (a new `complete_json(prompt, schema, n_probs)`
helper), keeping the model layer swappable.

---

## 3. Tools, risk levels and plans

```python
class Risk(IntEnum):
    READ_ONLY = 0   # reads local state only
    CHANGES   = 1   # launches/opens things, writes local files
    NETWORK   = 2   # causes network traffic (download, opening a URL)
    SHELL     = 3   # runs an arbitrary command

@dataclass(frozen=True)
class Tool:
    name: str             # unique first token (tested)
    summary: str          # one line, shown to the model and in "what can you do?"
    args: dict            # JSON schema for args (used for grammar AND Python validation)
    risk: Risk            # base risk
    plan: Callable[[dict], Plan]   # resolve args → concrete plan, or raise ActionError
    run: Callable[[Plan], Result]

@dataclass(frozen=True)
class Plan:
    tool: str
    risk: Risk            # max(tool.risk, risk derived from args) — never lowered
    summary: str          # "Open Visual Studio Code and a Terminal in ~/Projects/bitnet-desk"
    commands: tuple[tuple[str, ...], ...]   # exact argv lists, shown and executed verbatim
    details: dict         # e.g. download destination, result count limit
```

Rules:
- **What you approve is what runs.** The approval card renders `plan.commands` (via `shlex.join`) and
  the same frozen object is executed. No re-planning after approval.
- **argv lists, never `shell=True`**, except `run_shell`, whose whole point is a shell string — and
  the card shows that exact string.
- **Risk only goes up.** Arguments can escalate (opening a `.command`/`.app`/`.sh`/executable file via
  `open_path` → `SHELL`); nothing — model output included — can lower it.
- **No network before approval.** No HEAD requests, favicon fetches or URL previews to build the card.
- Tool output (e.g. `mdfind` results) is shown to the user and **not fed back to the model** in v1.
  This avoids prompt injection via file names and keeps each action a single step.

### First tools

| Tool | Args | Risk | Plan / command | Notes |
|---|---|---|---|---|
| `help` | – | read-only | answer built from the tool registry | Added after the try-out: the model invented abilities when asked "what can you do?". |
| `now` | – | read-only | Python `datetime` | Answers "what time/day is it" without the model making it up. |
| `find_files` | `query`, `kind?` (pdf/folder/image/…) | read-only | `mdfind -onlyin ~ -name <q>` (+ content query fallback) | Top 20 shown as clickable rows; clicking one goes through `open_path` (and its gate). If Spotlight returns nothing (index off/excluded — the case on the dev Mac), a 3-second name search of ~ (skipping Library, hidden folders, node_modules). |
| `open_app` | `name` | changes | `open -a <resolved .app path>` | Name resolved against installed apps (`/Applications`, `/System/Applications`, `…/Utilities`, `~/Applications`) + aliases in config; fuzzy match with stdlib `difflib`. Unknown app → error with suggestions, never a guess. |
| `open_path` (file/folder) | `path` | changes (→ shell for executables) | `open <path>` / `open -R` to reveal | Path must exist after `~` expansion. Names like "Downloads", "Desktop" map to known folders. |
| `open_url` | `url` | network | `open <url>` | `https://` and `http://` only (no `file:`, `javascript:`, custom schemes). Bare domains get `https://`. Grounded (see §5). |
| `start_project` | `name` | changes | `open -a <editor> <path>` + `open -a <terminal> <path>` | Defined in config.toml; unknown name → list known projects. |
| `download` | `url`, `filename?` | network | stdlib `urllib` → `~/Downloads/<name>.part`, then rename | https by default; never overwrites (`name (1).pdf`); size cap from config (default 2 GB); progress + cancel; sets `com.apple.quarantine` so Gatekeeper treats it like a browser download. Card notes that redirects may change the host. |
| `run_shell` | `command` | shell | `/bin/zsh -c <command>` in `~` | **Always** confirms. v1: command must be literally in the user's message (backticks, quotes, or `$ ` prefix) — the 2B model does not author shell commands. Timeout 60 s, output capped and shown. |

Tools have a code name (used in config, the log and the eval) and a short router label shown to the
model: `now`→now, `find_files`→find, `open_app`→app, `open_path`→path, `open_url`→url,
`start_project`→project, `download`→download, `run_shell`→shell, plus `chat`. The labels are chosen
to be single, distinct first tokens; a test checks this with the real tokenizer's output recorded
once from `/tokenize`.

### Actions and projects in config.toml

```toml
[actions]
auto_approve = ["open_app", "open_path", "now"]   # never open_url / download / run_shell
editor = "Visual Studio Code"   # default editor for start_project
terminal = "Terminal"           # default terminal for start_project ("iTerm", … or false for none)
log = true                      # write the action log

[projects.bitnet-desk]
path = "~/Projects/bitnet-desk"
aliases = ["bitnet", "bitnet desk"]
editor = "Visual Studio Code"   # optional per-project override
terminal = "Terminal"           # optional per-project override

[aliases.apps]
"vs code" = "Visual Studio Code"
"code"    = "Visual Studio Code"
```

`config.py` currently rejects unknown top-level keys; it gets explicit, validated support for the
`actions`, `projects` and `aliases` tables (bad entries skipped with a message, same as today). Running a
command on project start (e.g. `npm run dev`) would be `SHELL` risk — left for later on purpose.

---

## 4. Fast path

Deterministic rules in `fastpath.py`, tried in order, no model call. Input is normalised first:
lowercase for matching (original kept for args), strip "please", "can you", "could you", trailing
`?!.`.

| Rule | Pattern (sketch) | Result |
|---|---|---|
| Time/date | fixed phrases: "what time is it", "what's the date", "what day is it", "what's today" | `now` |
| Shell | starts with `$ `, or `run` + backtick/quoted command | `run_shell` with the literal command |
| Download | `(download\|save\|grab) <http(s) URL>( to downloads)?` | `download` |
| Project | `(start\|open\|work on\|switch to) (project )?<X>` where X is a known project name/alias | `start_project` |
| Open | `(open\|launch\|start\|show me) <X>` → resolve X in order: URL-like → `open_url`; `~`/`/` path → `open_path`; installed app (exact/alias, or fuzzy ≥ 0.9) → `open_app`; known folder word (downloads, desktop, documents, home) → `open_path` | first that resolves |
| Find | `(find\|search for\|locate\|where is\|where's) (my )?<X>` **and** X contains a file word (file, folder, pdf, doc, document, photo, my …) | `find_files` |

Principles:
- **Precision over coverage.** A wrong fast-path match is worse than a miss (the miss just goes to
  the model). Rules only fire when the target resolves to something real (app installed, project
  configured, URL well-formed).
- Trap phrases are in the eval: "open a can of worms…", "how do I download a file with curl?",
  "find me a good pancake recipe", "start thinking about…" must **not** match.
- If X resolves two ways (an app *and* a folder called "Notes"), take the documented precedence
  (app first) and show the alternative as a button on the card.
- The fast path works even when the model server is down or still loading.

---

## 5. Model path

One `/completion` call, temperature 0, `cache_prompt: true`, `n_probs: 5`, `n_predict` ~80, with a
`json_schema` that is a `oneOf` over the tools, each `{"tool": {"const": name}, "args": {…}}` with
`additionalProperties: false` and `maxLength` on every string, plus `{"tool": "chat"}` meaning "not
an action, just answer". Tool name comes first in the object so its probability is read early.

Prompt (fixed prefix, so the KV cache is reused across calls):

```
System: You turn the user's request into exactly one tool call as JSON. If no tool fits, or the
request is a question or conversation, choose "chat". Never invent URLs, paths or commands — only
use ones the user wrote.
Tools:
- app(name): launch a Mac application
- url(url): open a web page the user named
- …
Examples: (6–8 short few-shot lines, including two "chat" ones)
<|eot_id|>User: {text}<|eot_id|>Assistant:
```

**Confidence.** Take `completion_probabilities` at the tool-name token, keep only the candidates that
are valid first tokens of tool names, renormalise → `p_tool`. (Needed because the server reports
pre-grammar probabilities.) Built: `router.tool_confidence`; below `THRESHOLD` (0.7) → chat.

**Grounding checks** (Python, after the call, before planning) — this is where invented values die:
- `open_url` / `download`: the URL's host must appear in the user's text (a bare word "wikipedia" may
  ground `wikipedia.org` only via a small built-in map of well-known sites; otherwise reject).
- `run_shell`: command must appear verbatim in the user's text.
- `open_path`: must exist; `start_project`: must be configured; `open_app`: must resolve to an installed app.
- `find_files` query: every word must come from the user's text (lowercased, stop-words removed).

**When the router runs (command-verb check).** Only for messages the fast path didn't take that start
with `open`, `start`, `launch`, `download`, `find`, `run`, `play`, `show` (after stripping polite
openers), or with `/` (the `/` is removed; it means "treat this as a command"). Everything else goes
straight to chat with no extra latency. Cost: actions phrased without a command verb ("is it friday
yet?", "go to …" unless a fast rule takes it) aren't recognised; the eval counts these as gate misses.

**Slots / cache.** The server runs with `-np 2 -c 2×CTX` (two CTX-token slots). Chat requests pin
`id_slot: 0`, router requests `id_slot: 1`, so neither evicts the other's prompt cache. Extra RAM is
measured when this lands and recorded in `docs/benchmarks.md`.

**Latency budget.** Fast path: instant. Model path: target ≤ 3 s to the card on M1.

---

## 6. Permission gate, approval card, action log

**Gate** (`permissions.py`) is the only code that calls `tool.run`. Tools never run themselves.

- `plan.risk == READ_ONLY` → run now, log `decision: "auto"`.
- `plan.risk == CHANGES` and the tool is in `auto_approve` → run now, show a one-line note in the
  chat ("Opened Spotify"), log `decision: "auto"`.
- Otherwise → approval card in the chat:
  ```
  ┌ Open app · CHANGES ─────────────────────────────┐
  │ open -a /Applications/Spotify.app               │
  │ from: fast path                                 │
  │                      [Deny (Esc)]  [Run (⌘↩)]   │
  └─────────────────────────────────────────────────┘
  ```
  Risk badge colour by level; NETWORK cards show the full URL and destination; SHELL cards show the
  command in a monospace block and a "runs with your user's permissions" line. Model-path cards also
  show the confidence and the alternatives ("Did you mean: Open URL / Just chat").
  Plain Enter never approves (it's the send key). One approval = one plan; no "always allow" button — the config list is the only way to auto-approve.

**Action log**: append-only JSONL at `~/Library/Application Support/BitNetDesk/actions.jsonl`
(local only; `actions_log = false` in config disables it):

```json
{"ts": "2026-10-16T14:02:11+02:00", "text": "open spotify", "route": "fast", "tool": "app",
 "args": {"name": "Spotify"}, "risk": "changes", "commands": [["open", "-a", "/Applications/Spotify.app"]],
 "confidence": null, "decision": "approved", "result": "ok", "exit": 0, "ms": 140}
```

Logged: auto-runs, approvals, denials, failures and refusals (grounding failures). Viewer: an
"Action log" window (newest first, filter by decision) plus `python -m bitnet_desk.actions log` to
print the last N entries in the terminal.

---

## 7. Failure handling

| Situation | Behaviour |
|---|---|
| Fast path: no match | Go to the model path. |
| Model picks `chat` | Normal chat reply (current behaviour). |
| `p_tool` below threshold (start 0.7, tuned by eval) | Don't present a single action. Show "I'm not sure what you want" with the top 2 tools as buttons plus "Just chat". Even read-only tools don't auto-run when unsure. |
| Tool chosen but grounding fails (invented URL, missing path, unknown app/project) | Nothing runs. Say exactly what was wrong ("I can't find an app called 'Spotfy' — did you mean Spotify?") with suggestion buttons from `difflib`; for URLs: "Tell me the address and I'll open it." Logged as `refused`. |
| Args fail schema validation (shouldn't happen under grammar; checked anyway) | Treat as "unsure". |
| Model server down/loading | Fast path still works; otherwise "The model isn't ready — commands like 'open Safari' still work." |
| Approved action fails (non-zero exit, download error) | Show the error and output tail in the card; log `result: "error"`. No automatic retry, no automatic alternative tool. |
| User denies | Card collapses to "Denied"; logged. Nothing else happens. |
| Request needs two steps ("find X and open it") | v1 does the first step only (find); clicking a result opens it via `open_path` through the gate. |

Honesty rule from CLAUDE.md applies: the UI says "I think you want…" for model-path suggestions,
never states them as certain.

---

## 8. Eval

`evals/actions.jsonl` — one case per line:

```json
{"id": 1, "text": "open Safari", "tool": "open_app", "args": {"name": "Safari"}, "route": "fast"}
```

- `tool`: the ideal outcome (`chat` = not an action). `args`: expected values after normalisation
  (case-insensitive; paths `~`-expanded; URLs scheme-normalised). Per-arg matchers: `exact`
  (default), `contains`, or `any_of` for genuinely ambiguous cases.
- `route`: where the pipeline is **expected** to handle it — `fast` (fast path), `model` (passes the
  command-verb check, model decides), `chat` (command-verb check sends it straight to chat).
- Runs against a fixed fixture environment (fake installed-apps list, fake projects config, fake
  home folders) so results are the same on every machine. **Nothing is executed** — eval stops at
  the Plan.

**Scoring, reported separately:**

1. *Fast path* (deterministic, runs in **pytest**, no model):
   - coverage = correct ÷ cases with `route: fast`
   - precision = correct ÷ matched (tool + args); target 100 %
   - false triggers = any fast match on a case whose `route` isn't `fast` (target: 0)
2. *Command-verb check* (deterministic, pytest): for every case the fast path doesn't take, does the
   check send it to the model or to chat as `route` says? Trap phrases on both sides.
3. *Model path* (all 40 cases sent to the router **ignoring** fast path and verb check, to measure the
   2B model itself; `python -m bitnet_desk.actions eval`, needs the real server, not in pytest):
   - tool accuracy, args accuracy (given right tool), `chat` accuracy (no false actions)
   - grounding catches (model invented a value and §5 rejected it — safe, reported apart)
   - calibration: accuracy per `p_tool` bucket → pick the "unsure" threshold
   - latency p50/p95
4. *Pipeline* (fast path → verb check → model): end-to-end accuracy, gate misses, and **unsafe
   rate** = cases where a wrong action would be shown as a confident plan.

Results go to `docs/evals.md` with date, commit, model file and threads, so a future model swap is a
one-command comparison.

### The cases

Fixture: apps Safari, Spotify, Music, Notes, Terminal, Visual Studio Code; project `bitnet-desk`
(alias "bitnet desk") at `~/Projects/bitnet-desk`; Calculator; folders ~/Documents, ~/Downloads, ~/Desktop.

| # | Text | Expected tool | Expected args | Route |
|---|---|---|---|---|
| 1 | open Safari | open_app | name=Safari | fast |
| 2 | open spotify | open_app | name=Spotify | fast |
| 3 | launch Visual Studio Code | open_app | name=Visual Studio Code | fast |
| 4 | open vs code | open_app | name=Visual Studio Code (alias) | fast |
| 5 | please open notes | open_app | name=Notes | fast |
| 6 | open github.com | open_url | url=https://github.com | fast |
| 7 | open https://news.ycombinator.com | open_url | url=https://news.ycombinator.com | fast |
| 8 | open ~/Documents | open_path | path=~/Documents | fast |
| 9 | open my downloads folder | open_path | path=~/Downloads | fast |
| 10 | start project bitnet-desk | start_project | name=bitnet-desk | fast |
| 11 | work on bitnet desk | start_project | name=bitnet-desk | fast |
| 12 | open the bitnet-desk project | start_project | name=bitnet-desk | fast |
| 13 | download https://example.com/report.pdf | download | url=https://example.com/report.pdf | fast |
| 14 | save https://example.com/a.zip to downloads | download | url=https://example.com/a.zip | fast |
| 15 | find files named invoice | find_files | query=invoice | fast |
| 16 | where's my resume? | find_files | query contains resume | fast |
| 17 | what time is it | now | – | fast |
| 18 | what's the date today? | now | – | fast |
| 19 | run `git status` | run_shell | command=git status | fast |
| 20 | $ ls -la ~/Desktop | run_shell | command=ls -la ~/Desktop | fast |
| 21 | play some music | open_app | name any_of [Music, Spotify] | model |
| 22 | launch the terminal for me | open_app | name=Terminal | fast |
| 23 | show me what's on my desktop | open_path | path=~/Desktop | model |
| 24 | start up my bitnet thing | start_project | name=bitnet-desk | model |
| 25 | download the csv at https://x.org/data.csv | download | url=https://x.org/data.csv | model |
| 26 | find the PDFs about taxes I made last year | find_files | query contains tax | model |
| 27 | open the report I wrote about Q3 | find_files | query contains Q3 | model |
| 28 | show me the weather in Paris | chat | – (no URL given; must not invent one) | model |
| 29 | download the latest python installer | chat | – (no URL; must not invent) | model |
| 30 | run a check of my disk space | chat | – (no literal command; no model-written shell) | model |
| 31 | start project moonbase | chat | – (unknown project → no action; reply lists projects) | model |
| 32 | find me a good pancake recipe | chat | – (passes verb check; model must say chat) | model |
| 33 | start thinking about a name for my cat | chat | – (passes verb check) | model |
| 34 | show me how recursion works in Python | chat | – (passes verb check) | model |
| 35 | play devil's advocate: is remote work good? | chat | – (passes verb check) | model |
| 36 | how do I download a file with curl? | chat | – (verb check → chat, no model call) | chat |
| 37 | what does "open a can of worms" mean? | chat | – (verb check → chat) | chat |
| 38 | write a haiku about autumn | chat | – | chat |
| 39 | what is the capital of France | chat | – | chat |
| 40 | is it friday yet? | now | – | fast |
| 41 | can you open calculator for me | open_app | name=Calculator | fast |
| 42 | could you please launch spotify | open_app | name=Spotify | fast |
| 43 | will you open my documents folder for me? | open_path | path=~/Documents | fast |
| 44 | would you open safari please, thanks | open_app | name=Safari | fast |
| 45 | please could you open notes for me | open_app | name=Notes | fast |
| 46 | hey, can you tell me what time it is? | now | – | fast |
| 47 | can you explain how to open a file in Python? | chat | – (polite wrapper around a non-command) | chat |
| 48 | could you write me a poem for my mom | chat | – (polite wrapper around a non-command) | chat |
| 49 | can you open up about your feelings? | chat | – (polite + command verb, but not a command) | model |
| 50 | please tell me a joke | chat | – (polite wrapper around a non-command) | chat |
| 51 | what time is it today | now | – | fast |
| 52 | whats todays date? | now | – | fast |
| 53 | what day of the week is it | now | – | fast |
| 54 | what year is it | now | – | fast |
| 55 | tell me the time | now | – | fast |
| 56 | do you know what time it is? | now | – | fast |
| 57 | what's the current date and time | now | – | fast |
| 58 | today's date please | now | – | fast |
| 59 | what time is the next SpaceX launch? | chat | – (about a time, not the current time) | chat |
| 60 | what time is it in Tokyo? | chat | – (another time zone: not the local clock) | chat |
| 61 | how many days until Christmas? | chat | – (date arithmetic, not the current date) | chat |
| 62 | what can you do? | help | – | fast |
| 63 | help | help | – | fast |
| 64 | could you list your commands | help | – | fast |
| 65 | can you help me write a cover letter? | chat | – ('help' inside a request isn't the help command) | chat |

Cases 41–61 were added after the first try-out (2026-10-16): polite wrappers around commands and
non-commands, many phrasings of "what time/date is it", and time questions that must *not* be
answered from the local clock. Date/time questions are always answered by the fast path from the
system clock, never by the model.

If a fast rule is widened later, the case's `route` changes with it and the eval prints
per-case diffs. The set grows with every new tool; a tool doesn't ship without cases.

---

## 9. Build order (each step small, tested, one commit)

1. **Permission system + action log + fast path** with `open_app`, `open_path`, `now`: `Risk`, `Tool`,
   `Plan`, the gate with `auto_approve`, JSONL log + `python -m bitnet_desk.actions log`,
   `[actions]`/`[aliases]` config, eval file + fast-path scoring in pytest. Tests use a fake runner;
   nothing is launched for real.
2. **Approval card in the UI**; chat box routes through the fast path. → *stop here for a try-out
   in the app.*
3. `start_project` (+ `[projects]` config), `open_url`, `download`, `find_files`, `run_shell`, with a
   network-guard test (non-network tools fail if they open a non-localhost socket).
4. **Model router** (`-np 2`, RAM measured), command-verb check, grounding, failure handling, eval
   runner → `docs/evals.md` with fast-path and model-path accuracy reported separately.

Then: action log window, README section on actions and the permission model (README privacy wording
updated when `open_url`/`download` land, in step 3).
