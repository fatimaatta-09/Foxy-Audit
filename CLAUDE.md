I have made some fixes yet check if those are working at your end# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

The PyQt6 **desktop client** for the Foxy Audit platform: an animated pixel-art fox ("compliance
officer" mascot) that lives at the bottom of the screen, reacts to system telemetry, and exposes a
chat copilot. The desktop app talks to the backend platform two ways: a local UDP bridge from the
`@foxy.audit` SDK, and an HTTPS health check against `backend_url`.

**Branch note:** on the current **`foxy-skeleton`** branch the whole three-tier system is present, not
just the desktop app — `backend/` (the FastAPI / PostgreSQL service, with `chain.py`, `worker.py`,
`gemini.py`, `routers/`, `scripts/`, `docker-compose.yml`), `sdk/` (the `foxy-audit` pip package), and
`demo/`. So backend work *can* be done here. (The older `foxy-f` / `foxy-a` branches carried only the
desktop app — see "Repository branches" below.)

Note: the product is "Foxy Audit", but the code still uses the older "OmniAware Fox" / `omni_fox`
naming internally (including the QSettings org/app keys `OmniAwareFox` / `DesktopPet`).

## Running & commands

No dependency manifest, test suite, or lint config exists. Python 3.13+ (compiled `.pyc` are
cp313/cp314).

```bash
# Install deps (no requirements.txt — these are the imports actually used)
pip install PyQt6 psutil pynput requests
#   optional, only for window-following on non-Windows:
#     macOS → pip install pyobjc-framework-Quartz      Linux → pip install python-xlib
#   only for regenerating the spritesheet → pip install Pillow

# Run the app (it os.chdir()s to its own directory on startup)
python omni_fox.py

# Most UI widget modules have a standalone __main__ preview harness with a fake fox —
# use these to iterate on a widget without launching the whole app:
python clay_chat_popup.py        # chat popup preview

# Rebuild the sprite atlas (needs Pillow + the external ARTIFACT_DIR hardcoded in the file,
# which won't exist on your machine — it falls back to expanding the current atlas in place):
python build_16frame_atlas.py
```

There are no automated tests. Verify changes by running the app or a module's `__main__` preview.

## Architecture

`OmniAwareFox(QWidget)` in `omni_fox.py` is the center of everything — a frameless, always-on-top,
translucent `Tool` window fixed to a single 192×208 sprite cell. It owns the state machine, the
timers, the spritesheet frame cache, the background threads, the system tray, and all child overlays.

**Spritesheet animation.** One atlas (`ultimate_fox_spritesheet.png`), cells 192×208, 8 columns.
Each *action* occupies `ROWS_PER_ACTION` (3) rows = `FRAMES_PER_ACTION` (24) frames. The `ROW_*`
constants are `action_index * 3` offsets into the atlas. `_get_frame(row, frame_idx, flip)` crops and
caches frames. `build_16frame_atlas.py` regenerates the atlas from source 4×4 sheets.

**State machine.** A `self.state` string plus `current_row`/`current_frame`. `_set_state(name, row,
duration)` is the single transition point and is gated by `reaction_cooldown`. States in
`_TIMED_STATES` auto-expire back to `IDLE` once `reaction_timeout` passes (checked each animation
tick). IDLE has its own breathing/dozing sub-behavior, periodic autonomous "idle-break" poses, and
periodic compliance-tip speech bubbles. Two `QTimer`s drive it: animation at 100 ms, roaming at
150 ms. Roaming walks the fox along the bottom of the screen unless `_user_placed` (user dragged it)
or the chat is open.

**Background threads — all `QThread`, all communicate via Qt signals (never touch widgets directly):**
- `GlobalSensors` — pynput keyboard/mouse listeners (`typing_signal` / `scrolling_signal`) plus a
  psutil CPU/RAM/battery poll every 3 s (`hardware_update_signal`). High CPU/RAM → CRYING; low
  battery → ALERTING.
- `SDKBridgeListener` (`sdk_bridge.py`) — UDP socket on `127.0.0.1:9999`. Parses JSON datagrams the
  `@foxy.audit` SDK decorator emits: `hash_ok` → `hash_confirmed` (green flash), `policy_breach` →
  `policy_breach` (red alert + auto-opens chat with the breach details). This is the main live
  integration point with the wider platform.
- `StartupHealthWorker` — one-shot `GET {backend_url}/v1/health` with `Bearer {org_key}`; drives the
  "connected / unreachable" reaction. Created only when both `backend_url` and `org_key` are set.

`closeEvent` does explicit, ordered thread shutdown; QThread subclasses disconnect their own signals
on finish to avoid the Qt6 leak where a finished thread stays alive via a captured lambda.

**Transparent child overlays drawn on top of the sprite** (both `WA_TransparentForMouseEvents`):
- `SecurityOverlay` (`security_overlay.py`) — radial-gradient glow rings; `flash_green/red/amber`.
- `EyeOverlay` (`eye_tracker.py`) — paints tiny pixel pupils that drift toward the cursor, shown
  only during calm states (`EYE_VISIBLE_STATES`).

**Chat copilot.** `ChatPopup` (`clay_chat_popup.py`) is a frameless themed window. AI calls route
through `ai_providers.call_ai()`, which dispatches by provider (anthropic / openai / ollama /
lmstudio / custom) and is run off the UI thread on `_AICallWorker`; failures fall back to a canned
reply so a missing key never breaks the UI. The persona is `FOX_SYSTEM_PROMPT`. `open <path>` is
handled locally via `window_tracker.open_path` before hitting the AI. History is capped at 20 turns.

**Cross-platform window tracking.** `window_tracker.py`'s `get_active_window_rect()` uses Windows
ctypes / macOS Quartz / Linux Xlib and returns `None` if the optional dep is missing — callers treat
`None` as "fall back to roaming" rather than crashing.

## Theming — the key cross-cutting concept

`fox_settings.py` defines `THEMES`, **14 complete design-token dicts that all expose the exact same
keys** (palette, border, radius, shadow model, fonts). `FoxSettings` wraps `QSettings` (OS-native
persistent storage). `FoxSettings.theme_tokens()` returns the active theme merged with defaults so
optional keys are always present.

**All UI is 100% token-driven — no widget ever special-cases a particular theme.** Every themed
widget builds its stylesheet from tokens and exposes an `apply_theme(tokens)` / `set_tokens(tokens)` /
`apply_tokens(tokens)` method to re-skin in place; `SettingsDialog` emits `theme_changed` /
`settings_saved`, which are wired to call those methods live. Shadows are built from the `shadow_*`
tokens via `QGraphicsDropShadowEffect` (`shadow_blur: 0` → hard neobrutalist offset; coloured
`shadow_color` → neon glow). **To add a theme, add one fully-populated entry to `THEMES`** and
everything re-skins automatically — do not branch on theme names anywhere else.

## Conventions when extending the UI

- New themed widgets follow the token-driven pattern above and ship a standalone `if __name__ ==
  "__main__"` preview harness with a fake fox (use `clay_chat_popup.py` as the template).
- Use `resource_path()` (in `omni_fox.py`) for any bundled asset so paths work under PyInstaller
  (`sys._MEIPASS`).
- Do real work on a `QThread` and surface results via signals; keep the UI thread free.

## Repository branches (currently non-obvious)

The full desktop app lives on **`foxy-f`** (and on local `foxy-a`, which is `foxy-f` plus the
Compliance Command Center dashboard, `dashboard.py`). `main` and the remote `origin/foxy-a` contain
only `README.md`. Don't assume `main` is the source of truth — confirm with `git ls-tree -r <branch>0
--name-only` before branching or comparing.
