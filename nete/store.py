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

"""Where nete keeps things on disk, and how it reads and writes them.

Everything lives under ~/.nete (or $NETE_HOME), and everything belonging to one
preset lives in one directory:

    config.json                     default preset, where the model server is
    ace-step/                       the model server, if nete installed it
    scratch/rounds/<round>/         clips from before a preset has a name
    presets/<name>/
      preset.json                   the preset itself
      tracks/<variant>/*.flac       generated audio, the ingredients for a mix
      mixes/<id>.flac               one finished mix
      mixes/<id>.json               its cue sheet, play count and ratings
      references/<slug>.flac        music you want it to sound like
      references/<slug>.json        where it came from and what it measured
      rounds/<round>/*.flac         clips from its listening rounds
      transcripts/*.jsonl           what the agent and the listener said

One directory means deleting, renaming, backing up or sending someone a preset
is one move, and a rename cannot half-fail across three separate trees.

Tracks are the ingredients and mixes are the meals. Deleting a mix leaves the
tracks alone, so rebuilding is a minute rather than an hour.
"""

import datetime
import json
import os
import pathlib
import random
import re

HOME = pathlib.Path(os.environ.get("NETE_HOME", pathlib.Path.home() / ".nete"))

CONFIG = HOME / "config.json"
PRESETS = HOME / "presets"
REFERENCES = HOME / "references"
SCRATCH = HOME / "scratch"
ACE_STEP = HOME / "ace-step"

# Reserved, because rounds run before the listener has settled on a name.
SCRATCH_NAME = "_scratch"

DEFAULT_CONFIG = {
    "default_preset": None,
    "api": "http://127.0.0.1:8001",
    "ace_step": None,
    "agent": "claude",
}

# A preset is a plain dict with these keys. Anything missing falls back to the
# value here, so an older preset file keeps working when a key is added.
PRESET_DEFAULTS = {
    "name": "",
    "description": "",
    "prompt": "",
    "bpm": [100, 100],
    "keys": ["A minor"],
    "model": "acestep-v15-base",
    "steps": 32,
    "guidance": 7.0,
    "slow": 50,
    "bass": -18,
    "min_seconds": 20,
    "variants": {},
    "pattern": [],
    "crossfade": 8,
    # Slugs of shared references this preset aims at. The audio lives in
    # ~/.nete/references, so several presets can name the same one.
    "references": [],
    # Why each word is in the prompt, or why it is not. An editing agent reads
    # this so it does not retest something already settled or quietly undo it.
    # One entry per term: {"term", "verdict" in/out, "round", "score", "method"}.
    "decisions": [],
    "notes": "",
}

NAME_OK = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Ratings are 1 to 5. The words are only for printing; the number is what is
# stored and what decides play order.
RATINGS = {1: "bad", 2: "poor", 3: "OK", 4: "good", 5: "excellent"}

# Names for a preset that has not been named yet. Two short lists beat a
# dependency, and 24 by 24 is plenty for telling one unfinished session from
# another.
TEMP_ADJECTIVES = [
    "salty", "amber", "quiet", "copper", "velvet", "brisk", "hollow", "wild",
    "pale", "crooked", "gentle", "iron", "sleepy", "bitter", "hazy", "swift",
    "rusty", "mellow", "frosted", "gilded", "humid", "candid", "muted", "plucky",
]
TEMP_NOUNS = [
    "crackers", "lantern", "harbour", "thistle", "monsoon", "pigeon", "anvil",
    "marmalade", "glacier", "trombone", "kettle", "orchard", "beacon", "cactus",
    "ferry", "walnut", "sparrow", "quarry", "lagoon", "tungsten", "pebble",
    "almanac", "dovetail", "meridian",
]


def rating_word(value):
    value = normalise_rating(value)
    return f"{value} {RATINGS[value]}" if value else "-"


def normalise_rating(value):
    """Accept a number, or a numeric string, which is what a key press gives."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        if not value.isdigit():
            return None
        value = int(value)
    return value if value in RATINGS else None


def temp_name(rng=None):
    """A readable placeholder for a preset nobody has named yet, e.g. salty-crackers."""
    rng = rng or random.Random()
    taken = set(list_presets())
    for _ in range(200):
        candidate = f"{rng.choice(TEMP_ADJECTIVES)}-{rng.choice(TEMP_NOUNS)}"
        if candidate not in taken:
            return candidate
    raise NeteError("could not find an unused placeholder name; rename some presets")


class NeteError(Exception):
    """Something the user should read, not a stack trace."""


def now_stamp():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def ensure_home():
    for d in (HOME, PRESETS, REFERENCES, SCRATCH):
        d.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        write_config(DEFAULT_CONFIG)
    return HOME


def read_config():
    if not CONFIG.exists():
        return dict(DEFAULT_CONFIG)
    return {**DEFAULT_CONFIG, **json.loads(CONFIG.read_text())}


def write_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


def check_name(name):
    if not NAME_OK.match(name or ""):
        raise NeteError(f"preset names are lowercase letters, digits, dashes and "
                        f"underscores, starting with a letter or digit. Got {name!r}")
    return name


def preset_dir(name):
    return PRESETS / check_name(name)


def preset_path(name):
    return preset_dir(name) / "preset.json"


def list_presets():
    if not PRESETS.exists():
        return []
    return sorted(d.name for d in PRESETS.iterdir()
                  if d.is_dir() and (d / "preset.json").exists()
                  and d.name != SCRATCH_NAME)


def load_preset(name):
    path = preset_path(name)
    if not path.exists():
        known = ", ".join(list_presets()) or "none yet"
        raise NeteError(f"no preset called {name!r}. Have: {known}")
    return {**PRESET_DEFAULTS, **json.loads(path.read_text())}


def save_preset(preset):
    check_name(preset.get("name", ""))
    preset_dir(preset["name"]).mkdir(parents=True, exist_ok=True)
    preset_path(preset["name"]).write_text(json.dumps(preset, indent=2) + "\n")
    return preset_path(preset["name"])


def resolve_preset(name=None):
    """Use the named preset, or the default, and say so clearly when neither exists."""
    if name:
        return load_preset(name)
    default = read_config()["default_preset"]
    if not default:
        known = ", ".join(list_presets())
        raise NeteError("no preset given and no default set. "
                        + (f"Try: nete preset default <{known}>" if known
                           else "Make one first: nete preset create"))
    return load_preset(default)


def run_seconds(preset, variant):
    """How long this variant may play: its own number, held open to the floor."""
    return max(preset["variants"][variant]["seconds"], preset.get("min_seconds", 0))


def track_dir(preset_name, variant):
    return preset_dir(preset_name) / "tracks" / variant


def tracks_for(preset_name, variant):
    return sorted(track_dir(preset_name, variant).glob("*.flac"))


def mix_dir(preset_name):
    return preset_dir(preset_name) / "mixes"


def rounds_dir(preset_name=None):
    """Where a listening round's clips go.

    Rounds happen before the listener has settled on a name, so those land in
    scratch and `nete preset save` adopts them once there is a preset to own them.
    """
    if preset_name is None:
        return SCRATCH / "rounds"
    return preset_dir(preset_name) / "rounds"


def transcripts_dir(preset_name=None):
    if preset_name is None:
        return SCRATCH / "transcripts"
    return preset_dir(preset_name) / "transcripts"


def adopt_scratch(preset_name):
    """Move anything generated before the preset had a name into its directory."""
    moved = 0
    for kind in ("rounds", "transcripts"):
        src = SCRATCH / kind
        if not src.exists():
            continue
        dst = preset_dir(preset_name) / kind
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dst / item.name
            if not target.exists():
                item.rename(target)
                moved += 1
    return moved


def list_mixes(preset_name):
    """Every mix for a preset, newest first, each with its saved stats."""
    out = []
    folder = mix_dir(preset_name)
    if not folder.exists():
        return out
    for meta in sorted(folder.glob("*.json"), reverse=True):
        audio = meta.with_suffix(".flac")
        if audio.exists():
            out.append({**json.loads(meta.read_text()), "audio": audio, "meta": meta})
    return out


def save_mix_meta(meta_path, data):
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(data, indent=2) + "\n")


def new_mix_meta(preset_name, seconds, cues):
    return {
        "id": now_stamp(),
        "preset": preset_name,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "seconds": round(seconds, 1),
        "cues": cues,
        "plays": 0,
        "last_played": None,
        "rating": None,
        "variant_ratings": {},
    }


def rank_mixes(mixes):
    """Order for playback: never played first, then best rated, then least played.

    A mix you have not heard is always more interesting than one you have, so
    that comes first. After that a higher rating wins, an unrated mix sits where
    "OK" would, and among equals the one played least often comes up.
    """
    def key(m):
        rating = normalise_rating(m.get("rating")) or 3
        return (1 if m.get("plays", 0) else 0, -rating, m.get("plays", 0), m.get("id", ""))
    return sorted(mixes, key=key)


def self_check():
    assert check_name("cyber-punk_2") == "cyber-punk_2"
    for bad in ("", "Cyber", "-x", "a b", "a/b"):
        try:
            check_name(bad)
        except NeteError:
            pass
        else:
            raise AssertionError(f"{bad!r} should be rejected")

    p = {**PRESET_DEFAULTS, "min_seconds": 20,
         "variants": {"calm": {"seconds": 300}, "quick": {"seconds": 5}}}
    assert run_seconds(p, "calm") == 300
    assert run_seconds(p, "quick") == 20, "the floor holds short variants open"

    assert normalise_rating(4) == 4
    assert normalise_rating("4") == 4, "a key press arrives as a string"
    assert normalise_rating("good") is None, "the scale is numbers only"
    assert normalise_rating(0) is None and normalise_rating(9) is None
    assert normalise_rating(None) is None and normalise_rating("nonsense") is None
    assert rating_word(3) == "3 OK" and rating_word(None) == "-"

    name = temp_name(random.Random(1))
    assert check_name(name) == name, name
    assert name.count("-") == 1 and name.split("-")[0] in TEMP_ADJECTIVES

    order = rank_mixes([
        {"id": "c", "plays": 3, "rating": 5},
        {"id": "a", "plays": 0, "rating": 1},
        {"id": "d", "plays": 1, "rating": 2},
        {"id": "b", "plays": 2, "rating": None},
    ])
    assert [m["id"] for m in order] == ["a", "c", "b", "d"], [m["id"] for m in order]
    # Unrated must sit where OK would, not above an excellent one.
    pair = rank_mixes([{"id": "rated", "plays": 1, "rating": 5},
                       {"id": "unrated", "plays": 1, "rating": None}])
    assert [m["id"] for m in pair] == ["rated", "unrated"], pair
    print("store self-check ok")
