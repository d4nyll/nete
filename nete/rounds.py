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

"""Listening rounds: short clips for deciding one thing at a time.

A round holds every setting still except the one being tested, so a difference
you hear came from that one thing. Clips land in the preset's own rounds/<name>/ directory, with a
prompts.txt recording what produced them.

Two shapes:

- vary the prompts and pin the seed, to ask which wording is better
- pin the prompt and vary the seed, to ask whether one wording does anything
  that survives a different roll of the dice

The second shape feeds `blind`, which shuffles the two sets into A/B pairs and
keeps the answer key back until the listener has committed.
"""

import json
import pathlib
import random
import shutil
import subprocess
import textwrap

from . import audio, server, store


def round_dir(name, preset_name=None):
    return store.rounds_dir(preset_name) / store.check_name(name)


def list_rounds(preset_name=None):
    """Rounds for one preset, or every round anywhere when no preset is named."""
    bases = [store.rounds_dir(preset_name)] if preset_name else \
        [store.rounds_dir(None)] + [store.rounds_dir(n) for n in store.list_presets()]
    out = []
    for base in bases:
        if not base.exists():
            continue
        owner = base.parent.name if base.parent != store.SCRATCH else "scratch"
        for d in sorted(base.iterdir()):
            notes = d / "prompts.txt"
            if d.is_dir() and notes.exists():
                out.append((f"{owner}/{d.name}", notes.read_text().splitlines()[0]))
    return out


def clip(name, prompts, preset=None, seed=None, vary_seed=False, duration=20,
         seconds=None, slow=None, lowpass=None, bpm=None, key=None):
    """Generate one clip per prompt and return where they landed."""
    owner = preset["name"] if preset and preset.get("name") else None
    preset = preset or {**store.PRESET_DEFAULTS, "name": "round"}
    out = round_dir(name, owner)
    out.mkdir(parents=True, exist_ok=True)

    base = seed if seed is not None else random.randrange(1, 2 ** 31)
    draw = random.Random(base)
    seeds = [draw.randrange(1, 2 ** 31) for _ in prompts] if vary_seed else [base] * len(prompts)

    held = "prompt fixed, seed varies" if vary_seed else "prompt varies, seed fixed"
    lines = [f"base seed {base}  {bpm or preset['bpm']} bpm  {key or preset['keys'][0]}  "
             f"{duration}s  {preset['model']}  {preset['steps']} steps  "
             f"guidance {preset['guidance']}  ({held})", ""]

    made = []
    for i, (prompt, s) in enumerate(zip(prompts, seeds), 1):
        spec = {**preset, "prompt": prompt, "variants": {}}
        if bpm:
            spec["bpm"] = [bpm, bpm]
        if key:
            spec["keys"] = [key]
        # Rounds judge the wording, so the preset's own weight setting is left
        # off unless it was asked for explicitly.
        spec["bass"] = preset.get("bass", 0) if preset.get("name") != "round" else 0
        src = server.generate_track(spec, None, duration, out, seed=s)
        dst = out / f"{i}.flac"
        if dst.exists():
            dst.unlink()
        src.rename(dst)
        if slow:
            audio.slow_file(dst, slow, seconds)
        if lowpass:
            audio.filter_file(dst, lambda d, fr, nc: audio.low_pass(d, fr, nc, lowpass))
        made.append(dst)
        lines.append(f"{i}. seed {s}  {prompt}")

    (out / "prompts.txt").write_text("\n".join(lines) + "\n")
    return made


def blind(name, with_dir, without_dir, with_label="WITH", without_label="without",
          seed=None, preset_name=None):
    """Shuffle two matched sets into A/B pairs and write a key the listener should not read.

    Both sets must have been generated with --vary-seed off the same base seed,
    so pair N is the same roll of the dice on both sides and the only difference
    is the wording.
    """
    with_dir, without_dir = pathlib.Path(with_dir), pathlib.Path(without_dir)
    a_files = sorted(with_dir.glob("[0-9]*.flac"), key=lambda p: int(p.stem))
    b_files = sorted(without_dir.glob("[0-9]*.flac"), key=lambda p: int(p.stem))
    if not a_files or len(a_files) != len(b_files):
        raise store.NeteError(
            f"need the same number of clips in both folders, got "
            f"{len(a_files)} and {len(b_files)}")

    out = round_dir(name, preset_name)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    key = []
    for i, (a, b) in enumerate(zip(a_files, b_files), 1):
        arms = [(with_label, a), (without_label, b)]
        rng.shuffle(arms)
        for letter, (label, src) in zip("ab", arms):
            shutil.copyfile(src, out / f"pair{i}-{letter}.flac")
        key.append(f"pair {i}   A = {arms[0][0]:16} B = {arms[1][0]}")
    (out / "ANSWER-KEY-open-after-listening.txt").write_text("\n".join(key) + "\n")
    return out, key


ANALYSE_SRC = textwrap.dedent('''
    """Measure a whole track, however long, without loading it all at once."""
    import json, sys
    import numpy as np, soundfile as sf
    from scipy.ndimage import median_filter

    path = sys.argv[1]
    info = sf.info(path)
    sr, N, HOP = info.samplerate, 2048, 512
    window = np.hanning(N)

    # One minute at a time. The spectrogram of an hour would be gigabytes, and
    # the harmonic/percussive split copies it several times over.
    block = sr * 60
    done_frames = 0
    spectrum = np.zeros(N // 2 + 1)
    frames_seen = 0
    harmonic_energy = percussive_energy = 0.0
    flux_parts, loudness_parts = [], []
    tail = np.zeros(0, dtype="float32")

    for chunk in sf.blocks(path, blocksize=block, dtype="float32", always_2d=True):
        done_frames += len(chunk)
        # stderr, so the caller can show a progress line without it landing in
        # the JSON that stdout carries.
        print(f"PROGRESS {done_frames / sr:.0f} {info.frames / sr:.0f}",
              file=sys.stderr, flush=True)
        x = np.concatenate([tail, chunk.mean(1)])
        if len(x) < N:
            tail = x
            continue
        # Carry the last window over so frames are not lost at a seam.
        usable = (len(x) - N) // HOP * HOP + N
        frames = np.lib.stride_tricks.sliding_window_view(x[:usable], N)[::HOP] * window
        tail = x[usable - N + HOP:]

        S = np.abs(np.fft.rfft(frames, axis=1))
        spectrum += S.sum(0)
        frames_seen += S.shape[0]
        harmonic_energy += float((median_filter(S, size=(17, 1)) ** 2).sum())
        percussive_energy += float((median_filter(S, size=(1, 17)) ** 2).sum())
        flux_parts.append(np.maximum(0, np.diff(S, axis=0)).sum(1))
        loudness_parts.append(np.sqrt((S ** 2).sum(1)))

    flux = np.concatenate(flux_parts) if flux_parts else np.zeros(1)
    fps = sr / HOP
    churn = float(flux.std() / flux.mean()) if flux.mean() else 0.0

    # How long sound hangs on after a loud moment. Measured on loudness itself,
    # not on how fast loudness is changing: an earlier version used the second
    # and reported reverb-soaked tracks as drier than a dry one, because change
    # says nothing about ringing. Each peak is followed until the loudness has
    # halved, and the middle of those times is taken.
    envelope = np.concatenate(loudness_parts) if loudness_parts else np.zeros(2)
    decays = []
    threshold = np.median(envelope) + envelope.std()
    peaks = np.where((envelope[1:-1] > envelope[:-2]) & (envelope[1:-1] > envelope[2:])
                     & (envelope[1:-1] > threshold))[0] + 1
    for peak in peaks[:6000]:
        after = envelope[peak + 1:peak + int(fps * 6)]
        below = np.where(after < envelope[peak] / 2)[0]
        if len(below):
            decays.append(below[0] / fps)
    decay = float(np.median(decays)) if decays else 0.0

    # Autocorrelation through the frequency domain, because doing it directly
    # would be quadratic and an hour of audio is 420,000 flux values.
    centred = flux - flux.mean()
    size = 1 << (2 * len(centred) - 1).bit_length()
    spec = np.fft.rfft(centred, n=size)
    ac = np.fft.irfft(spec * np.conj(spec))[:len(centred)]
    lags = range(int(fps * 60 / 200), min(int(fps * 60 / 40) + 1, len(ac)))
    best = max(lags, key=lambda l: ac[l]) if len(ac) > 1 else 1

    f = np.fft.rfftfreq(N, 1 / sr)
    total = spectrum.sum() or 1.0
    band = lambda lo, hi: float(100 * spectrum[(f >= lo) & (f < hi)].sum() / total)
    both = (harmonic_energy + percussive_energy) or 1.0

    print(json.dumps({
        "seconds": round(info.frames / sr, 1),
        "analysed_seconds": round(frames_seen * HOP / sr, 1),
        "tempo_bpm": round(60 * fps / best),
        "beat_strength": round(float(ac[best] / ac[0]), 3) if ac[0] else 0.0,
        "percussive_pct": round(100 * percussive_energy / both, 1),
        "brightness_hz": round(float((f * spectrum).sum() / total)),
        "under_120hz_pct": round(band(20, 120), 1),
        "mid_500_2000_pct": round(band(500, 2000), 1),
        "churn": round(churn, 3),
        "decay_seconds": round(decay, 3),
    }, indent=2))
''')


def analyse(path, timeout=3600, report=None):
    """Measure a track: tempo, how much of it is beats, how bright and how heavy.

    Reads the whole track, a minute at a time, so an hour-long reference is
    measured in full rather than from its opening. Runs inside the model
    server's own environment, which already has the numerical libraries, so nete
    itself stays dependency-free.

    ``report(done_seconds, total_seconds)`` is called as it goes, because a
    ninety-minute track is a long time to look at a blank terminal.
    """
    cfg = store.read_config()
    root = cfg.get("ace_step")
    if not root or not pathlib.Path(root).exists():
        raise store.NeteError("analysis needs the model environment. Run: nete init")
    script = store.HOME / "analyse.py"
    script.write_text(ANALYSE_SRC)
    proc = subprocess.Popen(
        ["uv", "run", "python", str(script), str(pathlib.Path(path).expanduser())],
        cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    errors = []
    for line in proc.stderr:
        if line.startswith("PROGRESS ") and report:
            done, total = line.split()[1:3]
            report(float(done), float(total))
        elif not line.startswith("PROGRESS "):
            errors.append(line)
    out = proc.stdout.read()
    if proc.wait(timeout=timeout) != 0:
        raise store.NeteError("could not analyse that file:\n" + "".join(errors)[-500:])
    return json.loads(out)


def self_check():
    import tempfile
    base = pathlib.Path(tempfile.mkdtemp(prefix="nete_rounds_check_"))
    try:
        a, b = base / "with", base / "without"
        for d in (a, b):
            d.mkdir()
            for i in (1, 2, 3):
                (d / f"{i}.flac").write_bytes(b"x")
        saved, store.SCRATCH = store.SCRATCH, base / "scratch"
        try:
            out, key = blind("r1", a, b, seed=5)
            assert len(key) == 3, key
            assert len(list(out.glob("pair*.flac"))) == 6
            # Both labels must appear, or the shuffle is not shuffling.
            text = "\n".join(key)
            assert "WITH" in text and "without" in text
            assert (out / "ANSWER-KEY-open-after-listening.txt").exists()
        finally:
            store.SCRATCH = saved
    finally:
        shutil.rmtree(base)
    print("rounds self-check ok")
