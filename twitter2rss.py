#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "requests",
#   "feedgen",
# ]
# ///

"""twitter2rss: turn public X/Twitter accounts into RSS feeds via api.fxtwitter.com.

One <user>.xml per user.

    uv run twitter2rss.py Barchart cs_zhaozilong
    uv run twitter2rss.py $(cat users.txt)

LIMITS
- Public accounts only; each run sees the latest ~20 posts.
- Photos are embedded by their original pbs.twimg.com URL; videos show a thumbnail.
"""

import argparse
import html
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests
from feedgen.feed import FeedGenerator


def fetch_statuses(user):
    # fxtwitter returns spurious 404s now and then; retry before giving up.
    for attempt in range(3):
        if attempt:
            time.sleep(5 * attempt)
        response = requests.get(
            f"https://api.fxtwitter.com/2/profile/{user}/statuses",
            timeout=20,
            headers={"User-Agent": "twitter2rss"},
        )
        if response.ok:
            break
    response.raise_for_status()
    return response.json().get("results") or []


def media_html(media):
    parts = []
    for item in (media or {}).get("all", []):
        url = item.get("url") if item.get("type") == "photo" else item.get("thumbnail_url")
        if url:
            parts.append(
                f'<p><img src="{html.escape(url, quote=True)}" '
                f'style="max-width:100%;height:auto;" /></p>'
            )
    return parts


def text_html(text):
    return "<p>" + html.escape(text or "").replace("\n", "<br/>") + "</p>"


def translate_zh(statuses):
    """Translate English posts to Chinese in one request.

    Returns {id(status): chinese_text}; empty if translation fails.
    """
    english = [s for s in statuses if s and s.get("lang") == "en" and s.get("text")]
    if not english:
        return {}
    for attempt in range(3):
        if attempt:
            time.sleep(10 * attempt)
        try:
            response = requests.post(
                "https://translate.googleapis.com/translate_a/t",
                params={"client": "gtx", "sl": "en", "tl": "zh-CN"},
                data={"q": [s["text"] for s in english]},
                timeout=30,
            )
            response.raise_for_status()
            return {id(s): zh for s, zh in zip(english, response.json()) if zh}
        except Exception as error:
            print(f"translation failed (attempt {attempt + 1}): {error}", file=sys.stderr)
    print("keeping English only", file=sys.stderr)
    return {}


def bilingual_html(status, zh):
    """ZH first, then the original text."""
    original = text_html(status.get("text"))
    return text_html(zh) + original if zh else original


def build_description(status, translations):
    parts = [bilingual_html(status, translations.get(id(status)))]
    parts += media_html(status.get("media"))

    quote = status.get("quote")
    if quote:
        who = html.escape((quote.get("author") or {}).get("screen_name", ""))
        link = html.escape(quote.get("url", ""), quote=True)
        parts.append(
            f'<blockquote><p><a href="{link}">@{who}</a></p>'
            f"{bilingual_html(quote, translations.get(id(quote)))}{''.join(media_html(quote.get('media')))}</blockquote>"
        )

    return "\n".join(parts)


def build_feed(user, statuses):
    feed = FeedGenerator()
    feed.title(f"@{user}")
    feed.link(href=f"https://x.com/{user}")
    feed.description(f"Posts from x.com/{user}")

    translations = translate_zh(statuses + [s.get("quote") for s in statuses])

    for status in statuses:
        url = status.get("url")
        if not url:
            continue

        entry = feed.add_entry(order="append")
        entry.id(url)
        entry.link(href=url)

        # Cap by display width (CJK = 2) so Chinese titles stay short enough
        # that mail clients don't truncate at the last space.
        zh = translations.get(id(status))
        title, width = "", 0
        for ch in " ".join((zh or status.get("text") or "").split()):
            width += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
            if width > 80:
                break
            title += ch
        title = title or "Media post"
        if status.get("reposted_by"):
            title = f"RT: {title}"
        entry.title(title)

        description = build_description(status, translations)
        entry.description(description)
        entry.content(description, type="html")

        ts = status.get("created_timestamp")
        if ts:
            entry.pubDate(datetime.fromtimestamp(ts, tz=timezone.utc))

    # Keep lastBuildDate stable when the feed has not changed.
    dates = [e.pubDate() for e in feed.entry() if e.pubDate()]
    if dates:
        feed.lastBuildDate(max(dates))

    return feed


def user_name(text):
    text = text.lstrip("@")
    if not re.fullmatch(r"\w{1,15}", text, re.ASCII):
        raise argparse.ArgumentTypeError(f"{text!r} is not a valid X username")
    return text


def update_feed(user):
    feed = build_feed(user, fetch_statuses(user))
    if not feed.entry():
        raise ValueError("no posts found (private, suspended, or API error?)")
    feed.rss_file(f"{user}.xml", pretty=True)
    return len(feed.entry())


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("users", nargs="+", type=user_name, metavar="user")
    users = parser.parse_args().users

    failed = []
    for user in users:
        try:
            count = update_feed(user)
        except Exception as error:
            failed.append(user)
            print(f"{user}: FAILED, old feed kept. {error}", file=sys.stderr)
        else:
            print(f"{user}: wrote {count} posts to {user}.xml", flush=True)

    if failed:
        sys.exit(f"{len(failed)} of {len(users)} user(s) failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
