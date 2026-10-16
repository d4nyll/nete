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

"""Turning a description of a reference into candidate wordings, and testing them.

A caption says a track is "cavernous, with metallic percussion over a deep drone".
Each of those is a candidate for the preset's prompt, and each can be said several
ways. This pulls the candidates out, expands each into wordings, and then tries
them against the model without spending anyone's attention.

The screen is the point. A candidate is generated twice off the same seed, once
with the wording and once without, and whatever the wording claims to change is
measured. Most change nothing. Those are recorded rather than discarded, and
offered last, because a measurement that finds nothing is weaker evidence than a
listener who hears nothing.

What survives is ranked by how much it moved. Where two are close, the one naming
a thing wins over the one describing a quality: across about a dozen tested terms,
every word for *what a thing is* did something and every word for *how it should
sound* did not.
"""

import json
import random
import re

from . import rounds, server, store

# What a wording claims to change, and which measurement settles it. A candidate
# with no dimension cannot be screened and goes to the listener unscreened.
DIMENSIONS = {
    "brightness": {"metric": "brightness_hz", "words": [
        "bright", "dark", "muffled", "dull", "shimmer", "airy", "crisp", "warm",
        "high-frequency", "treble", "low-pass", "subdued", "damped", "veiled"]},
    "weight": {"metric": "under_120hz_pct", "words": [
        "sub-bass", "sub bass", "deep", "heavy", "low-end", "rumble", "thick",
        "booming", "weighty", "bass-heavy", "thin", "light"]},
    "space": {"metric": "decay_seconds", "words": [
        "cavernous", "reverb", "reverberant", "spacious", "cathedral", "hall",
        "distant", "echo", "delay", "dry", "close", "intimate", "roomy",
        "ambient space", "spatial depth", "tail", "decaying"]},
    "beat": {"metric": "beat_strength", "words": [
        "beat", "pulse", "kick", "drum", "percussion", "rhythm", "groove",
        "ostinato", "driving", "steady", "metronomic", "four-on-the-floor"]},
    "attack": {"metric": "percussive_pct", "words": [
        "percussive", "staccato", "plucked", "struck", "transient", "hits",
        "sustained", "held", "drone", "pad", "legato", "flowing"]},
    "activity": {"metric": "churn", "words": [
        "busy", "dense", "sparse", "restless", "minimal", "layered", "evolving",
        "static", "shifting", "granular", "stuttering", "glitch"]},
}

# A wording that names a thing rather than describing a quality. Ties break
# toward these.
SOUND_SOURCES = [
    "drone", "pad", "synth", "piano", "guitar", "strings", "choir", "voice",
    "vocal", "bell", "chime", "flute", "brass", "horn", "percussion", "drum",
    "kick", "cymbal", "taiko", "bass", "arpeggio", "motif", "ostinato", "noise",
    "hiss", "crackle", "bird", "water", "stream", "rain", "wind", "leaves",
    "insect", "thunder", "fire", "footsteps", "reverb", "delay", "clipping",
    "saturation", "filter", "oscillator",
]

# Words a caption uses to join clauses. A phrase that still starts with one of
# these after stripping is a fragment of a sentence, not something to ask for.
JOINERS = {
    "a", "an", "the", "and", "or", "but", "with", "over", "under", "this",
    "that", "these", "those", "its", "their", "his", "her", "some", "more",
    "very", "slowly", "softly", "gently", "like", "into", "from", "of", "to",
    "by", "as", "in", "on", "at", "additional", "another", "second", "while",
    "interwoven", "layered", "joined", "accompanied", "followed", "is", "are",
    "was", "were", "it", "they",
}

# Phrases worth pulling out of a caption, longest first so the longer one wins.
PHRASE = re.compile(
    r"\b([a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,3}\s+"
    r"(?:drone|pad|pads|synth|piano|guitar|strings|choir|vocal|vocals|bell|bells|"
    r"chime|chimes|flute|brass|horn|percussion|drums|drum|kick|cymbal|bass|"
    r"arpeggio|motif|ostinato|hiss|crackle|birdsong|water|stream|rain|wind|"
    r"leaves|reverb|delay|texture|shimmer|rumble))\b")


def dimension_of(wording):
    """Which measurement settles this wording, or None when nothing does."""
    low = wording.lower()
    for name, spec in DIMENSIONS.items():
        if any(word in low for word in spec["words"]):
            return name
    return None


def names_a_thing(wording):
    low = wording.lower()
    return any(source in low for source in SOUND_SOURCES)


def extract(text, limit=40):
    """Candidate wordings found in a description, most mentioned first."""
    counts = {}
    for match in PHRASE.finditer(text.lower()):
        words = match.group(1).split()
        # Peel joining words off the front, so "and a deep synth pad" and "deep
        # synth pad" are one candidate rather than two.
        while words and words[0] in JOINERS:
            words.pop(0)
        if not (1 < len(words) <= 4) or words[0] in JOINERS:
            continue
        counts[" ".join(words)] = counts.get(" ".join(words), 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"wording": w, "mentions": n} for w, n in ordered[:limit]]


def expand(wording):
    """Other ways of asking for the same thing, including a technical one.

    Both styles are kept because which one the model hears is a question for the
    screen, not an assumption. A measurement decides it per candidate.
    """
    low = wording.lower()
    out = [low]
    if "cavern" in low or "cathedral" in low:
        out += ["long reverb tail", "a very reverberant hall", "slow diffuse echo"]
    if "reverb" in low and "long" not in low:
        out += ["long reverb tail", "a long decaying tail"]
    if "deep" in low or "sub" in low:
        out += ["heavy sub bass", "a deep low-end weight"]
    if "shimmer" in low or "bright" in low:
        out += ["bright high-frequency shimmer"]
    if "drone" in low:
        out += ["a sustained drone underneath", "one held low note throughout"]
    if "arpegg" in low:
        out += ["a repeating arpeggiated figure"]
    if "percussion" in low or "drum" in low:
        out += ["a repeating kick drum pattern"]
    seen, unique = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def candidates_for(preset, descriptions, limit=None):
    """Every wording worth trying for this preset, with what already decided it."""
    settled = {d["term"].lower() for d in preset.get("decisions", [])}
    found, seen = [], set()
    for record in extract(" ".join(descriptions)):
        for wording in expand(record["wording"]):
            if wording in seen or wording in settled:
                continue
            seen.add(wording)
            found.append({
                "wording": wording,
                "from": record["wording"],
                "mentions": record["mentions"],
                "dimension": dimension_of(wording),
                "names_a_thing": names_a_thing(wording),
            })
    return found[:limit] if limit else found


def screen_one(preset, candidate, pairs=3, seconds=20, base_seed=None, report=None):
    """Generate the wording with and against itself, and measure what it claims.

    Returns the candidate with an ``effect``: how far the measurement moved, as a
    share of the value without the wording. ``None`` when nothing settles it.
    """
    dimension = candidate.get("dimension")
    if not dimension:
        return {**candidate, "status": "unmeasurable", "effect": None}

    metric = DIMENSIONS[dimension]["metric"]
    draw = random.Random(base_seed if base_seed is not None else 0)
    seeds = [draw.randrange(1, 2 ** 31) for _ in range(pairs)]
    base = preset["prompt"]
    with_wording, without = [], []

    for index, seed in enumerate(seeds):
        if report:
            report(f"  pair {index + 1}/{pairs}")
        for prompt, bucket in ((f"{base}, {candidate['wording']}", with_wording),
                               (base, without)):
            spec = {**preset, "prompt": prompt, "variants": {}, "slow": None}
            track = server.generate_track(spec, None, seconds,
                                          store.HOME / "scratch" / "screen", seed=seed)
            bucket.append(rounds.analyse(track)[metric])
            track.unlink(missing_ok=True)

    mid_with = sorted(with_wording)[len(with_wording) // 2]
    mid_without = sorted(without)[len(without) // 2]
    effect = abs(mid_with - mid_without) / abs(mid_without) if mid_without else 0.0
    return {**candidate, "status": "moved" if effect >= 0.10 else "flat",
            "effect": round(effect, 3), "metric": metric,
            "with": mid_with, "without": mid_without}


def rank(screened):
    """Running order: what moved, then what could not be measured, then what did not.

    Nothing is thrown away. A wording the measurement found nothing in is still
    offered, after everything that did move, because a flat measurement is weaker
    evidence than a listener hearing nothing.
    """
    order = {"moved": 0, "unmeasurable": 1, "flat": 2}
    return sorted(screened, key=lambda c: (
        order.get(c["status"], 3),
        -(c.get("effect") or 0),
        not c.get("names_a_thing"),
        c["wording"]))


def self_check():
    assert dimension_of("cavernous reverb") == "space"
    assert dimension_of("heavy sub bass") == "weight"
    assert dimension_of("bright shimmer") == "brightness"
    assert dimension_of("melancholic and wistful") is None, "mood cannot be measured"

    assert names_a_thing("a sustained drone underneath")
    assert not names_a_thing("melancholic and wistful")

    text = ("a deep synth pad and a low drone, with metallic percussion and a "
            "bright shimmer. the deep synth pad returns later.")
    found = {c["wording"]: c["mentions"] for c in extract(text)}
    assert "deep synth pad" in found, sorted(found)
    assert found["deep synth pad"] == 2, "a repeated phrase counts twice"
    # Sentence joiners are not part of what you ask the model for.
    noisy = {c["wording"] for c in extract("a drone and a pad, interwoven with the birdsong")}
    assert not any(w.split()[0] in JOINERS for w in noisy), noisy
    assert dimension_of("a long decaying tail") == "space"

    grown = expand("cavernous reverb")
    assert "long reverb tail" in grown and grown[0] == "cavernous reverb"

    preset = {**store.PRESET_DEFAULTS, "name": "t", "prompt": "dark ambient",
              "decisions": [{"term": "long reverb tail", "verdict": "out"}]}
    wordings = [c["wording"] for c in candidates_for(preset, [text])]
    assert "long reverb tail" not in wordings, "a settled wording is not offered again"

    ordered = rank([
        {"wording": "flat one", "status": "flat", "effect": 0.02, "names_a_thing": True},
        {"wording": "mood", "status": "unmeasurable", "effect": None, "names_a_thing": False},
        {"wording": "small", "status": "moved", "effect": 0.2, "names_a_thing": False},
        {"wording": "big", "status": "moved", "effect": 0.9, "names_a_thing": False},
    ])
    assert [c["wording"] for c in ordered] == ["big", "small", "mood", "flat one"], ordered

    tie = rank([
        {"wording": "quality", "status": "moved", "effect": 0.5, "names_a_thing": False},
        {"wording": "a drone", "status": "moved", "effect": 0.5, "names_a_thing": True},
    ])
    assert tie[0]["wording"] == "a drone", "a tie goes to the one naming a thing"
    print("terms self-check ok")
