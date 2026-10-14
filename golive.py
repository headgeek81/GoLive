"""GoLive: when headgeek81 goes live on Twitch, post once to the Facebook Page and Instagram.
Meant to run every 5 minutes on GitHub Actions. Posts once per stream, never twice."""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

LOGIN = os.getenv("TWITCH_LOGIN", "headgeek81")
LINK = "twitch.tv/headgeek81"
GRAPH = "https://graph.facebook.com/v23.0"
STATE_FILE = Path(__file__).parent / "state.json"
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
POST_FACEBOOK = os.getenv("POST_FACEBOOK", "1") == "1"
POST_INSTAGRAM = os.getenv("POST_INSTAGRAM", "1") == "1"
DASHBOARD_REPO = os.getenv("DASHBOARD_REPO", "")
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
COOLDOWN_HOURS = float(os.getenv("COOLDOWN_HOURS", "3"))  # a dropped stream that restarts will not re-post

# Games that get the scary treatment. Anything else gets a general gaming post.
HORROR = [g.strip().lower() for g in os.getenv(
    "HORROR_GAMES", "horror,phasmophobia,silent hill,resident evil,dead by daylight,outlast,until dawn,"
    "lethal company,five nights,alan wake,amnesia,alien isolation,forest,content warning,demonologist,"
    "dead space,little nightmares,layers of fear,visage,fears to fathom,the quarry,man of medan,devour,"
    "callisto protocol,cry of fear,white noise,nightmare,haunted,poppy playtime,fatal frame,evil within,"
    "the mortuary assistant,fear the spotlight,ghost watchers,sker ritual").split(",") if g.strip()]
OTHER = [g.strip().lower() for g in os.getenv(
    "NON_HORROR_GAMES", "grand theft auto,helldivers,rainbow six,marvel rivals,overwatch,fortnite,valorant,"
    "apex legends,call of duty,minecraft,rocket league,league of legends,mario,zelda,pokemon").split(",") if g.strip()]
GENERIC_CATEGORIES = {"", "just chatting", "irl", "special events", "talk shows & podcasts", "software and game development"}

HORROR_OPENERS = ["WE ARE LIVE and I should NOT be playing this alone",
                  "LIVE NOW and the dread is real",
                  "I'M LIVE!!! Get in here before something eats me",
                  "The stream is LIVE. Come watch me panic in real time"]
OTHER_OPENERS = ["LIVE NOW and the chaos is real",
                 "I'M LIVE!!! Come watch me lose it",
                 "The stream is LIVE and I have zero chill",
                 "WE ARE LIVE. Get in here"]


def tag(s):
    return "#" + re.sub(r"[^A-Za-z0-9]", "", s)


def is_horror(text):
    t = text.lower()
    return any(h in t for h in HORROR) and not any(o in t for o in OTHER)


def find_game(stream):
    """Use Twitch's game category. If it is blank or generic, look for a known game name in the stream title."""
    cat = (stream.get("game_name") or "").strip()
    if cat.lower() not in GENERIC_CATEGORIES:
        return cat
    title = (stream.get("title") or "").lower()
    for name in HORROR + OTHER:
        if name in title and name != "horror" and len(name) > 4:
            return name.title()
    return ""


def hashtags(game, horror):
    base = ["#HeadGeek81", "#LiveNow", "#TwitchStreamer", "#Twitch", "#LiveStreamer", "#GamerCommunity", "#SmallStreamer"]
    scary = ["#HorrorGaming", "#HorrorGames", "#ScaryGames"]
    other = ["#Gaming", "#GamingCommunity", "#FunnyGaming"]
    out, seen = [], set()
    for t in ([tag(game)] if game else []) + (scary if horror else other) + base:
        if len(t) > 1 and t.lower() not in seen and len(out) < 12:
            seen.add(t.lower())
            out.append(t)
    return " ".join(out)


def captions(title, game):
    horror = is_horror(f"{game} {title}")
    opener = random.choice(HORROR_OPENERS if horror else OTHER_OPENERS)
    if game and horror:
        play = f"Playing {game} and absolutely not handling it well."
    elif game:
        play = f"Playing {game}. Bring snacks and bad decisions."
    else:
        play = "Come hang out and cause some trouble."
    body = f"{opener}\n\n{title}\n{play}\n\n"
    tags = hashtags(game, horror)
    fb = f"🔴 {body}Come hang out on Twitch: https://{LINK}\n\n{tags}"
    ig = f"🔴 {body}Watch LIVE on Twitch: {LINK} (link in bio)\n\n{tags}"
    return fb, ig


def twitch_token():
    r = requests.post("https://id.twitch.tv/oauth2/token", timeout=30, data={
        "client_id": os.environ["TWITCH_CLIENT_ID"], "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]


def live_stream():
    r = requests.get("https://api.twitch.tv/helix/streams", timeout=30, params={"user_login": LOGIN}, headers={
        "Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": f"Bearer {twitch_token()}"})
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data and data[0].get("type") == "live" else None


def check(r):
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:300]}")
    return r


def post_facebook(caption):
    page, tok = os.environ["FB_PAGE_ID"], os.environ["FB_PAGE_TOKEN"]
    r = check(requests.post(f"{GRAPH}/{page}/feed", timeout=60, data={
        "message": caption, "link": f"https://{LINK}", "access_token": tok}))
    return r.json()["id"]


def post_instagram(caption, thumb):
    ig = os.environ["IG_USER_ID"]
    tok = os.environ.get("IG_PAGE_TOKEN") or os.environ["FB_PAGE_TOKEN"]
    # Instagram needs a public picture. Twitch's live preview works once the stream has a thumbnail.
    probe = requests.get(thumb, timeout=30)
    if not probe.ok or "404_preview" in probe.url:
        raise RuntimeError("Twitch has no preview image yet (will retry in 5 minutes)")
    cid = check(requests.post(f"{GRAPH}/{ig}/media", timeout=60, data={
        "image_url": thumb, "caption": caption, "access_token": tok})).json()["id"]
    for _ in range(12):
        st = requests.get(f"{GRAPH}/{cid}", timeout=30, params={"fields": "status_code", "access_token": tok}).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the image: {st}")
        time.sleep(5)
    return check(requests.post(f"{GRAPH}/{ig}/media_publish", timeout=60, data={
        "creation_id": cid, "access_token": tok})).json()["id"]


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))



def push_dashboard(state):
    """Publish the recent check history to the public dashboard (optional)."""
    if not (DASHBOARD_REPO and DASHBOARD_TOKEN):
        return
    import base64
    try:
        url = f"https://api.github.com/repos/{DASHBOARD_REPO}/contents/golive.json"
        hdr = {"Authorization": f"Bearer {DASHBOARD_TOKEN}", "Accept": "application/vnd.github+json"}
        cur = requests.get(url, headers=hdr, timeout=30)
        body = {"message": "GoLive: update checks",
                "content": base64.b64encode(json.dumps({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                        "checks": state.get("checks", [])}, indent=1).encode()).decode()}
        if cur.status_code == 200:
            body["sha"] = cur.json()["sha"]
        check(requests.put(url, headers=hdr, json=body, timeout=60))
        print("Dashboard updated.")
    except Exception as e:
        print(f"(dashboard update skipped: {re.sub(r'EAA[A-Za-z0-9_-]{20,}', '[removed]', str(e))[:200]})")


def record_check(state, stream, game="", results=None):
    chk = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "live": bool(stream)}
    if stream:
        chk.update(title=stream.get("title", ""), game=game, **(results or {}))
    state["checks"] = (state.get("checks", []) + [chk])[-30:]
    save_state(state)
    push_dashboard(state)


def main():
    stream = live_stream() if not (DRY_RUN and not os.getenv("TWITCH_CLIENT_ID")) else None
    if not stream and DRY_RUN:
        stream = {"id": "sample", "title": "Sample title: playing something scary", "game_name": "Phasmophobia",
                  "thumbnail_url": "https://static-cdn.jtvnw.net/previews-ttv/live_user_headgeek81-{width}x{height}.jpg"}
        print("Not live right now, showing a sample of what would be posted.")
    if not stream:
        print(f"{LOGIN} is offline. Nothing to do.")
        if not DRY_RUN:
            record_check(load_state(), None)
        return
    state = load_state()
    sid = stream["id"]
    title, game = stream.get("title") or "Live now", find_game(stream)
    thumb = (stream.get("thumbnail_url", "").replace("{width}", "1280").replace("{height}", "720")
             + f"?t={int(time.time())}")
    fb_cap, ig_cap = captions(title, game)

    if DRY_RUN:
        print("--- Facebook ---\n" + fb_cap + "\n\n--- Instagram ---\n" + ig_cap + f"\n(image: {thumb})")
        return

    if state.get("stream_id") != sid:
        last = state.get("announced_at")
        if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < COOLDOWN_HOURS * 3600:
            print("Already announced a stream recently (probably a reconnect). Skipping.")
            state.update({"stream_id": sid, "facebook": "skipped", "instagram": "skipped"})
            record_check(state, stream, game, {"facebook": "skipped", "instagram": "skipped"})
            return
        state = {"checks": state.get("checks", []), "stream_id": sid, "announced_at": datetime.now(timezone.utc).isoformat()}
    print(f"LIVE: {title} ({game}). stream id {sid}")
    failed = set()
    for name, on, fn in (("facebook", POST_FACEBOOK, lambda: post_facebook(fb_cap)),
                         ("instagram", POST_INSTAGRAM, lambda: post_instagram(ig_cap, thumb))):
        if not on or state.get(name):
            continue
        try:
            state[name] = fn()
            print(f"  {name}: posted ({state[name]})")
        except Exception as e:
            print(f"  {name}: FAILED ({e}). Will retry on the next check.")
            failed.add(name)
        save_state(state)
    results = {n: ("failed" if n in failed else "skipped" if state.get(n) == "skipped" else "posted" if state.get(n) else "off")
               for n in ("facebook", "instagram")}
    record_check(state, stream, game, results)


if __name__ == "__main__":
    main()
