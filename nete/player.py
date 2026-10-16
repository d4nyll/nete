# nete: background music generated and mixed on your own machine.
# Copyright (C) 2026 Daniel Li
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. It is distributed WITHOUT ANY WARRANTY; see the licence for
# details. You should have received a copy of the licence with this program; if
# not, see <https://www.gnu.org/licenses/>.

"""Playing mixes, with a small screen showing where you are and what is next.

Five lines: what played before, what is playing, what comes next, how far
through you are, and which keys do what.

Rating is the point of the screen as much as playback is. A version you dislike
is easy to notice and impossible to recall afterwards, so the number keys 1 to 5
rate whatever is playing at the moment it bothers you. When the mix ends the same
keys rate the whole thing, which is the judgement you can only make once you have
heard all of it. What you press is written back to the mix's file and decides what
gets played next time.
"""

import curses
import os
import signal
import subprocess
import sys
import time

from . import audio, store

BAR_WIDTH = 24


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def segment_at(cues, elapsed):
    """Index of the segment playing at ``elapsed`` seconds."""
    index = 0
    for i, (at, _variant, _name) in enumerate(cues):
        if at <= elapsed:
            index = i
        else:
            break
    return index


class Session:
    """One mix playing, with its process, its clock and its ratings."""

    def __init__(self, meta):
        self.meta = meta
        self.cues = meta.get("cues", [])
        self.total = meta.get("seconds", 0) or audio.duration(meta["audio"])
        self.proc = subprocess.Popen(
            ["afplay", str(meta["audio"])],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started = time.monotonic()
        self.paused_at = None
        self.paused_total = 0.0
        self.stopped_at = None

    @property
    def elapsed(self):
        # Frozen once the audio stops, so a mix waiting to be rated does not go
        # on counting past its own length.
        now = self.stopped_at or self.paused_at or time.monotonic()
        return min(now - self.started - self.paused_total, self.total or float("inf"))

    @property
    def paused(self):
        return self.paused_at is not None

    def toggle_pause(self):
        # afplay has no pause of its own, so the process itself is suspended.
        if self.paused:
            os.kill(self.proc.pid, signal.SIGCONT)
            self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = None
        else:
            os.kill(self.proc.pid, signal.SIGSTOP)
            self.paused_at = time.monotonic()

    def finished(self):
        ended = self.proc.poll() is not None
        if ended and self.stopped_at is None:
            self.stopped_at = time.monotonic()
        return ended

    def stop(self):
        if self.paused:
            os.kill(self.proc.pid, signal.SIGCONT)
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def rate_variant(self, score):
        index = segment_at(self.cues, self.elapsed)
        if not self.cues:
            return None
        variant = self.cues[index][1]
        self.meta.setdefault("variant_ratings", {})[variant] = score
        return variant

    def save(self):
        data = {k: v for k, v in self.meta.items() if k not in ("audio", "meta")}
        store.save_mix_meta(self.meta["meta"], data)


def draw(screen, session, queued, message, ended=False, next_mix=None):
    screen.erase()
    cues = session.cues
    index = segment_at(cues, session.elapsed) if cues else 0

    def line_for(offset, tag):
        i = index + offset
        if offset > 0 and cues and i >= len(cues):
            return (f" {'next':7} {'(next mix)':<10} {next_mix:>10}" if next_mix
                    else f" {'next':7} nothing queued after this")
        if not cues or not 0 <= i < len(cues):
            return f" {tag:7}  -"
        at, variant, name = cues[i]
        when = f"({clock(at)})" if offset <= 0 else f"(at {clock(at)})"
        mark = "▶" if offset == 0 else " "
        rated = store.normalise_rating(session.meta.get("variant_ratings", {}).get(variant))
        flag = f"  [{store.rating_word(rated)}]" if rated else ""
        return f" {mark} {tag:7} {variant:<10} {when:>10}  {name[:34]}{flag}"

    done = session.elapsed / session.total if session.total else 0
    filled = int(min(1.0, max(0.0, done)) * BAR_WIDTH)
    state = "paused " if session.paused else "playing"
    bar = "#" * filled + "." * (BAR_WIDTH - filled)

    scale = "1 bad  2 poor  3 OK  4 good  5 excellent"
    keys = (f" PRESS A NUMBER TO RATE IT:  {scale}      n skip   q quit" if ended else
            f" space pause   rate what is playing: {scale}   n next   q quit")
    head = ("  THIS MIX HAS FINISHED, WAITING FOR YOUR RATING" if ended
            else f" {session.meta['audio']}")
    rows = [
        head,
        line_for(-1, "before"),
        line_for(0, "NOW"),
        line_for(1, "next"),
        f" {'FINISHED' if ended else state} {clock(session.elapsed)} / "
        f"{clock(session.total)}  [{bar}]  {session.meta['id']}  "
        f"played {session.meta.get('plays', 0)}x  "
        f"mix {store.rating_word(session.meta.get('rating'))}  {queued} queued",
        keys,
    ]
    if message and not ended:
        rows.append(f" {message}")
    height, width = screen.getmaxyx()
    for i, row in enumerate(rows[:height]):
        screen.addnstr(i, 0, row, width - 1)
    screen.refresh()


def ask_rating(screen, session, queued, next_mix=None):
    """Hold the finished mix on screen until the listener says what they thought."""
    curses.flash()
    while True:
        draw(screen, session, queued, "", ended=True, next_mix=next_mix)
        try:
            key = screen.getkey()
        except curses.error:
            time.sleep(0.25)
            continue
        score = store.normalise_rating(key)
        if score:
            return score
        if key in ("n", "\n", " "):
            return "skip"
        if key == "q":
            return "quit"


def run(screen, preset, mixes):
    curses.curs_set(0)
    screen.nodelay(True)
    queue = list(mixes)
    message = f"{preset['name']}: {len(queue)} mix(es)"
    session = None

    try:
        while True:
            if session is None:
                if not queue:
                    return
                meta = queue.pop(0)
                meta["plays"] = meta.get("plays", 0) + 1
                meta["last_played"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                session = Session(meta)
                session.save()

            if session.finished():
                # Stop here rather than rolling straight on. The whole-mix
                # rating is the one you can only give once you have heard all
                # of it, so the mix that just ended has to wait for an answer.
                if store.normalise_rating(session.meta.get("rating")) is None:
                    verdict = ask_rating(screen, session, len(queue),
                                         queue[0]["id"] if queue else None)
                    if verdict == "quit":
                        session.save()
                        return
                    if isinstance(verdict, int):
                        session.meta["rating"] = verdict
                session.save()
                session = None
                message = "next mix"
                continue

            draw(screen, session, len(queue), message,
                 next_mix=queue[0]["id"] if queue else None)
            try:
                key = screen.getkey()
            except curses.error:
                time.sleep(0.25)
                continue

            if key == "q":
                session.stop()
                session.save()
                return
            if key == " ":
                session.toggle_pause()
                message = ""
            elif store.normalise_rating(key):
                score = store.normalise_rating(key)
                variant = session.rate_variant(score)
                session.save()
                message = (f"{variant} rated {store.rating_word(score)}" if variant
                           else "no cue sheet for this mix")
            elif key == "n":
                session.stop()
                session.save()
                session = None
                message = "skipped"
    finally:
        if session:
            session.stop()
            session.save()


def play(preset):
    mixes = store.rank_mixes(store.list_mixes(preset["name"]))
    if not mixes:
        raise store.NeteError(
            f"no mixes for {preset['name']} yet. Make one:\n"
            f"  nete generate {preset['name']}")
    if not sys.stdout.isatty():
        # The screen needs a real terminal. Say so rather than showing a
        # curses traceback to someone who piped the output somewhere.
        newest = mixes[0]
        raise store.NeteError(
            "nete play needs a terminal. Run it directly, or play the file:\n"
            f"  afplay {newest['audio']}")
    curses.wrapper(run, preset, mixes)
    return len(mixes)


def self_check():
    cues = [[0.0, "calm", "a.flac"], [292.0, "pulse", "b.flac"], [524.0, "calm", "c.flac"]]
    assert segment_at(cues, 0) == 0
    assert segment_at(cues, 291.9) == 0
    assert segment_at(cues, 292.0) == 1, "a cue starts exactly on its own timestamp"
    assert segment_at(cues, 10_000) == 2, "past the end stays on the last segment"
    assert segment_at([], 5) == 0, "no cue sheet does not crash"
    assert clock(0) == "0:00" and clock(65) == "1:05" and clock(-3) == "0:00"
    # The number keys are what the screen offers, so they have to map to scores.
    assert store.normalise_rating("1") == 1 and store.normalise_rating("5") == 5
    assert store.normalise_rating("q") is None, "quit must not read as a score"
    assert store.normalise_rating("n") is None, "skip must not read as a score"
    print("player self-check ok")
