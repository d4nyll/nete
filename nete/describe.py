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

"""Asking a model what a reference actually sounds like.

The measurements say a track is bright or heavy. They cannot say it is a piano,
or rustling leaves, or a drone with bells over it. This runs an audio-language
model over a reference and writes down what it hears: the instruments, the
real-world sounds, what sits in front and what sits behind.

Two models never share this machine's memory. The captioner is about 8 billion
parameters and the generation server holds several more, so this stops the
server before loading anything and leaves it stopped. Start it again when you
want to generate.

The captioner lives in its own environment at ~/.nete/describe, because it needs
a newer transformers than the generation server will accept.
"""

import json
import pathlib
import shutil
import subprocess
import textwrap

from . import references, server, store

HOME = store.HOME / "describe"
VENV = HOME / ".venv"
MODEL = "nvidia/audio-flamingo-next-captioner-hf"

# Short enough that the model describes the whole window rather than narrating
# its opening. At 20 minutes it reported the first minute and stopped.
WINDOW_SECONDS = 300

PROMPT = (
    "Describe this audio. Name every instrument and sound source you hear, and any "
    "real-world or environmental sounds such as water, wind, birds or rustling "
    "leaves. Say which sounds sit in the foreground and which form the background. "
    "Describe the production: the space, the reverb, the texture, how wide or close "
    "it feels. Do not list chords. Do not state the duration, key or tempo."
)

RUNNER = textwrap.dedent('''
    """Caption one file in windows. Printed as JSON on stdout, progress on stderr."""
    import json, sys, time
    import librosa, torch
    from transformers import AutoProcessor, MusicFlamingoForConditionalGeneration
    from transformers.models.musicflamingo import modeling_musicflamingo as mf

    # The rotary time embedding is computed in float64, which Metal cannot do at
    # all, so this runs it in float32 on an Apple GPU and leaves other devices
    # with the original.
    _original = mf.apply_rotary_time_emb

    def _rotary_without_float64(hidden_states, cos, sin):
        if hidden_states.device.type != "mps":
            return _original(hidden_states, cos, sin)
        dtype = hidden_states.dtype
        h = hidden_states.to(torch.float32)
        cos, sin = cos.to(h), sin.to(h)
        width = cos.shape[-1]
        turned = (h[..., :width] * cos) + (mf.rotate_half(h[..., :width]) * sin)
        return torch.cat((turned, h[..., width:]), dim=-1).to(dtype)

    mf.apply_rotary_time_emb = _rotary_without_float64

    path, window, model_id, prompt = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    proc = AutoProcessor.from_pretrained(model_id)
    model = MusicFlamingoForConditionalGeneration.from_pretrained(
        model_id, dtype=torch.bfloat16).to(device).eval()
    print("READY", file=sys.stderr, flush=True)

    total = librosa.get_duration(path=path)
    chunks = []
    for start in range(0, int(total), window):
        audio, sr = librosa.load(path, sr=16000, mono=True, offset=start, duration=window)
        if len(audio) < sr * 10:
            break
        messages = [{"role": "user", "content": [{"type": "audio", "audio": audio},
                                                 {"type": "text", "text": prompt}]}]
        inputs = proc.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt").to(device, dtype=torch.bfloat16)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=260, do_sample=False)
        text = proc.batch_decode(out[:, inputs["input_ids"].shape[1]:],
                                 skip_special_tokens=True)[0].strip()
        chunks.append({"from": start, "to": start + int(len(audio) / sr), "text": text})
        print(f"PROGRESS {start + int(len(audio) / sr)} {int(total)}",
              file=sys.stderr, flush=True)
    print(json.dumps({"window": window, "chunks": chunks}))
''')


def installed():
    return (VENV / "bin" / "python").exists()


def install(report=print):
    """Build the captioner's own environment. Downloads about 15 GB on first use."""
    HOME.mkdir(parents=True, exist_ok=True)
    if not installed():
        report("building the describe environment")
        subprocess.run(["uv", "venv", "--python", "3.12", str(VENV)], check=True,
                       capture_output=True)
    report("installing its packages")
    subprocess.run(["uv", "pip", "install", "--python", str(VENV / "bin" / "python"), "-q",
                    "transformers>=5.18", "torch", "torchaudio", "soundfile",
                    "accelerate", "librosa"], check=True)
    return VENV


def describe_file(path, report=None, progress=None, window=WINDOW_SECONDS):
    """Caption one audio file. Stops the generation server first.

    Returns {"window": seconds, "chunks": [{"from", "to", "text"}, ...]}.
    """
    if not installed():
        raise store.NeteError("the describe model is not installed. Run: nete describe --install")

    if server.is_up():
        if report:
            report("stopping the generation server; these two models do not "
                   "share this machine's memory")
        server.stop()

    runner = HOME / "runner.py"
    runner.write_text(RUNNER)
    proc = subprocess.Popen(
        [str(VENV / "bin" / "python"), str(runner), str(path), str(window), MODEL, PROMPT],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**_env()})
    noise = []
    for line in proc.stderr:
        if line.startswith("PROGRESS ") and progress:
            done, total = line.split()[1:3]
            progress(float(done), float(total))
        elif line.startswith("READY") and report:
            report("model loaded")
        else:
            noise.append(line)
    out = proc.stdout.read()
    if proc.wait() != 0 or not out.strip():
        raise store.NeteError("could not describe that file:\n" + "".join(noise)[-600:])
    return json.loads(out)


def _env():
    import os
    # Keep the weights inside ~/.nete rather than the user's shared cache.
    return {**os.environ, "HF_HOME": str(HOME / "hf"), "TOKENIZERS_PARALLELISM": "false"}


def description_path(slug):
    return references.references_dir() / f"{slug}.description.json"


def load(slug):
    path = description_path(slug)
    return json.loads(path.read_text()) if path.exists() else None


def save(slug, description):
    description_path(slug).write_text(json.dumps(description, indent=2) + "\n")
    return description_path(slug)


def text_for(slug):
    """Every chunk of one description joined, or an empty string."""
    found = load(slug)
    return " ".join(c["text"] for c in found["chunks"]) if found else ""


def self_check():
    assert WINDOW_SECONDS == 300, "20-minute windows made it narrate only the opening"
    assert "Do not list chords" in PROMPT
    assert "rustling leaves" in PROMPT, "it has to be asked for real-world sounds"
    assert "float32" in RUNNER and "mps" in RUNNER, "the Metal float64 fix has to be in there"
    assert "server.stop()" in pathlib.Path(__file__).read_text(), \
        "describing must never run alongside the generation server"
    print("describe self-check ok")
