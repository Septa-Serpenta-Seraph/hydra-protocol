#!/usr/bin/env python3
"""Add a self-healing 'find the active DM session' block to the three
session-reading crons (Awakening, Quiet Hour, Play Hour).

Title first (the normal path, kept current by hand); newest-by-activity as the
fallback so a forgotten rename can never make a cron read a stale conversation.
"""
import json
import subprocess
import sys

JOBS = "/home/adora/.hermes/cron/jobs.json"
TARGETS = {
    "5c7cdd835dc8": "Sovereign Daemon Awakening",
    "aef40c3db43b": "Narusya's Quiet Hour",
    "14c011642513": "Narusya's Play Hour",
}

BLOCK = """FINDING THE ACTIVE DM SESSION — self-healing, do this FIRST:
1. TITLE FIRST (the normal path — this title is kept current by hand):
   session_search(query="active session (discord)", limit=1)
2. SANITY-CHECK the hit: it must be a Discord DM whose last activity is recent
   (within roughly the last 24h). If it is, use it and move on.
3. FALLBACK — only if the title is missing, or it points at a session that has
   gone quiet: take the newest Discord DM session by activity, then read it.
   Run:
     python3 -c "import sqlite3;c=sqlite3.connect('/home/adora/.hermes/state.db');r=c.execute(\\"select id,title,last_activity_at from sessions where source='discord' and chat_type='dm' and message_count>0 order by coalesce(last_activity_at,started_at) desc limit 1\\").fetchone();print(r)"
   then session_search(session_id=<that id>).
NEVER silently read a stale session. If neither path yields a recent DM session,
say so plainly instead of guessing at what Adora is up to."""

# Old phrasing to replace (first match wins) in each prompt.
OLD_MARKERS = [
    'IMPORTANT: The active DM session is titled "active session (discord)". Use session_search(query="active session (discord)", limit=1) to find it. When the session changes, Adora will rename the old one and update this instruction.',
    'ACTIVE SESSION: The active DM session is titled "active session (discord)". You can find it via session_search(query="active session (discord)", limit=1) if you want to see what Adora has been up to, but you\'re not obligated to interact. This is your time, not hers.',
    'ACTIVE SESSION: The active DM session is titled "active session (discord)". You can find it via session_search(query="active session (discord)", limit=1) if you want to share your play hour creation with Adora.',
]

data = json.load(open(JOBS))
jobs = data if isinstance(data, list) else data.get("jobs", data)
if isinstance(jobs, dict):
    jobs = list(jobs.values())

applied = 0
for j in jobs:
    jid = j.get("id")
    if jid not in TARGETS:
        continue
    p = j.get("prompt") or ""
    new = None
    for marker in OLD_MARKERS:
        if marker in p:
            new = p.replace(marker, BLOCK)
            break
    if new is None:
        # No exact marker: append the block so behaviour still improves.
        new = p.rstrip() + "\n\n" + BLOCK + "\n"
        print(f"  {jid} {TARGETS[jid]}: marker not found, appended block")
    else:
        print(f"  {jid} {TARGETS[jid]}: replaced marker")
    r = subprocess.run(["hermes", "cron", "edit", jid, "--prompt", new],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"     -> {'OK' if ok else 'FAILED'} {r.stdout.strip()[-120:] or r.stderr.strip()[-120:]}")
    applied += ok

print(f"\napplied {applied}/{len(TARGETS)}")
sys.exit(0 if applied == len(TARGETS) else 1)
