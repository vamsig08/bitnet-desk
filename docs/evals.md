# Evals

Action eval: `python -m bitnet_desk.actions eval --write` (cases in `evals/actions.jsonl`, design in `docs/actions-design.md` §8). Newest first.

## 2026-10-17 — commit 678af4e, ggml-model-i2_s.gguf, 4 threads, 102 cases, threshold 0.7

### Fast path (rules, no model)
- Coverage of `route: fast` cases: 65/65 (100%)
- Precision when it matches: 65/65 (100%); false triggers: 0
- Command-verb check on the rest: 37/37 (100%) sent where expected

### Model path (every case sent to the router, ignoring fast path and verb check)
- Tool choice, action cases: 53/73 (73%)
- Tool + arguments, action cases: 50/73 (68%) as picked, 47/73 (64%) offered after grounding/planning
- Chat cases kept as chat: 17/29 (59%) by the model, 26/29 (90%) after grounding (9 invented actions caught)
- Overall (what the user is offered): 73/102 (72%)
- Latency per router call: p50 1087 ms, p95 1427 ms

Calibration (confidence of the model's pick → tool+args right; threshold 0.7):

| confidence | cases | right |
|---|---|---|
| 0.0–0.5 | 8 | 1 |
| 0.5–0.7 | 11 | 2 |
| 0.7–0.9 | 16 | 7 |
| 0.9–1.0 | 47 | 40 |

**Unsafe picks** (a wrong action offered to the user): 3
- #28 'show me the weather in Paris': expected chat, offered `Search your files for “Paris weather”` (p=0.73) — shown as a card (asks first)
- #32 'find me a good pancake recipe': expected chat, offered `Search your files for “good pancake recipe”` (p=0.99) — shown as a card (asks first)
- #102 'play some jazz': expected chat, offered `Play (Spotify)` (p=0.98) — shown as a card (asks first)

Other misses (no action offered, or chat instead of an action):
- #2 'open spotify': expected open_app, model said open_app {"name": "Spotify"} → chat
- #4 'open vs code': expected open_app, model said open_app {"name": "vs code"} → chat
- #6 'open github.com': expected open_url, model said open_app {"name": "GitHub"} → refused
- #16 "where's my resume?": expected find_files, model said find_files {"query": "my resume"} → chat
- #20 '$ ls -la ~/Desktop': expected run_shell, model said find_files {"query": "/Desktop", "kind": "folder"} → chat
- #23 "show me what's on my desktop": expected open_path, model said open_app {"name": "Finder"} → chat
- #27 'open the report I wrote about Q3': expected find_files, model said start_project {"name": "Q3 Report"} → refused
- #40 'is it friday yet?': expected now, model said chat {} → chat
- #43 'will you open my documents folder for me?': expected open_path, model said open_app {"name": "Finder"} → chat
- #45 'please could you open notes for me': expected open_app, model said start_project {"name": "notes"} → refused
- #62 'what can you do?': expected help, model said chat {} → chat
- #66 'pause': expected media, model said media {"action": "pause"} → chat
- #69 'resume spotify': expected media, model said playlist {"name": "focus"} → chat
- #70 'next song please': expected media, model said media {"action": "play"} → chat
- #71 'skip this track': expected media, model said playlist {"name": "focus"} → chat
- #72 'go back a song': expected media, model said playlist {"name": "focus"} → chat
- #75 'turn it down a bit': expected volume, model said volume {"action": "down"} → chat
- #81 'turn on do not disturb': expected dnd, model said volume {"action": "mute"} → chat
- #82 'dnd off': expected dnd, model said chat {} → chat
- #83 'take a screenshot': expected screenshot, model said open_app {"name": "Safari"} → chat
- #84 'screenshot this window': expected screenshot, model said open_app {"name": "Screencap"} → chat
- #85 'grab a screenshot of an area': expected screenshot, model said open_app {"name": "Screencapture"} → chat
- #86 'lock my mac': expected lock_screen, model said open_app {"name": "Keychain Access"} → chat
- #87 'please lock the screen': expected lock_screen, model said volume {"action": "mute"} → chat
- #88 'empty the trash': expected empty_trash, model said open_app {"name": "Trash"} → refused
- #91 '/skip to the next one': expected media, model said playlist {"name": "focus"} → chat

### Pipeline (fast path → verb check → model)
- End-to-end right: 96/102 (94%); actions missed because the verb check sent them to chat: 0; unsafe picks reaching the user: 3

## 2026-10-16 — commit b398bc0, ggml-model-i2_s.gguf, 4 threads, 65 cases, threshold 0.7

### Fast path (rules, no model)
- Coverage of `route: fast` cases: 39/39 (100%)
- Precision when it matches: 39/39 (100%); false triggers: 0
- Command-verb check on the rest: 26/26 (100%) sent where expected

### Model path (every case sent to the router, ignoring fast path and verb check)
- Tool choice, action cases: 39/45 (87%)
- Tool + arguments, action cases: 37/45 (82%) as picked, 37/45 (82%) offered after grounding/planning
- Chat cases kept as chat: 14/20 (70%) by the model, 18/20 (90%) after grounding (4 invented actions caught)
- Overall (what the user is offered): 55/65 (85%)
- Latency per router call: p50 828 ms, p95 1173 ms

Calibration (confidence of the model's pick → tool+args right; threshold 0.7):

| confidence | cases | right |
|---|---|---|
| 0.0–0.5 | 5 | 2 |
| 0.5–0.7 | 2 | 0 |
| 0.7–0.9 | 9 | 5 |
| 0.9–1.0 | 33 | 30 |

**Unsafe picks** (a wrong action offered to the user): 2
- #30 'run a check of my disk space': expected chat, offered `Search your files for “disk space”` (p=0.83) — shown as a card (asks first)
- #32 'find me a good pancake recipe': expected chat, offered `Search your files for “good pancake recipe”` (p=0.99) — shown as a card (asks first)

Other misses (no action offered, or chat instead of an action):
- #16 "where's my resume?": expected find_files, model said find_files {"query": "my resume"} → chat
- #20 '$ ls -la ~/Desktop': expected run_shell, model said open_path {"path": "~/Desktop"} → chat
- #21 'play some music': expected open_app, model said chat {} → chat
- #23 "show me what's on my desktop": expected open_path, model said open_app {"name": "Finder"} → chat
- #27 'open the report I wrote about Q3': expected find_files, model said start_project {"name": "Q3 Report"} → refused
- #40 'is it friday yet?': expected now, model said chat {} → chat
- #43 'will you open my documents folder for me?': expected open_path, model said open_path {"path": "~/Documents"} → chat
- #45 'please could you open notes for me': expected open_app, model said start_project {"name": "net notes"} → chat

### Pipeline (fast path → verb check → model)
- End-to-end right: 60/65 (92%); actions missed because the verb check sent them to chat: 0; unsafe picks reaching the user: 2
