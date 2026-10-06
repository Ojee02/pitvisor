# pitvisor replay test playbook

The SignalR live feed this service was originally built around no longer
works, so the live path is gone. Everything below tests the replay player.

Base URL: `https://pitvisor-api.ojee.net` (nginx strips the `/live/` prefix
before proxying to `127.0.0.1:5101`).

## 1. Service is up

```bash
curl -s https://pitvisor-api.ojee.net/live/health
# expect: {"service":"replay","status":"UP"}
```

## 2. Library lists recordings

```bash
curl -s https://pitvisor-api.ojee.net/live/replays | python3 -m json.tool
# expect: one entry per recordings/*.jsonl, with year/event/session/size
```

No `?key=` — the replay endpoints are public.

## 3. Schedule for the download picker

```bash
curl -s "https://pitvisor-api.ojee.net/live/replays/schedule?year=2025" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(len(d["events"]),"events")'
```

## 4. Start a session

```bash
SID=$(curl -s -X POST https://pitvisor-api.ojee.net/live/replays/session/start \
  -H 'Content-Type: application/json' \
  -d '{"name":"2025_Singapore_Grand_Prix_Race.jsonl","speed":20,"loop":true}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
echo $SID
```

## 5. Watch it come up

```bash
curl -s https://pitvisor-api.ojee.net/live/replays/session/$SID/snapshot \
  | python3 -c '
import sys,json;d=json.load(sys.stdin)
print("load:", d["load"]["stage"], "-", d.get("load",{}).get("detail"))
print("drivers:", len(d["drivers"]), "rc:", len(d["race_control"]))'
```

`load.stage` walks `reading -> outline -> building -> playing`. A cold start
(outline not yet memoised) takes ~60 s; a warm one ~20 s. When it reaches
`playing`, `drivers` is non-empty and `session.track_outline` has ~352 points.

## 6. Stream

```bash
timeout 8 curl -sN https://pitvisor-api.ojee.net/live/replays/session/$SID/stream | head -c 600
# expect: "event: snapshot" lines, roughly 3/sec
```

## 7. Controls

```bash
curl -s -X POST .../session/$SID/pause
curl -s -X POST .../session/$SID/resume
curl -s -X POST .../session/$SID/speed -H 'Content-Type: application/json' -d '{"speed":5}'
curl -s -X POST .../session/$SID/seek   -H 'Content-Type: application/json' -d '{"t_sec":3600}'
curl -s -X POST .../session/$SID/stop
```

## 8. Memory / concurrency

Sessions hold the fully parsed recording in memory and are capped at
`PITVISOR_MAX_REPLAY_SESSIONS` (default 3), evicting the least-recently
touched one. Watch with:

```bash
watch -n2 'systemctl show pitvisor-live.service -p MemoryCurrent; \
  curl -s https://pitvisor-api.ojee.net/live/replays/sessions | python3 -m json.tool'
```

## 9. Download (rate-limited)

```bash
curl -s -X POST https://pitvisor-api.ojee.net/live/replays/download \
  -H 'Content-Type: application/json' \
  -d '{"year":2024,"round":1,"session":"Race"}'
# 6 per hour per address; the 7th returns 429
```
