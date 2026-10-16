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

"""`nete init`: get the model server onto this machine and ready to answer.

Three things have to be true before anything else works: ~/.nete exists, a copy
of ACE-Step is installed with its Python environment, and the server answers on
its port. init does all three and says which ones were already done.

If ACE-Step is already installed elsewhere, point at it with `--ace-step` rather
than downloading 43 GB again.
"""

import pathlib
import shutil
import subprocess

from . import server, store

REPO = "https://github.com/ACE-Step/ACE-Step-1.5.git"


def have(tool):
    return shutil.which(tool) is not None


# Needed before anything works.
REQUIRED = ("git", "uv", "afconvert", "afplay")
# Needed only for reference tracks, and installable, so nete fetches these.
FOR_REFERENCES = ("yt-dlp", "ffmpeg")


def check_tools(report=print):
    missing = [t for t in REQUIRED if not have(t)]
    if missing:
        raise store.NeteError(
            "missing: " + ", ".join(missing) + "\n"
            "  git and uv:    brew install git uv\n"
            "  afconvert/afplay ship with macOS, so this may not be a Mac")
    install_reference_tools(report)


def install_reference_tools(report=print):
    """Get yt-dlp and ffmpeg, which reference tracks need, preferring brew.

    Not fatal when it fails: everything except `nete reference add` still works,
    so a machine without them is worth a warning rather than a refusal.
    """
    missing = [t for t in FOR_REFERENCES if not have(t)]
    if not missing:
        return []
    if have("brew"):
        report(f"installing {' and '.join(missing)} with brew")
        done = subprocess.run(["brew", "install", *missing])
        if done.returncode == 0:
            return []
    elif have("uv") and missing == ["yt-dlp"]:
        report("installing yt-dlp with uv")
        if subprocess.run(["uv", "tool", "install", "yt-dlp"]).returncode == 0:
            return []
    still = [t for t in FOR_REFERENCES if not have(t)]
    if still:
        report(f"could not install {', '.join(still)}; "
               f"`nete reference add` will not work until you do")
    return still


def install_ace_step(target, report):
    if (target / "pyproject.toml").exists():
        report(f"model code already at {target}")
    else:
        report(f"cloning ACE-Step into {target} (a few minutes)")
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", REPO, str(target)], check=True)

    if (target / ".venv").exists():
        report("python environment already built")
    else:
        report("building the python environment (a few minutes)")
        subprocess.run(["uv", "sync"], cwd=str(target), check=True)
    return target


def run(ace_step=None, start_server=True, report=print):
    check_tools()
    store.ensure_home()
    report(f"config at {store.HOME}")

    cfg = store.read_config()
    if ace_step:
        target = pathlib.Path(ace_step).expanduser().resolve()
        if not (target / "pyproject.toml").exists():
            raise store.NeteError(f"{target} does not look like an ACE-Step checkout")
        report(f"using the existing install at {target}")
    else:
        target = install_ace_step(store.ACE_STEP, report)

    cfg["ace_step"] = str(target)
    store.write_config(cfg)

    if not start_server:
        report("skipping server start")
        return target

    if server.is_up():
        report(f"server already running: {server.health().get('loaded_model')}")
    else:
        report("starting the model server; the first run downloads about 20 GB")
        info = server.start()
        report(f"server up: {info.get('loaded_model')}")

    report("")
    report("Ready. Next:")
    report("  nete preset create        build a preset by listening to clips")
    report("  nete preset list          see what you already have")
    return target


def self_check():
    assert REPO.endswith(".git")
    for tool in ("git", "uv", "afconvert", "afplay"):
        assert isinstance(have(tool), bool)
    print("setup self-check ok")
