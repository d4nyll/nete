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

"""Talking to the local ACE-Step model server, and generating one track.

The server is a separate process listening on 127.0.0.1:8001. nete starts it on
demand and leaves it running, because loading the model takes a couple of
minutes and a run of several tracks should pay that once.

Use a base model, never a turbo one. A turbo model forces the setting that
controls how hard it follows the prompt back to 1, so it barely hears the words
you give it: rounds of listening tests on turbo showed candidate wordings
drawing 60-59 and 58-58 that then had clear effects once the base model was used.
"""

import json
import random
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import audio, store


class ServerError(store.NeteError):
    pass


def api_url(path=""):
    return store.read_config()["api"].rstrip("/") + path


def post(path, payload, timeout=60):
    req = urllib.request.Request(
        api_url(path), data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # The server puts raw newlines inside JSON strings, so strict
            # parsing rejects its own output.
            return json.loads(r.read().decode(), strict=False)
    except urllib.error.HTTPError as e:
        raise ServerError(f"{api_url(path)} returned {e.code}: {e.read().decode()[:300]}")
    except (urllib.error.URLError, ConnectionError) as e:
        raise ServerError(f"cannot reach {api_url()} ({e}). Try: nete init")


def health(timeout=4):
    try:
        with urllib.request.urlopen(api_url("/health"), timeout=timeout) as r:
            return json.loads(r.read().decode(), strict=False)["data"]
    except Exception:
        return None


def is_up():
    return health() is not None


def start(wait_seconds=180):
    """Start the server in the background and wait for it to answer.

    Detached from this process, so it survives the command that started it and
    the next command does not pay the loading time again.
    """
    if is_up():
        return health()
    cfg = store.read_config()
    root = cfg.get("ace_step") and __import__("pathlib").Path(cfg["ace_step"])
    if not root or not root.exists():
        raise ServerError("no model server installed. Run: nete init")

    log = store.HOME / "server.log"
    env = {"ACESTEP_CONFIG_PATH": "acestep-v15-base", "ACESTEP_LM_BACKEND": "mlx",
           "TOKENIZERS_PARALLELISM": "false", "ACESTEP_INIT_LLM": "auto",
           "PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0.0"}
    import os
    with open(log, "ab") as out:
        subprocess.Popen(
            ["uv", "run", "acestep-api", "--host", "127.0.0.1", "--port", "8001"],
            cwd=str(root), env={**os.environ, **env},
            stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)

    for _ in range(wait_seconds):
        time.sleep(1)
        if is_up():
            return health()
    raise ServerError(f"server did not come up within {wait_seconds}s. See {log}")


def stop():
    killed = subprocess.run(["pkill", "-f", "acestep-api"], capture_output=True)
    return killed.returncode == 0


def generate_track(preset, variant, seconds, out_dir, rng=None, seed=None):
    """Generate one track and return where it landed.

    Applies the preset's speed and weight settings on the way out, because those
    two are part of what the preset *is* rather than a choice made per mix.
    """
    rng = rng or random.Random()
    prompt = preset["variants"][variant]["prompt"] if variant else preset["prompt"]
    if not prompt.rstrip().endswith("instrumental"):
        prompt = prompt.rstrip(", ") + ", instrumental"

    body = {
        "prompt": prompt,
        "lyrics": "[instrumental]",
        "bpm": rng.randint(*preset["bpm"]),
        "key_scale": rng.choice(preset["keys"]),
        "audio_duration": seconds,
        "inference_steps": preset["steps"],
        "guidance_scale": preset["guidance"],
        "batch_size": 1,
        "audio_format": "flac",
        "thinking": False,
        "use_cot_caption": False,
        "model": preset["model"],
    }
    if seed is not None:
        body["use_random_seed"] = False
        body["seed"] = seed

    task_id = post("/release_task", body)["data"]["task_id"]
    while True:
        time.sleep(4)
        entry = post("/query_result", {"task_id_list": [task_id]})["data"][0]
        if entry["status"] == 2:
            raise ServerError(f"the model failed on that request: {entry}")
        if entry["status"] == 1:
            break

    result = json.loads(entry["result"], strict=False)[0]
    src = _local_path(result["file"])
    used_seed = str(result.get("seed_value", "noseed")).split(",")[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{variant or 'default'}-{used_seed}.flac"
    shutil.move(src, dst)

    if preset.get("bass"):
        audio.filter_file(dst, lambda d, fr, nc: audio.shelve_bass(d, fr, nc, preset["bass"]))
    return dst


def _local_path(file_url):
    """Turn '/v1/audio?path=%2FUsers%2F...' into a path on this machine."""
    import pathlib
    query = urllib.parse.urlparse(file_url).query
    return pathlib.Path(urllib.parse.parse_qs(query)["path"][0])


def self_check():
    import pathlib
    assert _local_path("/v1/audio?path=%2Ftmp%2Fa%20b.flac") == pathlib.Path("/tmp/a b.flac")
    print("server self-check ok")
