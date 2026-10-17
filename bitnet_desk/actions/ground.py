"""Grounding: is what the model filled in actually in the user's message?

check() returns "literal" (the value is in the message), "inferred" (a reasonable reading of it —
allowed, but the user must approve), or raises Ungrounded (the model made it up: no action).
"""
import re
from pathlib import Path
from urllib.parse import urlsplit

from .registry import ActionError
from .resolve import normalise_url

STOP_WORDS = {"the", "a", "an", "my", "me", "for", "to", "of", "in", "on", "about", "and", "or",
              "with", "from", "i", "it", "that", "this", "some", "all", "file", "files", "app",
              "folder", "project", "please", "open", "find", "show", "start", "launch", "up"}

# Well-known sites the user may name without a domain ("browse to wikipedia").
WELL_KNOWN = {"wikipedia": "wikipedia.org", "github": "github.com", "youtube": "youtube.com",
              "google": "google.com", "gmail": "mail.google.com", "reddit": "reddit.com",
              "hacker news": "news.ycombinator.com", "stack overflow": "stackoverflow.com",
              "amazon": "amazon.com", "netflix": "netflix.com", "twitter": "x.com",
              "linkedin": "linkedin.com", "maps": "maps.google.com", "chatgpt": "chatgpt.com"}


# A search is only clearly about the user's files if the message says so.
FILE_HINTS = re.compile(r"\b(?:files?|folders?|documents?|docs?|pdfs?|photos?|images?|pictures?|"
                        r"spreadsheets?|slides|notes|downloads|desktop)\b", re.I)
# "what time is it in Tokyo / in UTC" is not about the local clock.
PLACE = re.compile(r"\b(?:in|at)\s+[A-Z][a-z]+")                    # case-sensitive: a named place
ZONE = re.compile(r"\b(?:utc|gmt|time ?zones?|pst|pdt|est|edt|cst|cdt|mst|mdt|bst|cet|cest|ist|jst|"
                  r"aest|hkt|sgt)\b", re.I)


# Music and volume picks must be about music or sound, in words the message actually uses.
MUSIC_WORDS = re.compile(r"\b(?:music|song|songs|track|tracks|tune|tunes|spotify|playlist|album|playing|"
                         r"play|pause|resume|skip|next|previous|prev)\b", re.I)
# …and the action must be one the message asks for ("set the volume to 30" isn't "up")
MEDIA_ACTION_WORDS = {"play": r"play|resume|unpause|start|put on", "pause": r"pause|stop|hold",
                      "toggle": r"pause|play|toggle", "next": r"next|skip", "previous": r"previous|prev|back|last"}
VOLUME_ACTION_WORDS = {"up": r"up|louder|raise|increase|higher|more", "down": r"down|quieter|softer|lower|decrease|reduce|less",
                       "mute": r"(?<!un)mute|silence|sound off", "unmute": r"unmute|sound on", "set": r"\d+"}
# Naming a player: "spotify"; plain "music" is just music, so Music needs "Apple Music", "the Music app"…
PLAYER_NAMED = {"Spotify": re.compile(r"\bspotify\b", re.I),
                "Music": re.compile(r"\b(?:apple music|itunes|music app|(?:in|on|using|with) (?:the )?music)\b", re.I)}


def player_named(player, text):
    rx = PLAYER_NAMED.get(player)
    return bool(rx and rx.search(text))
SOUND_WORDS = re.compile(r"\b(?:volume|sound|audio|louder|quieter|softer|mute|unmute|speakers?|loud|quiet)\b", re.I)


class Ungrounded(Exception):
    pass


def _words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def _content_words(text):
    return [w for w in _words(text) if w not in STOP_WORDS]


def _word_in(word, text_words):
    return any(w == word or (len(word) >= 3 and (w.startswith(word) or word.startswith(w)))
               for w in text_words)


def check(tool, args, text):
    lowered = text.lower()
    tw = _words(text)
    if tool == "now":
        if PLACE.search(text) or ZONE.search(text):
            raise Ungrounded("asks about another place or time zone, not the local clock")
        return "literal"
    if tool == "help":
        return "literal"
    if tool == "open_url":
        try:
            host = (urlsplit(normalise_url(str(args.get("url", "")))).hostname or "").removeprefix("www.")
        except ActionError:
            raise Ungrounded("not a web address") from None
        if host and host in lowered:
            return "literal"
        if any(site == host and re.search(rf"\b{re.escape(name)}\b", lowered) for name, site in WELL_KNOWN.items()):
            return "inferred"
        raise Ungrounded(f"the message doesn't mention {host or 'a web address'}")
    if tool == "download":
        url = str(args.get("url", "")).strip()
        if url and url in text:
            return "literal"
        raise Ungrounded("the message doesn't contain that URL")
    if tool == "run_shell":
        command = str(args.get("command", "")).strip()
        if command and command in text:
            return "literal"
        raise Ungrounded("the model doesn't write shell commands; type the exact command")
    if tool == "find_files":
        words = _content_words(str(args.get("query", "")))
        if not (words and all(_word_in(w, tw) for w in words)):
            raise Ungrounded("the search words aren't in the message")
        return "literal" if FILE_HINTS.search(text) else "inferred"   # "find me a pancake recipe": ask
    if tool == "media":
        if not MUSIC_WORDS.search(text):
            raise Ungrounded("the message isn't about music")
        action = str(args.get("action"))
        if not re.search(rf"\b(?:{MEDIA_ACTION_WORDS.get(action, '$^')})\b", text, re.I):
            raise Ungrounded(f"the message doesn't ask to {action}")
        player = str(args.get("player") or "")
        if player and not player_named(player, text):
            raise Ungrounded(f"the message doesn't mention {player}")
        # playing *something specific* ("play some jazz") isn't resume: allowed, but the user approves
        extra = [w for w in _content_words(text) if not re.fullmatch(MUSIC_WORDS, w)
                 and w not in {"music", "on", "in", "apple", "song", "some", "this", "one", "go", "back"}]
        return "inferred" if extra else "literal"
    if tool == "volume":
        if not SOUND_WORDS.search(text):
            raise Ungrounded("the message isn't about the volume")
        action = str(args.get("action"))
        if not re.search(rf"\b(?:{VOLUME_ACTION_WORDS.get(action, '$^')})\b", text, re.I):
            raise Ungrounded(f"the message doesn't ask to turn it {action}")
        level = args.get("level")
        if args.get("action") == "set" and not re.search(rf"\b{level}\b", text):
            raise Ungrounded("the message doesn't give that volume level")
        return "literal"
    if tool == "playlist":
        words = _content_words(str(args.get("name", "")))
        if not (words and all(_word_in(w, tw) for w in words)):
            raise Ungrounded("the message doesn't name that playlist")
        return "literal"
    if tool in ("open_app", "start_project", "open_path"):
        value = str(args.get("name") or args.get("path") or "")
        if tool == "open_path":
            value = Path(value).name or value
        words = _content_words(value)
        return "literal" if words and all(_word_in(w, tw) for w in words) else "inferred"
    raise Ungrounded(f"unknown tool {tool}")
