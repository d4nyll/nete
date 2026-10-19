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

import array
import curses
import pathlib
import shutil
import subprocess
import sys
import tempfile
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
    """One mix playing, with its process, its clock and its ratings.

    Pausing stops the player rather than suspending it. Suspending it looked
    simpler and survived a short pause, but not a long one: macOS sleeps while
    you are away, tears the audio device down and builds it again on wake, and a
    suspended player sleeps through all of that. It comes back alive and silent.

    So a pause remembers the position and kills the player, and a resume writes
    the rest of the mix to a temporary file and plays that. afplay cannot start
    partway through a file, which is why resuming needs a trimmed copy. It costs
    about a second and a half on an hour-long mix.
    """

    def __init__(self, meta):
        self.meta = meta
        self.cues = meta.get("cues", [])
        self.total = meta.get("seconds", 0) or audio.duration(meta["audio"])
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix="nete_play_"))
        self.paused_position = None
        self.stopped_at = None
        self.proc = None
        self._start(pathlib.Path(meta["audio"]), at=0.0)

    def _start(self, path, at):
        self.proc = subprocess.Popen(["afplay", str(path)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Pretend playback began however long ago it would have, so the clock
        # keeps reading the position in the whole mix rather than in this piece.
        self.started = time.monotonic() - at

    @property
    def elapsed(self):
        # Frozen while paused or once the audio stops, so a mix waiting to be
        # rated does not go on counting past its own length.
        if self.paused_position is not None:
            return self.paused_position
        now = self.stopped_at or time.monotonic()
        return min(now - self.started, self.total or float("inf"))

    @property
    def paused(self):
        return self.paused_position is not None

    def toggle_pause(self):
        if self.paused:
            self.resume()
        else:
            self.pause()

    def pause(self):
        self.paused_position = self.elapsed
        self._kill()

    def resume(self):
        at = self.paused_position or 0.0
        self.paused_position = None
        if at >= self.total - 1:
            self.stopped_at = time.monotonic()
            return
        self._start(self._rest_of_mix(at), at=at)

    def _rest_of_mix(self, at):
        """Write everything from ``at`` onwards to a file afplay can start on."""
        for old in self.scratch.glob("*.wav"):
            old.unlink(missing_ok=True)
        full = self.scratch / "full.wav"
        audio.to_wav(self.meta["audio"], full)
        data, params = audio.read_wav(full)
        full.unlink()
        offset = int(at * params.framerate) * params.sampwidth * params.nchannels
        rest = array.array("h", data.tobytes()[offset:])
        out = self.scratch / f"from-{int(at)}.wav"
        audio.write_wav(out, rest, params)
        return out

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def finished(self):
        if self.paused:
            return False
        ended = self.proc is not None and self.proc.poll() is not None
        if ended and self.stopped_at is None:
            self.stopped_at = time.monotonic()
        return ended

    def stop(self):
        self._kill()
        shutil.rmtree(self.scratch, ignore_errors=True)

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
        f" {'FINISHED' if ended else state} {clock(session.elapsed)}/"
        f"{clock(session.total)} [{bar}] played {session.meta.get('plays', 0)}x "
        f"mix {store.rating_word(session.meta.get('rating'))} {queued} queued",
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
                if session.paused:
                    draw(screen, session, len(queue), "resuming...",
                         next_mix=queue[0]["id"] if queue else None)
                session.toggle_pause()
                message = "" if not session.paused else "paused"
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

    # Pausing must not leave the player suspended: a suspended one does not
    # survive the machine sleeping, which is what a long pause invites. Checking
    # for the imports rather than the signal names, because a check written as a
    # string literal finds itself when it reads this file.
    import importlib
    module = importlib.import_module(__name__)
    assert not hasattr(module, "signal"), "the signal module is how suspending was done"
    assert not hasattr(module, "os"), "os.kill is the other half of it"
    assert clock(0) == "0:00" and clock(65) == "1:05" and clock(-3) == "0:00"
    # The number keys are what the screen offers, so they have to map to scores.
    assert store.normalise_rating("1") == 1 and store.normalise_rating("5") == 5
    assert store.normalise_rating("q") is None, "quit must not read as a score"
    assert store.normalise_rating("n") is None, "skip must not read as a score"
    print("player self-check ok")
