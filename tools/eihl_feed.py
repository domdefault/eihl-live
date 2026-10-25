#!/usr/bin/env python3
"""Read the EIHL live feed straight from the scoring system's public S3 bucket.

    python3 eihl_feed.py today            today's games with status and score
    python3 eihl_feed.py game 4464        score, goals and penalties for one game
    python3 eihl_feed.py watch 4464       print each change to the score or events as it lands

Standard library only. Match IDs are the scoring system's, not eliteleague.co.uk's.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = "https://s3.eu-west-1.amazonaws.com/eihl.hokejovyzapis.cz"
UK = ZoneInfo("Europe/London")
FINAL = {"K": "Final", "KP": "Final (OT)", "KN": "Final (SO)"}
_etags = {}


def get(path):
    """Fetch one JSON file. Returns None for a missing file, or "unchanged" on a 304."""
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": "eihl-feed"})
    if path in _etags:
        req.add_header("If-None-Match", _etags[path])
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            _etags[path] = r.headers.get("ETag", "")
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return "unchanged"
        if e.code in (403, 404):
            return None
        raise


def season(now):
    return now.year if now.month >= 7 else now.year - 1  # 2026 = the 2026/27 season


def fixtures(year):
    """All fixtures for a season: IDs, dates and teams. Live status and score come from game-info."""
    out = {}
    for league in (3, 4, 5):
        for folder in ("league-matches2", "league-matches"):
            data = get(f"{folder}/{year}/{league}.json")
            if data:
                for m in data["matches"]:
                    out[m["id"]] = m
                break
    return sorted(out.values(), key=lambda m: m["start_date"])


def clock(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def who(p):
    return f"#{p['jersey']} {p['name'].strip()} {p['surname'].strip()}" if p else "Bench"


def status(info):
    alias = info["actualTimeAlias"]
    return FINAL.get(alias) or info["actualTimeName"]


def score(info):
    f = info["results"]["score"]["final"]
    return f"{f['score_home']}-{f['score_guest']}"


def cmd_today():
    now = datetime.now(UK)
    today = now.strftime("%Y-%m-%d")
    games = [m for m in fixtures(season(now)) if m["start_date"].startswith(today)]
    if not games:
        print("No games today.")
    for m in games:
        info = (get(f"matches/{m['id']}/game-info.json") or {}).get("gameInfo")
        state = f"{score(info)}  {status(info)}" if info and info["actualTimeAlias"] != "0" else "not started"
        print(f"{m['id']}  {m['start_date'][11:16]}  {m['home']['shortcut']} v {m['guest']['shortcut']}  {state}  ({m['league']})")


def events(pe):
    for period in ("1", "2", "3", "overtime"):
        yield from pe.get(period, [])


def describe(e):
    d, side = e["data"], e["data"].get("team", "")
    if e["type"] == "goal":
        assists = ", ".join(who(a) for a in d["assistants"]) or "unassisted"
        return f"{clock(d['scoreboardTime'])}  GOAL {side:7}  {who(d['scorer'])} ({assists})  {d['balance']}  {d['currentScore']}"
    if e["type"] == "penalty":
        return f"{clock(d['createTime'])}  PEN  {side:7}  {who(d['disciplinedPlayer'])}  {d['duration'] // 60} min {d['codename']}"
    return None


def cmd_game(match_id):
    gi = get(f"matches/{match_id}/game-info.json")
    if not gi:
        sys.exit(f"No game-info for match {match_id}")
    info = gi["gameInfo"]
    t = info["teamInfo"]
    print(f"{t['home']['name']} {score(info)} {t['visitor']['name']}  ({status(info)})")
    shots = get(f"visualization/shots/{match_id}.json")
    if shots:
        m = shots["match"]
        # match_shot_resutl_id: 1 saved, 2 missed, 3 blocked, 4 goal
        sog = {tid: sum(1 for s in m["shots"] if s["team_id"] == tid and s["match_shot_resutl_id"] in (1, 4))
               for tid in (m["home_id"], m["visitor_id"])}
        print(f"Shots on goal {sog[m['home_id']]}-{sog[m['visitor_id']]}")
    for e in events(get(f"matches/{match_id}/period-events.json") or {}):
        line = describe(e)
        if line:
            print(line)


def cmd_watch(match_id, every=5):
    seen, last_score, primed = set(), None, False
    while True:
        gi = get(f"matches/{match_id}/game-info.json")
        if isinstance(gi, dict):
            info = gi["gameInfo"]
            now = f"{score(info)} {status(info)}"
            if now != last_score:
                print(f"{datetime.now(UK):%H:%M:%S}  SCORE  {now}", flush=True)
                last_score = now
        pe = get(f"matches/{match_id}/period-events.json")
        if isinstance(pe, dict):
            for e in events(pe):
                key = (e["type"], e["data"].get("id"))
                if key in seen:
                    continue
                seen.add(key)
                line = describe(e)
                if line and primed:  # the first pass only records what's already there
                    print(f"{datetime.now(UK):%H:%M:%S}  {line}", flush=True)
        primed = True
        if isinstance(gi, dict) and gi["gameInfo"]["actualTimeAlias"] in FINAL:
            break
        time.sleep(every)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["today"]:
        cmd_today()
    elif args[:1] == ["game"] and len(args) == 2:
        cmd_game(args[1])
    elif args[:1] == ["watch"] and len(args) == 2:
        cmd_watch(args[1])
    else:
        sys.exit(__doc__)
