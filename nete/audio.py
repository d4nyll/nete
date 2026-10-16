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

"""Everything nete does to audio after the model has generated it.

The model responds to words naming what a thing is. It ignores words describing
how it should sound: `muffled`, `slow`, `subdued`, `tape saturation` and
`bitcrushed` were each tested against a listener and none of them changed
anything audible. So speed, brightness and weight are set here instead, where
they are arithmetic and cannot be ignored.

Pure standard library. afconvert, which ships with macOS, does the decoding and
encoding; everything in between works on plain 16-bit samples.
"""

import array
import math
import pathlib
import subprocess
import wave

BASS_CORNER_HZ = 120


def to_wav(src, dst):
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(src), str(dst)],
                   check=True, capture_output=True)


def to_flac(src, dst):
    subprocess.run(["afconvert", "-f", "flac", "-d", "flac", str(src), str(dst)],
                   check=True, capture_output=True)


def read_wav(path):
    with wave.open(str(path)) as w:
        params = w.getparams()
        data = array.array("h")
        data.frombytes(w.readframes(params.nframes))
    return data, params


def write_wav(path, data, params):
    with wave.open(str(path), "wb") as w:
        w.setparams(params._replace(nframes=len(data) // params.nchannels))
        w.writeframes(data.tobytes())


def to_m4a(src, dst, bitrate=128_000):
    """Make a small, universally playable copy. Returns the bitrate used.

    AAC in an MP4 container plays on phones, browsers and anything that handles
    video, and is a fraction of the size of the FLAC it came from. The FLAC stays
    as the master.

    The highest bitrate AAC will accept depends on the sample rate, and a mix
    slowed to half speed sits at 24 kHz where 192 kbps is refused outright. So
    the asked-for rate is tried first and then stepped down.
    """
    ladder = [b for b in (320_000, 256_000, 192_000, 160_000, 128_000,
                          96_000, 64_000, 48_000) if b <= bitrate]
    last = None
    for rate in ladder or [64_000]:
        done = subprocess.run(
            ["afconvert", "-f", "m4af", "-d", "aac", "-b", str(rate), str(src), str(dst)],
            capture_output=True, text=True)
        if done.returncode == 0 and dst.exists():
            return rate
        last = done.stderr.strip()
    raise RuntimeError(f"could not encode {src.name} as AAC at any bitrate: {last}")


def duration(path):
    """Seconds of audio in a file, read from afinfo."""
    out = subprocess.run(["afinfo", str(path)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "estimated duration" in line:
            return float(line.split(":")[1].strip().split()[0])
    return 0.0


def _run_biquad(data, nchannels, b0, b1, b2, a1, a2):
    out = array.array("h", bytes(len(data) * 2))
    for ch in range(nchannels):
        x1 = x2 = y1 = y2 = 0.0
        for i in range(ch, len(data), nchannels):
            x0 = data[i]
            y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, x0, y1, y0
            out[i] = -32768 if y0 < -32768 else (32767 if y0 > 32767 else int(y0))
    return out


def low_pass(data, framerate, nchannels, cutoff):
    """Remove the sound above ``cutoff`` wiggles per second, so it sits further back.

    Two stages at the pole Q values of a 4th-order Butterworth. Asking the model
    for a darker sound moved its spectral centre by 7% and could not be heard;
    this reaches 60%.
    """
    for q in (0.54120, 1.30656):
        w = 2 * math.pi * cutoff / framerate
        alpha, cos_w = math.sin(w) / (2 * q), math.cos(w)
        a0 = 1 + alpha
        data = _run_biquad(
            data, nchannels,
            (1 - cos_w) / 2 / a0, (1 - cos_w) / a0, (1 - cos_w) / 2 / a0,
            -2 * cos_w / a0, (1 - alpha) / a0)
    return data


def shelve_bass(data, framerate, nchannels, gain_db, corner=BASS_CORNER_HZ):
    """Make everything below ``corner`` quieter (or louder) by ``gain_db``.

    A negative number thins the deep sound without removing it, which is what
    "turn the bass down" means. Removing it outright takes the weight out.
    RBJ audio cookbook low-shelf, shelf slope 1.
    """
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * corner / framerate
    cos_w, sin_w = math.cos(w0), math.sin(w0)
    two_sqrt_a_alpha = 2 * math.sqrt(A) * (sin_w / 2 * math.sqrt(2))
    a0 = (A + 1) + (A - 1) * cos_w + two_sqrt_a_alpha
    return _run_biquad(
        data, nchannels,
        A * ((A + 1) - (A - 1) * cos_w + two_sqrt_a_alpha) / a0,
        2 * A * ((A - 1) - (A + 1) * cos_w) / a0,
        A * ((A + 1) - (A - 1) * cos_w - two_sqrt_a_alpha) / a0,
        -2 * ((A - 1) + (A + 1) * cos_w) / a0,
        ((A + 1) + (A - 1) * cos_w - two_sqrt_a_alpha) / a0)


def crossfade(tail, head, nchannels, n_frames):
    """Fade ``tail`` out and ``head`` in over the same stretch and add them.

    Equal-power ramps rather than straight lines, so the join holds a steady
    loudness instead of dipping in the middle.
    """
    out = array.array("h", bytes(n_frames * nchannels * 2))
    base = len(tail) // nchannels - n_frames
    for j in range(n_frames):
        t = j / n_frames
        fade_out, fade_in = math.cos(t * math.pi / 2), math.sin(t * math.pi / 2)
        for ch in range(nchannels):
            v = int(tail[(base + j) * nchannels + ch] * fade_out
                    + head[j * nchannels + ch] * fade_in)
            out[j * nchannels + ch] = -32768 if v < -32768 else (32767 if v > 32767 else v)
    return out


def slow_file(path, pct, seconds=None):
    """Replay a file's samples at a lower rate, the way a tape machine does.

    Stretches time and drops pitch by the same amount, which is where the extra
    depth comes from. It also doubles how much listening one generation yields,
    because the model will not generate more than 600 seconds at a time.

    ``seconds`` trims the result to that many seconds of playback.
    """
    wav = path.with_suffix(".wav")
    to_wav(path, wav)
    data, params = read_wav(wav)
    rate = int(params.framerate * pct / 100)
    if seconds:
        keep = int(seconds * rate) * params.sampwidth * params.nchannels
        data = array.array("h", data.tobytes()[:keep])
    out = path.with_name(path.stem + "-slowed.wav")
    write_wav(out, data, params._replace(framerate=rate))
    to_flac(out, path)
    wav.unlink()
    out.unlink()
    return path


def filter_file(path, fn):
    """Decode, run ``fn(data, framerate, nchannels)`` over the samples, re-encode."""
    wav = path.with_suffix(".wav")
    to_wav(path, wav)
    data, params = read_wav(wav)
    out = path.with_name(path.stem + "-filtered.wav")
    write_wav(out, fn(data, params.framerate, params.nchannels), params)
    to_flac(out, path)
    wav.unlink()
    out.unlink()
    return path


def self_check():
    # The ladder only ever steps down from what was asked for.
    import inspect
    source = inspect.getsource(to_m4a)
    assert "b <= bitrate" in source, "never encode above what the caller asked for"

    fs, nch = 24000, 2
    # A low-pass must leave a slow wiggle alone and flatten a fast one.
    def tone(hz, n=4800):
        d = array.array("h", bytes(n * nch * 2))
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * hz * i / fs))
            d[i * nch], d[i * nch + 1] = v, v
        return d

    def peak(d):
        return max(abs(v) for v in d[len(d) // 2:])

    slow, fast = tone(200), tone(6000)
    assert peak(low_pass(slow, fs, nch, 2000)) > 0.5 * peak(slow), "slow wiggle survives"
    assert peak(low_pass(fast, fs, nch, 2000)) < 0.2 * peak(fast), "fast wiggle removed"

    # Turning the bass down must shrink a deep tone and leave a bright one.
    deep, bright = tone(50), tone(4000)
    assert peak(shelve_bass(deep, fs, nch, -18)) < 0.35 * peak(deep)
    assert peak(shelve_bass(bright, fs, nch, -18)) > 0.8 * peak(bright)

    # A crossfade keeps its length, starts on the old track and ends on the new.
    a = array.array("h", [10000] * 800)
    b = array.array("h", [-10000] * 800)
    x = crossfade(a, b, nch, 100)
    assert len(x) == 200, len(x)
    assert abs(x[0] - 10000) < 250 and abs(x[-2] + 10000) < 250
    assert all(-32768 <= v <= 32767 for v in x)
    print("audio self-check ok")
