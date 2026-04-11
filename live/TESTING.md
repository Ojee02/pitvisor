# pitvisor-live test playbook

Send this file back to me during (or just before) an F1 session — I'll walk through each step against the live service.

**What we're testing:** the `pitvisor-live` backend service on disinteg + the `/live` page on pitvisor.ojee.net, end to end, against a real F1 session. The last time this was touched, everything worked against synthetic data but nothing has been tested against the actual SignalR feed.

---

## Before we start

Tell me:

1. **Which session are we testing?** e.g. "Miami GP Practice 1" — I need the event name and session type so I can confirm the worker's schedule detection matched.
2. **What time does it start (UTC)?** — I want to verify the pre-window (`PRE_WINDOW_MINUTES = 15`) kicks in correctly.
3. **Is this the very first live session since Phase 9 deployed?** — if yes, expect parse warnings on the first few minutes. Normal. We fix them on the fly.
4. **What device are you testing from?** — desktop browser + iOS Safari, ideally, because we've been burned by iOS once this week already.

---

## Phase 1 — Pre-session check (run 20+ minutes before the session starts)

All of these should work even when no session is live.

### 1.1 Service is up

```bash
curl -s https://pitvisor-api.ojee.net/live/health
# expect: {"live_active":false,"status":"UP"}
```

### 1.2 Next session is detected

```bash
curl -s https://pitvisor-api.ojee.net/live/status | python3 -m json.tool
```

**Check:**
- `active: false` — should still be false if session is >15 min away
- `next_session.event_name` — should match the session you're testing
- `next_session.session` — should match the session type (e.g. "Practice 1", "Race")
- `next_session.start_utc` — sanity check the timestamp

### 1.3 Config is what we expect

```bash
curl -s https://pitvisor-api.ojee.net/live/config | python3 -m json.tool
```

**Check:** all the knobs match their defaults unless we've overridden something. If `replay_file` is non-null, **STOP** — replay mode is leaking into production, you need to unset `PITVISOR_LIVE_REPLAY` from the systemd unit and restart.

### 1.4 Frontend offline view

Open https://pitvisor.ojee.net/live

**Check:**
- ⬜ Page loads without blank screen or JS errors (open devtools console)
- ⬜ "No live session" title shows
- ⬜ "Next" card shows the correct event name, session type, and a live countdown (should tick down every second)
- ⬜ "← back to analysis" link works
- ⬜ No CORS errors in devtools (should see successful GETs to `/live/status`, `/live/stream`)
- ⬜ On iOS Safari: layout doesn't blow up (panels are correctly sized, nothing clipping off-screen)

### 1.5 Worker logs tail (ssh into disinteg)

```bash
sshpass -p 'disinteg#123' ssh disinteg@100.118.201.77 'sudo journalctl -u pitvisor-live -f'
```

**Expected idle output:**
```
pitvisor.live.worker: live worker started
pitvisor.live.main: ── pitvisor-live config ──
...
```

Then silence. No error lines. Leave this running — we'll watch it through the session.

---

## Phase 2 — Worker activation (around T-15 minutes)

Roughly 15 minutes before the scheduled start (per `PRE_WINDOW_MINUTES`), the worker should detect the session and begin connecting.

### 2.1 Worker logs — activation

In the `journalctl -f` window you should see:

```
pitvisor.live.worker: session active: <Event Name> — <Session Name>
pitvisor.live.worker: loaded track outline (<N> points, <M> corners)
pitvisor.live.client: Starting FastF1 live timing client [v3.8.x]
```

**If you DON'T see this by T-10 minutes:**
```bash
curl -s https://pitvisor-api.ojee.net/live/status | python3 -m json.tool
```
- If `active: false` still and `next_session` is still correct → scheduler isn't firing. Schedule detection bug. Tell me.
- If `active: false` and `next_session` has changed → the original session may have been cancelled or rescheduled. Tell me the new `next_session`.

### 2.2 SignalR connection established

A few seconds after activation you should see:

```
SignalR            INFO  Connection established
```

**If instead you see:**
- `Connection closed` repeatedly → auth failure or network issue. Check `get_auth_token` is working. Tell me the full traceback.
- `signalrcore.messages.CompletionMessage` errors → F1 changed their message format. Tell me the exact error.
- No change for >60s → timeout kicks in and client exits. Worker should restart it (new in this build). Watch for "SignalR client not alive during active session — restarting".

### 2.3 Track outline visible on the UI

Refresh https://pitvisor.ojee.net/live

**Check:**
- ⬜ LIVE badge (red, pulsing) replaces "Offline"
- ⬜ Session name and type show in the header
- ⬜ Track map panel shows the circuit outline (dark grey path)
- ⬜ Corner numbers labeled around the track
- ⬜ Timing table shows "waiting for timing data…" OR starts showing drivers

---

## Phase 3 — Data flowing (during the session)

### 3.1 Backend snapshot sanity

```bash
curl -s https://pitvisor-api.ojee.net/live/snapshot > /tmp/snap.json
python3 -c "
import json
d = json.load(open('/tmp/snap.json'))
print('session:', d['session'].get('name'), '-', d['session'].get('type'))
print('active:', d['session'].get('active'))
print('lap:', d['session'].get('lap'), '/', d['session'].get('total_laps'))
print('track_status:', d['track_status'])
print('weather:', d['weather'])
print('driver_count:', len(d['drivers']))
print('race_control_count:', len(d['race_control']))
if d['drivers']:
    sample = d['drivers'][0]
    print()
    print('sample driver:', sample.get('tla'), sample.get('number'))
    for k in ['position','lap','last_lap','best_lap','gap_leader','interval','tire_compound','tire_age','in_pit','retired','speed','rpm','gear','throttle','brake','drs','x','y','color','team']:
        print(f'  {k}: {sample.get(k)}')
"
```

**Check the sample driver:**
- ⬜ `position` is a number 1-20
- ⬜ `last_lap` is a time string like `1:32.412` (only after they've completed a lap)
- ⬜ `tire_compound` is one of SOFT/MEDIUM/HARD/INTERMEDIATE/WET
- ⬜ `tire_age` is a small integer (lap count on current stint)
- ⬜ `speed`, `rpm`, `gear`, `throttle`, `brake`, `drs` are all populated (numbers, not null) — this is the single most important check because it tells us CarData.z is decoding correctly
- ⬜ `x`, `y` are numbers (not null) — Position.z decoding works
- ⬜ `color` starts with `#` — team color decoded
- ⬜ `team` is the team name

### 3.2 Track map — live positions

On https://pitvisor.ojee.net/live:
- ⬜ 20 colored dots appear on the track map
- ⬜ Dots are moving (positions update ~1 Hz, CSS glides them smoothly)
- ⬜ Driver TLAs (VER, LEC, etc.) float next to each dot
- ⬜ When a driver pits, their dot goes semi-transparent
- ⬜ When a driver retires, their dot turns dark grey
- ⬜ Dots aren't all clumped in one corner or off-screen (sign of a rotation mismatch — see "Common failures" below)

### 3.3 Timing table

- ⬜ Rows sorted by position
- ⬜ Position number left column, TLA + team color stripe, gap, interval, last lap, best lap, tire
- ⬜ Current leader's gap shows blank or "—" or "LAP 1", not garbage
- ⬜ Last lap time turns green when a driver sets a personal best
- ⬜ Last lap time turns purple when a driver sets a session best
- ⬜ Tire badge shows correct compound (S/M/H/I/W) in correct color

### 3.4 Telemetry — the hardest test

1. Click a driver dot on the track map (e.g. VER)
2. ⬜ That driver's TLA in the timing table row gets a blue highlight
3. ⬜ Telemetry panel bottom shows a "readout card" with VER + current speed/gear/throttle
4. ⬜ Six tile charts (Speed, RPM, Gear, Throttle, Brake, DRS) start drawing a line for VER
5. ⬜ Lines update every ~250ms — no stalled points
6. ⬜ X-axis is "-30s" to "0s" (current time at right)
7. Click LEC (or any other driver)
8. ⬜ Both drivers overlay on all 6 charts in their respective team colors
9. Click a 3rd driver
10. ⬜ All 3 overlay
11. Try to click a 4th driver
12. ⬜ Works (max 4)
13. Try to click a 5th driver
14. ⬜ Click does nothing (chip is disabled, cursor changes)
15. Click VER again to deselect
16. ⬜ VER's line disappears from all 6 charts; readout card disappears
17. Hover over a chart line
18. ⬜ Tooltip shows "X.Xs ago" and the value per driver (TLA, not number)

**The critical check:** Telemetry tiles are where the most can go wrong. If Speed shows flat zero or empty lines while CarData.z appears to be arriving (logs show `CarData.z` dispatches), the channel ID mapping may be wrong (expected: `"0"`→RPM, `"2"`→Speed, `"3"`→Gear, `"4"`→Throttle, `"5"`→Brake, `"45"`→DRS). Tell me and I'll check.

### 3.5 Race control feed

The side panel should show messages as they come in during the session:
- ⬜ "GREEN LIGHT" or equivalent start-of-session message
- ⬜ Yellow flag messages (if any) have a yellow left-border
- ⬜ SC/VSC messages (if any) have an orange left-border
- ⬜ Timestamps render correctly (HH:MM:SS, UTC)
- ⬜ Feed scrolls — latest messages at top

### 3.6 Weather card

- ⬜ Air temp, track temp, humidity, pressure, wind speed, rain
- ⬜ Values change over time (pressure is usually stable, track temp drifts)
- ⬜ Rain field shows "Yes" during actual rain (otherwise "No")

### 3.7 Session clock

- ⬜ Header shows either a lap counter (`LAP 12/57`) for races, or a time clock (`1:23:45`) for practice/qualifying
- ⬜ Track status chip changes color during yellow flags / SC / red (test by waiting for one)

---

## Phase 4 — Stress / weird cases

If the session is going well and we have time:

### 4.1 Disconnect test

1. Put your laptop to sleep for 2 minutes
2. Wake it up
3. Refresh the tab
4. ⬜ Reconnects immediately, shows current state (SSE auto-reconnect works)

### 4.2 Navigate away and back

1. Click "← back to analysis"
2. Wait 10 seconds
3. Click "Live Timing" link in the sidebar
4. ⬜ /live page reloads cleanly, reconnects, shows current live state

### 4.3 Slow network (devtools)

1. Chrome devtools → Network → throttle to "Slow 3G"
2. Reload /live
3. ⬜ Page still loads within 30s and shows data (maybe slower updates)

### 4.4 Mobile (iOS Safari)

Open pitvisor.ojee.net/live on an iPhone:
- ⬜ Header is readable (stacked vertically at that breakpoint)
- ⬜ Track map is tall and usable — circuit outline visible, dots moving
- ⬜ Timing table is scrollable horizontally if it overflows
- ⬜ Telemetry tiles render (recharts charts are not blank — this was our earlier iOS bug)
- ⬜ Can tap a driver on the track map to select it
- ⬜ Portrait and landscape both work

---

## Phase 5 — Post-session

### 5.1 Graceful shutdown

After `POST_WINDOW_HOURS = 3` hours past session start, the worker should:
- ⬜ Stop the SignalR client (log: "session ended")
- ⬜ STATE clears
- ⬜ `/live/status` returns `active: false`
- ⬜ Frontend automatically falls back to the offline view on next snapshot

### 5.2 Recording saved

```bash
sshpass -p 'disinteg#123' ssh disinteg@100.118.201.77 'ls -lh /tmp/pitvisor-live-*.txt | tail -5'
```

or if RECORDING_DIR was set:
```bash
sshpass -p 'disinteg#123' ssh disinteg@100.118.201.77 'ls -lh /home/disinteg/pitvisor/backend/recordings/ 2>/dev/null'
```

- ⬜ A new recording file exists, 10+ MB, dated during the session
- Save the filename — we can replay it later via `PITVISOR_LIVE_REPLAY` to debug any parse bugs that showed up.

---

## Common failures and what they mean

| Symptom | Likely cause | Tell me |
|---|---|---|
| Track outline loads but no dots ever appear | Position.z not decoding OR positions arriving but not in state.drivers | `curl /live/snapshot` and check if driver entries have `x`/`y` fields |
| Dots cluster in corner / move wrong direction | Track rotation angle mismatch between outline and live positions | The current year+round and whether the cached track came from a different year |
| Timing table shows drivers but no lap times | TimingData payload shape changed | Full error log from `journalctl -u pitvisor-live -n 200` |
| Telemetry charts draw but values are flat/wrong | CarData.z channel ID mapping off | Raw channel snapshot — `curl /live/snapshot` and grep for `speed`/`rpm`/`gear` |
| LIVE badge never appears despite session starting | Scheduler window mismatch (wrong timezone?) | `curl /live/status` + current UTC time |
| "Connection closed" in SignalR logs repeatedly | Auth failure (F1 added auth mid-season before) | Traceback from `journalctl -u pitvisor-live -n 300` |
| Recharts tiles blank on iOS Safari only | Another percentage-height bug in Live.module.css | Screenshot from iOS + the panel that's broken |
| CORS errors in browser devtools | Nginx stripped CORS headers on SSE | Response headers from `curl -I -H "Origin: https://pitvisor.ojee.net" https://pitvisor-api.ojee.net/live/status` |

---

## Quick command reference

```bash
# tail live service logs
sshpass -p 'disinteg#123' ssh disinteg@100.118.201.77 'sudo journalctl -u pitvisor-live -f'

# restart live service (e.g. after applying a fix)
sshpass -p 'disinteg#123' ssh disinteg@100.118.201.77 'echo "disinteg#123" | sudo -S systemctl restart pitvisor-live'

# current snapshot
curl -s https://pitvisor-api.ojee.net/live/snapshot | python3 -m json.tool

# current config (verify knob overrides)
curl -s https://pitvisor-api.ojee.net/live/config | python3 -m json.tool

# schedule (what the worker thinks is next)
curl -s https://pitvisor-api.ojee.net/live/status | python3 -m json.tool

# stream 10 seconds of SSE events (first event = latest snapshot)
timeout 10 curl -sN https://pitvisor-api.ojee.net/live/stream | head -c 4000
```

---

## Dev workflow: replay a past session

You don't have to wait for a real session to test the UI. The `live.recorder`
module downloads any past session's live-timing data and writes a JSONL file
that `live.replay` can drive through the exact same pipeline.

```bash
# List what's available for a year
python -m live.recorder 2025 --list

# Show the download size WITHOUT downloading (HEAD requests only)
python -m live.recorder 2025 18 R --size-only

# Download (prompts for confirmation after showing size)
python -m live.recorder 2025 18 R

# Or: non-interactive
python -m live.recorder 2025 18 R --yes

# Replay it — the service runs exactly as if this were a live session
PITVISOR_LIVE_REPLAY=recordings/2025_Singapore_Grand_Prix_Race.jsonl \
PITVISOR_LIVE_REPLAY_SPEED=10 \
python live_main.py
```

- `--speed 0` = as fast as possible (useful for profiling, not UI testing)
- `--speed 1` = real-time (1 hour session = 1 hour replay)
- `--speed 10` = 10× (good balance for UI checks)
- `PITVISOR_LIVE_REPLAY_LOOP=1` = loop back to start when the file ends

Typical sizes (Singapore 2025 Race is the largest: ~2h40m, 87k records):

| Session type | Wire bytes (approx) | Duration |
|---|---|---|
| Practice 1/2/3 | 15-25 MB | ~1h |
| Qualifying | 10-20 MB | ~1h |
| Sprint | 5-10 MB | ~30m |
| Race | 20-35 MB | ~2h |

The JSONL on disk is larger (~4-5×) than wire bytes due to JSON expansion of
the `.z` topics. A typical race recording is ~100 MB of JSONL.

To run the **frontend** against a local replay-mode backend:

```bash
# in a second shell, in the pitvisor frontend repo
REACT_APP_BACKEND=http://localhost:5101 npm start
# then visit http://localhost:3000/live
```

---

## How to use this file

When the session is 20-30 minutes away, send me a message like:

> "Starting the pitvisor-live test. Session: Miami GP Practice 1, 2026-05-01 16:30 UTC. First real session. Desktop + iPhone. Tailing journalctl. Paste [TESTING.md](live/TESTING.md)"

And paste (or just link) this file. I'll walk us through each phase, help interpret errors, and push fixes live if anything breaks. Worst case we restart the service mid-session and lose a minute of data — better than missing the whole session.

*(This playbook lives at `live/TESTING.md` in the pitvisor backend repo. Update it after the first session once we know which checks actually catch bugs and which were just paranoia.)*
