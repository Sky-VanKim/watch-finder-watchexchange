#!/usr/bin/env python3
"""
Watch a subreddit for new posts whose title or body contains a keyword,
and send a push notification via ntfy for each match.

Configuration (environment variables):
  SUBREDDIT             e.g. "MechanicalKeyboards" (no "r/")
  KEYWORDS              comma-separated, e.g. "group buy, GMK"
  NTFY_TOPIC            your private ntfy topic name (treat like a password)
  NTFY_SERVER           optional, default https://ntfy.sh
  REDDIT_CLIENT_ID      from https://www.reddit.com/prefs/apps ("script" app)
  REDDIT_CLIENT_SECRET  from the same page
  REDDIT_USERNAME       optional: bot account username (signs in as the bot)
  REDDIT_PASSWORD       optional: bot account password
  REDDIT_USER_AGENT     optional, e.g. "keyword-watcher/1.0 by u/yourname"
  STATE_FILE            optional, default state.json

No third-party dependencies; standard library only.
"""

import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request

MAX_SEEN = 1000  # how many post IDs to remember


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def http(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def get_token(client_id, client_secret, user_agent, username=None, password=None):
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    if username and password:
        # Sign in as the bot account (script-app "password" grant).
        form = {"grant_type": "password", "username": username, "password": password}
    else:
        # App-only access, no user account.
        form = {"grant_type": "client_credentials"}
    body = urllib.parse.urlencode(form).encode()
    raw = http(
        "https://www.reddit.com/api/v1/access_token",
        data=body,
        headers={"Authorization": f"Basic {auth}", "User-Agent": user_agent},
        method="POST",
    )
    return json.loads(raw)["access_token"]


def fetch_new_posts(subreddit, token, user_agent, limit=100):
    url = f"https://oauth.reddit.com/r/{urllib.parse.quote(subreddit)}/new?limit={limit}&raw_json=1"
    raw = http(url, headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent})
    return [child["data"] for child in json.loads(raw)["data"]["children"]]


def build_matcher(keywords):
    # Whole-word, case-insensitive. "GMK" matches "GMK Olivia" but not "GMKX".
    parts = [re.escape(k.strip()) for k in keywords if k.strip()]
    if not parts:
        sys.exit("KEYWORDS is empty")
    return re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


def find_match(post, matcher):
    for field in ("title", "selftext"):
        m = matcher.search(post.get(field) or "")
        if m:
            return m.group(1)
    return None


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(path, seen_ids):
    with open(path, "w") as f:
        json.dump({"seen": seen_ids[-MAX_SEEN:]}, f)


def notify(server, topic, post, keyword):
    title = post.get("title", "(no title)")
    permalink = "https://www.reddit.com" + post.get("permalink", "")
    body = f"r/{post.get('subreddit')} · matched \"{keyword}\"\n{title}"
    headers = {
        # HTTP headers must be latin-1; ntfy accepts RFC 2047-style, but keep it simple:
        "Title": f"New r/{post.get('subreddit')} post".encode("latin-1", "replace").decode("latin-1"),
        "Click": permalink,
        "Tags": "bell",
    }
    http(f"{server.rstrip('/')}/{topic}", data=body.encode("utf-8"), headers=headers, method="POST")


def main():
    subreddit = env("SUBREDDIT", required=True)
    keywords = env("KEYWORDS", required=True).split(",")
    topic = env("NTFY_TOPIC", required=True)
    server = env("NTFY_SERVER", "https://ntfy.sh")
    client_id = env("REDDIT_CLIENT_ID", required=True)
    client_secret = env("REDDIT_CLIENT_SECRET", required=True)
    user_agent = env("REDDIT_USER_AGENT", "keyword-watcher/1.0")
    state_path = env("STATE_FILE", "state.json")

    matcher = build_matcher(keywords)
    token = get_token(client_id, client_secret, user_agent,
                      env("REDDIT_USERNAME"), env("REDDIT_PASSWORD"))
    posts = fetch_new_posts(subreddit, token, user_agent)  # newest first

    state = load_state(state_path)
    first_run = state is None
    seen = state["seen"] if state else []
    seen_set = set(seen)

    sent = 0
    for post in reversed(posts):  # oldest first, so notifications arrive in order
        pid = post["id"]
        if pid in seen_set:
            continue
        seen.append(pid)
        seen_set.add(pid)
        if first_run:
            continue  # don't flood you with old posts on the very first run
        keyword = find_match(post, matcher)
        if keyword:
            notify(server, topic, post, keyword)
            sent += 1

    save_state(state_path, seen)
    if first_run:
        print(f"First run: recorded {len(posts)} existing posts, no notifications sent.")
    else:
        print(f"Checked {len(posts)} posts, sent {sent} notification(s).")


if __name__ == "__main__":
    main()
