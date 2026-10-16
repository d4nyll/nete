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

"""Building one long mix out of a preset's tracks.

A mix plays several versions of a preset in turn and fades one into the next, so
a work session gets some shape instead of the same thing for two hours.

Two rules came out of listening and are worth keeping:

Every segment plays at the speed it was generated at. An earlier version sped
some segments up and slowed others down to vary the energy, which moved them into
different musical keys, and back to back they sounded like someone walking on a
keyboard. Energy comes from *which* version plays, never from changing its speed.

Nothing runs for less than the preset's floor. Versions carry a seconds budget
set by ear on isolated clips, but inside a mix anything under about 20 seconds
arrives and leaves too fast to be anything but distracting.
"""

import math
import pathlib
import random
import tempfile

from . import audio, store


def brightness(preset, variant):
    """How much of the bright part of the sound this version keeps.

    By default the version allowed to play longest is the one that has to stay
    out of the way, so it keeps the least, and the one that can only last
    seconds keeps the most. When every version is allowed the same time there is
    nothing to compare, and they all sit in the middle.

    A version can set its own ``brightness`` and ignore all of that. The default
    ties "short" to "bright", which is wrong for a version that should be brief
    *and* dark.
    """
    own = preset["variants"][variant].get("brightness")
    if own:
        return int(own)
    lengths = [store.run_seconds(preset, v) for v in preset["variants"]]
    longest, shortest = max(lengths), min(lengths)
    if longest == shortest:
        return 2000
    secs = store.run_seconds(preset, variant)
    position = (math.log(secs) - math.log(longest)) / (math.log(shortest) - math.log(longest))
    return int(500 * (4000 / 500) ** position)


def plan(preset, minutes, rng, available):
    """Decide the running order: which version plays when, and from which track."""
    pattern = [v for v in (preset["pattern"] or list(preset["variants"])) if v in available]
    if not pattern:
        raise store.NeteError(
            f"no tracks to mix. Generate some first:\n"
            f"  nete generate {preset['name']}")

    order, total, crossfade = [], 0.0, preset["crossfade"]
    while True:
        variant = pattern[len(order) % len(pattern)]
        # Never the same file twice running, so a repeat is at least separated.
        pool = [t for t in available[variant] if not order or t != order[-1][0]] \
            or available[variant]
        order.append((rng.choice(pool), variant))
        total += store.run_seconds(preset, variant) - (crossfade if len(order) > 1 else 0)
        if total >= minutes * 60:
            return order


def build(preset, minutes=60, seed=None, on_segment=None):
    """Render a mix for ``preset`` and record it under ~/.nete/mixes.

    Returns the mix metadata dict, which carries the cue sheet so a listener can
    name a time and have it looked up.
    """
    available = {v: store.tracks_for(preset["name"], v) for v in preset["variants"]}
    available = {v: t for v, t in available.items() if t}
    rng = random.Random(seed)
    order = plan(preset, minutes, rng, available)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="nete_mix_"))
    try:
        mixed, params, cues = None, None, []
        for index, (track, variant) in enumerate(order):
            wav = tmp / f"seg{index}.wav"
            audio.to_wav(track, wav)
            data, params = audio.read_wav(wav)
            wav.unlink()
            nch = params.nchannels

            want = int(store.run_seconds(preset, variant) * params.framerate)
            frames = len(data) // nch
            if want < frames:
                start = rng.randrange(0, frames - want)
                data = data[start * nch:(start + want) * nch]
            data = audio.low_pass(data, params.framerate, nch, brightness(preset, variant))

            if on_segment:
                on_segment(index + 1, len(order), variant, track.name)

            if mixed is None:
                cues.append([0.0, variant, track.name])
                mixed = data
                continue
            # A fade can never run longer than a third of the shorter segment,
            # or it would swallow it whole.
            n = min(preset["crossfade"] * params.framerate,
                    len(mixed) // nch // 3, len(data) // nch // 3)
            cues.append([round((len(mixed) // nch - n) / params.framerate, 1),
                         variant, track.name])
            faded = audio.crossfade(mixed, data, nch, n)
            del mixed[len(mixed) - n * nch:]
            mixed.extend(faded)
            mixed.extend(data[n * nch:])

        meta = store.new_mix_meta(
            preset["name"], len(mixed) // params.nchannels / params.framerate, cues)
        out_dir = store.mix_dir(preset["name"])
        out_dir.mkdir(parents=True, exist_ok=True)
        raw = tmp / "mix.wav"
        audio.write_wav(raw, mixed, params)
        audio.to_flac(raw, out_dir / f"{meta['id']}.flac")
        raw.unlink()
        store.save_mix_meta(out_dir / f"{meta['id']}.json", meta)
        return meta
    finally:
        for leftover in tmp.glob("*"):
            leftover.unlink()
        tmp.rmdir()


def self_check():
    preset = {**store.PRESET_DEFAULTS, "name": "t", "min_seconds": 20,
              "variants": {"calm": {"prompt": "a", "seconds": 300},
                           "quick": {"prompt": "b", "seconds": 5}},
              "pattern": ["calm", "quick"]}
    assert brightness(preset, "calm") == 500, "the longest keeps the least"
    assert brightness(preset, "quick") == 4000, "the shortest keeps the most"
    # A version can be brief and dark at once.
    own = {**preset, "variants": {**preset["variants"],
                                  "quick": {**preset["variants"]["quick"], "brightness": 800}}}
    assert brightness(own, "quick") == 800, "its own setting wins"
    assert brightness(own, "calm") == 500, "and does not disturb the others"

    flat = {**preset, "variants": {"a": {"seconds": 60}, "b": {"seconds": 60}}}
    assert brightness(flat, "a") == brightness(flat, "b") == 2000, \
        "a preset that is calm throughout treats every version alike"

    # calm alone is 300s, so ask for long enough that the pattern has to repeat.
    order = plan(preset, minutes=6, rng=random.Random(1),
                 available={"calm": [pathlib.Path("c1")], "quick": [pathlib.Path("q1")]})
    assert [v for _, v in order][:2] == ["calm", "quick"], order
    assert sum(store.run_seconds(preset, v) for _, v in order) >= 360
    print("mixer self-check ok")
