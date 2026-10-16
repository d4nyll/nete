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

"""Reference tracks: music you want a preset to sound like.

Paste a YouTube link or point at a file and nete keeps a copy, measures it, and
remembers the numbers. The agent building a preset aims at those numbers instead
of guessing what a genre name means, which is how "dark cinematic" stopped
meaning strings in a preset whose reference had none.

References live in ~/.nete/references and a preset names the ones it uses. They
are large, they outlive the preset that prompted them, and two presets can want
the same track, so sharing one copy beats keeping one per preset. Deleting a
preset leaves its references alone.

Audio comes down through yt-dlp, which hands the stream to ffmpeg to make a
FLAC. The whole track is kept, both because the measurement reads all of it and
because the model server can condition on a reference recording, which is worth
having the audio for later.

Downloading from YouTube is against their terms of service. nete keeps the audio
and the measurements locally and sends nothing anywhere.
"""

import json
import pathlib
import re
import shutil
import subprocess

from . import rounds, store

URL_LIKE = re.compile(r"^https?://", re.I)
SAFE_SLUG = re.compile(r"[^a-zA-Z0-9_]+")


def is_url(text):
    return bool(URL_LIKE.match(str(text).strip()))


def slugify(text, fallback="reference"):
    """A filename-safe name. Punctuation becomes one dash, never a run of them."""
    slug = SAFE_SLUG.sub("-", str(text)).strip("-").lower()[:60].strip("-")
    return slug or fallback


def references_dir():
    return store.REFERENCES


def linked_slugs(preset_name):
    """Which references a preset names, with anything already deleted left out."""
    if not preset_name:
        return None
    preset = store.load_preset(preset_name)
    here = {a.stem for a in references_dir().glob("*.flac")}
    return [slug for slug in preset.get("references", []) if slug in here]


def link(slug, preset_name):
    """Have a preset name this reference. Returns True if that changed anything."""
    if not (references_dir() / f"{slug}.flac").exists():
        raise store.NeteError(f"no reference called {slug!r}. "
                              f"Add one: nete reference add <url or file>")
    preset = store.load_preset(preset_name)
    if slug in preset.get("references", []):
        return False
    preset["references"] = [*preset.get("references", []), slug]
    store.save_preset(preset)
    return True


def unlink(slug, preset_name):
    """Stop a preset naming this reference. The audio stays where it is."""
    preset = store.load_preset(preset_name)
    if slug not in preset.get("references", []):
        raise store.NeteError(f"{preset_name!r} does not name a reference {slug!r}")
    preset["references"] = [s for s in preset["references"] if s != slug]
    store.save_preset(preset)
    return True


def probe(url):
    """Ask yt-dlp what is at this URL without downloading it."""
    done = subprocess.run(
        ["yt-dlp", "--skip-download", "--no-warnings",
         "--print", "%(id)s\t%(title)s\t%(duration)s", url],
        capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise store.NeteError(f"could not read that link:\n{done.stderr.strip()[-400:]}")
    video_id, title, duration = (done.stdout.strip().split("\t") + ["", "", ""])[:3]
    return {"id": video_id, "title": title,
            "duration": float(duration) if duration.replace(".", "").isdigit() else None}


# YouTube hands out format URLs that it sometimes then refuses, so a 403 on one
# attempt says nothing about the next. Retrying the same URL has worked every
# time it has come up; asking for a different format is the second line.
FORMAT_CHOICES = ["bestaudio/best", "bestaudio[ext=m4a]/bestaudio", "best"]


def download(url, destination, report=None, attempts=3):
    """Fetch the audio-only stream and convert it to FLAC.

    Retries, because YouTube returns a 403 on a format URL often enough that a
    single failure is not a real answer.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for attempt in range(attempts):
        fmt = FORMAT_CHOICES[min(attempt, len(FORMAT_CHOICES) - 1)]
        if attempt and report:
            report(f"attempt {attempt + 1} of {attempts}, asking for format {fmt!r}")
        proc = subprocess.Popen(
            ["yt-dlp", "-f", fmt, "-x", "--audio-format", "flac",
             "--audio-quality", "0", "--no-warnings", "--no-playlist", "--newline",
             "--retries", "5", "--fragment-retries", "5", "--extractor-retries", "3",
             "-o", str(destination.with_suffix(".%(ext)s")), url],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.rstrip()
            if report and line:
                report(line)
            if "ERROR" in line:
                last_error = line
        if proc.wait() == 0 and destination.exists():
            return destination
        for leftover in destination.parent.glob(f"{destination.stem}.*"):
            if leftover != destination:
                leftover.unlink(missing_ok=True)
    raise store.NeteError(
        f"download failed after {attempts} attempts:\n{last_error or 'no error reported'}")


def add(source, preset_name=None, measure=True, report=None, progress=None):
    """Store a reference, measure it, and have ``preset_name`` name it if given.

    A URL already downloaded is not fetched again, whichever preset asked for it.
    ``report(line)`` gets download messages; ``progress(done, total)`` gets how
    far the measurement has read.
    """
    folder = references_dir()
    folder.mkdir(parents=True, exist_ok=True)

    if is_url(source):
        info = probe(source)
        slug = slugify(info["id"] or info["title"])
        audio = folder / f"{slug}.flac"
        record = {"slug": slug, "source": source, "title": info["title"],
                  "video_id": info["id"]}
        if not audio.exists():
            download(source, audio, report=report)
        elif report:
            report(f"already downloaded, reusing {audio.name}")
    else:
        path = pathlib.Path(source).expanduser()
        if not path.is_file():
            raise store.NeteError(f"no file at {path}")
        slug = slugify(path.stem)
        audio = folder / f"{slug}.flac"
        record = {"slug": slug, "source": str(path), "title": path.stem, "video_id": None}
        if not audio.exists():
            if path.suffix.lower() == ".flac":
                shutil.copyfile(path, audio)
            else:
                from . import audio as audio_tools
                tmp = folder / f"{slug}.wav"
                audio_tools.to_wav(path, tmp)
                audio_tools.to_flac(tmp, audio)
                tmp.unlink()

    # The audio path is not stored. A reference downloaded before the preset was
    # named gets moved into the preset's directory later, and a path written at
    # download time would still point at where it used to be. The file is always
    # <folder>/<slug>.flac, so deriving it survives the move.
    # Written before measuring, so a download that is interrupted during the
    # measurement still leaves a reference that can be measured later instead of
    # audio nobody has a record of.
    meta = folder / f"{slug}.json"
    meta.write_text(json.dumps(record, indent=2) + "\n")
    if measure:
        record["measurements"] = rounds.analyse(audio, report=progress)
        meta.write_text(json.dumps(record, indent=2) + "\n")
    if preset_name:
        link(slug, preset_name)
    return {**record, "audio": str(audio)}


def listing(preset_name=None):
    """Every reference, or just the ones a preset names.

    The audio is whatever sits next to the record, not whatever path the record
    was written with, so a reference keeps working after it is moved.
    """
    folder = references_dir()
    if not folder.exists():
        return []
    wanted = linked_slugs(preset_name) if preset_name else None
    out, seen = [], set()
    for meta in sorted(folder.glob("*.json")):
        # Descriptions live beside the records and end .description.json, so a
        # plain glob would read one as the other.
        if meta.name.endswith(".description.json"):
            continue
        record = json.loads(meta.read_text())
        if "slug" not in record:
            continue
        audio = folder / f"{record['slug']}.flac"
        if audio.exists() and (wanted is None or record["slug"] in wanted):
            out.append({**record, "audio": str(audio)})
            seen.add(record["slug"])
    for audio in sorted(folder.glob("*.flac")):
        if audio.stem not in seen and (wanted is None or audio.stem in wanted):
            out.append({"slug": audio.stem, "source": str(audio), "title": None,
                        "video_id": None, "audio": str(audio)})
    return out


def measure_missing(preset_name=None, report=None, progress=None):
    """Measure any reference whose numbers are not recorded yet."""
    done = []
    for record in listing(preset_name):
        if record.get("measurements"):
            continue
        if report:
            report(f"measuring {record['slug']}")
        record["measurements"] = rounds.analyse(record["audio"], report=progress)
        folder = references_dir()
        stored = {k: v for k, v in record.items() if k != "audio"}
        (folder / f"{record['slug']}.json").write_text(json.dumps(stored, indent=2) + "\n")
        done.append(record)
    return done


def remove(slug):
    """Delete a reference and stop every preset naming it."""
    folder = references_dir()
    gone = 0
    for suffix in (".flac", ".json"):
        path = folder / f"{slug}{suffix}"
        if path.exists():
            path.unlink()
            gone += 1
    if not gone:
        raise store.NeteError(f"no reference called {slug!r}")
    for name in store.list_presets():
        preset = store.load_preset(name)
        if slug in preset.get("references", []):
            preset["references"] = [s for s in preset["references"] if s != slug]
            store.save_preset(preset)
    return gone


def average(preset_name=None):
    """The middle of every reference's numbers, for a preset aimed at several.

    Each reference carries equal weight. A track twice as long does not get
    twice the say, because the question is what kind of sound to aim at rather
    than how much of it there is.
    """
    records = [r for r in listing(preset_name) if r.get("measurements")]
    if not records:
        return None
    keys = [k for k in records[0]["measurements"]
            if isinstance(records[0]["measurements"][k], (int, float))]
    return {k: round(sum(r["measurements"][k] for r in records) / len(records), 2)
            for k in keys}


def self_check():
    assert is_url("https://www.youtube.com/watch?v=abc")
    assert is_url("HTTP://example.com/x")
    assert not is_url("~/Music/track.mp3")
    assert not is_url("/Users/d/a.flac")

    assert slugify("Dark Synthwave Music - Devour") == "dark-synthwave-music-devour"
    assert slugify("nUtAWeSK28g") == "nutawesk28g"
    assert slugify("///") == "reference", "a name that sanitises to nothing still gets one"
    assert len(slugify("x" * 200)) <= 60

    import tempfile
    base = pathlib.Path(tempfile.mkdtemp(prefix="nete_ref_check_"))
    saved_refs, saved_presets = store.REFERENCES, store.PRESETS
    store.REFERENCES, store.PRESETS = base / "references", base / "presets"
    try:
        folder = references_dir()
        folder.mkdir(parents=True)
        store.PRESETS.mkdir(parents=True)
        for slug, bright in (("a", 1000), ("b", 2000)):
            (folder / f"{slug}.flac").write_bytes(b"x")
            # A path from somewhere else entirely, which is what a record
            # written before the reference was moved actually contains.
            (folder / f"{slug}.json").write_text(json.dumps(
                {"slug": slug, "audio": "/gone/elsewhere.flac",
                 "measurements": {"brightness_hz": bright, "tempo_bpm": 100}}))
        assert len(listing()) == 2, "a moved reference is still found"
        assert listing()[0]["audio"].endswith("/a.flac")
        assert average() == {"brightness_hz": 1500.0, "tempo_bpm": 100.0}

        store.save_preset({**store.PRESET_DEFAULTS, "name": "one"})
        store.save_preset({**store.PRESET_DEFAULTS, "name": "two"})
        assert listing("one") == [], "a new preset names no references"

        assert link("a", "one") is True
        assert link("a", "one") is False, "naming the same one twice changes nothing"
        assert link("a", "two") is True, "two presets can name the same reference"
        assert [r["slug"] for r in listing("one")] == ["a"]
        assert average("one") == {"brightness_hz": 1000.0, "tempo_bpm": 100.0}

        link("b", "one")
        assert average("one") == {"brightness_hz": 1500.0, "tempo_bpm": 100.0}
        unlink("b", "one")
        assert [r["slug"] for r in listing("one")] == ["a"]
        assert (folder / "b.flac").exists(), "unlinking leaves the audio alone"

        # Audio whose measurement never finished still shows up, unmeasured.
        (folder / "orphan.flac").write_bytes(b"x")
        found = {r["slug"]: r for r in listing()}
        assert "orphan" in found and not found["orphan"].get("measurements")
        assert average()["brightness_hz"] == 1500.0, "unmeasured ones are skipped"

        assert remove("a") == 2
        assert listing("one") == [], "deleting a reference unnames it everywhere"
        assert store.load_preset("two")["references"] == []
    finally:
        store.REFERENCES, store.PRESETS = saved_refs, saved_presets
        shutil.rmtree(base)
    print("references self-check ok")
