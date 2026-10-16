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

"""Handing a preset-building session to a coding agent.

`nete preset create` does not run the listening session itself. It starts a
coding agent in this terminal, with instructions describing the method and the
`nete` commands available, and gets out of the way. The agent asks the questions,
generates the clips, and writes the preset when the listener is happy.

Two agents are supported. Claude Code takes its instructions as a first message
plus an appended system prompt; OpenCode takes a first message. Both then run
interactively until the listener quits.

Afterwards there is a reflection step, and it runs on its own. The session id is
decided here and handed to the agent on launch, so when the agent exits nete
knows exactly which transcript to read: Claude Code writes one JSONL per session
under ~/.claude/projects/<encoded-cwd>/<id>.jsonl, and OpenCode hands one back
through `opencode session export <id>`. Nobody has to find a file or pass a
path.
"""

import datetime
import json
import pathlib
import shutil
import subprocess
import uuid

from . import store

PROMPTS = pathlib.Path(__file__).parent / "prompts"

# nete's own commands are pre-approved, so a session can generate clips and
# measure references without stopping to ask on every one. Nothing else is: the
# agent still prompts before touching anything outside nete.
ALLOWED = "Bash(nete *)"

AGENTS = {
    "claude": {
        "binary": "claude",
        "interactive": lambda prompt, system, model, session: [
            "claude", *(["--model", model] if model else []),
            *(["--session-id", session] if session else []),
            "--allowedTools", ALLOWED,
            "--append-system-prompt", system, prompt],
        "oneshot": lambda prompt, model: [
            "claude", *(["--model", model] if model else []), "--print", prompt],
    },
    # OpenCode has no separate system-prompt flag, so the instructions go in
    # ahead of the first message. Its own free models work without logging in:
    # `opencode models` lists them and they answered a test prompt unauthenticated.
    "opencode": {
        "binary": "opencode",
        # OpenCode has no per-tool allow list, only all-or-nothing, so its
        # sessions keep asking rather than being handed blanket approval.
        "interactive": lambda prompt, system, model, session: [
            "opencode", "run", *(["--model", model] if model else []),
            *(["--session", session] if session else []),
            f"{system}\n\n---\n\n{prompt}"],
        "oneshot": lambda prompt, model: [
            "opencode", "run", *(["--model", model] if model else []), prompt],
    },
}


def available():
    return [name for name, spec in AGENTS.items() if shutil.which(spec["binary"])]


def pick(requested=None):
    name = requested or store.read_config().get("agent") or "claude"
    if name not in AGENTS:
        raise store.NeteError(f"unknown agent {name!r}. Have: {', '.join(AGENTS)}")
    if not shutil.which(AGENTS[name]["binary"]):
        found = available()
        raise store.NeteError(
            f"{name} is not installed. "
            + (f"Try --agent {found[0]}" if found else f"Install one of: {', '.join(AGENTS)}"))
    return name


def read_prompt(filename):
    return (PROMPTS / filename).read_text()


def opening_message(preset=None, new_name=None):
    """What the agent is told to do first, as the listener's first message."""
    if preset:
        variants = ", ".join(preset["variants"]) or "none yet"
        settled = preset.get("decisions") or []
        kept = [d for d in settled if d.get("verdict") == "in"]
        rejected = [d for d in settled if d.get("verdict") == "out"]
        history = ""
        if settled:
            history = (
                "\n\nAlready settled by listening tests, so do not retest or quietly "
                "undo these:\n"
                + "".join(f"  IN   {d['term']} ({d.get('score', '')})\n" for d in kept)
                + "".join(f"  OUT  {d['term']} ({d.get('score', '')})\n" for d in rejected)
                + "\nIf I ask for something on the OUT list, say what it scored and what "
                  "it cost last time before you spend rounds on it again.")
        return (
            f"I want to change my existing nete preset '{preset['name']}'.\n\n"
            f"Its prompt is: {preset['prompt']}\n"
            f"Its versions are: {variants}"
            f"{history}\n\n"
            f"Run `nete preset show {preset['name']}` to see all of it. Ask me what "
            f"I want different, then run listening rounds the way your instructions "
            f"describe. Change as little as possible."
        )
    return (
        f"I want to build a new nete preset for background music to work to.\n\n"
        f"Call it '{new_name}' for now and save it under that name. It is a "
        f"placeholder; nete asks me for a real name when you are done, so do not "
        f"spend a question on it.\n\n"
        f"Ask me what it is for, then run listening rounds the way your instructions "
        f"describe. Start by checking the model server is up with `nete status`."
    )


def new_session_id(agent_name):
    """A session id nete chooses, so it can find the transcript afterwards."""
    return str(uuid.uuid4()) if agent_name == "claude" else f"nete-{uuid.uuid4().hex[:12]}"


def claude_transcript(session_id, projects_dir=None):
    """Claude writes one JSONL per session under ~/.claude/projects/<cwd>/<id>.jsonl.

    The same id can appear under more than one project directory if the session
    was resumed from elsewhere, so the newest match is the one holding the whole
    conversation.
    """
    base = projects_dir or (pathlib.Path.home() / ".claude" / "projects")
    matches = [m for m in base.glob(f"*/{session_id}.jsonl") if m.is_file()]
    return max(matches, key=lambda m: m.stat().st_mtime) if matches else None


def opencode_transcript(session_id, out_path):
    """OpenCode keeps sessions in its own store and exports one as JSON."""
    done = subprocess.run(["opencode", "session", "export", session_id],
                          capture_output=True, text=True)
    if done.returncode != 0 or not done.stdout.strip():
        return None
    out_path.write_text(done.stdout)
    return out_path


def capture_transcript(agent_name, session_id, destination):
    """Put this session's transcript where reflection can read it.

    Returns the path, or None when the agent left nothing behind, which happens
    if the session was abandoned before the first exchange.
    """
    if agent_name == "claude":
        found = claude_transcript(session_id)
        if found:
            shutil.copyfile(found, destination)
            return destination
        return None
    return opencode_transcript(session_id, destination)


def transcript_path(action, name=None):
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    folder = store.transcripts_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{stamp}-{action}.jsonl"


def run_session(agent_name, preset=None, model=None, new_name=None):
    """Start the agent in this terminal, wait for it, and bring back its transcript.

    Returns (transcript_path or None, session_id).
    """
    spec = AGENTS[agent_name]
    session_id = new_session_id(agent_name)
    cmd = spec["interactive"](opening_message(preset, new_name),
                              read_prompt("build_preset.md"), model, session_id)
    print(f"Starting {agent_name}. Quit it when you are done and nete will carry on.\n")
    subprocess.run(cmd, cwd=str(pathlib.Path.cwd()))

    dest = transcript_path("create" if preset is None else "edit",
                           preset["name"] if preset else None)
    found = capture_transcript(agent_name, session_id, dest)
    return found, session_id


def reflect(agent_name, transcript, model=None):
    """Ask an agent to read a finished session and propose instruction edits."""
    transcript = pathlib.Path(transcript)
    if not transcript.exists():
        candidate = store.TRANSCRIPTS / transcript.name
        if candidate.exists():
            transcript = candidate
        else:
            raise store.NeteError(f"no transcript at {transcript}")

    prompt = (read_prompt("reflect.md")
              + f"\n\nThe transcript is at: {transcript}\n"
              + f"The instructions are at: {PROMPTS / 'build_preset.md'}\n")
    cmd = AGENTS[agent_name]["oneshot"](prompt, model)
    print(f"Reading {transcript.name} with {agent_name}...\n")
    return subprocess.run(cmd).returncode


def self_check():
    assert set(AGENTS) == {"claude", "opencode"}
    fresh = opening_message(new_name="salty-crackers")
    assert "nete status" in fresh
    assert "salty-crackers" in fresh, "the agent has to know what to save it as"
    assert "placeholder" in fresh, "and that it is not the final name"
    p = {**store.PRESET_DEFAULTS, "name": "x", "prompt": "a b",
         "variants": {"calm": {"prompt": "c", "seconds": 60}}}
    msg = opening_message(p)
    assert "'x'" in msg and "calm" in msg
    assert "Change as little as possible" in msg

    # An editing agent must be handed what was already settled.
    with_history = opening_message({**p, "decisions": [
        {"term": "atmospheric", "verdict": "in", "score": "60-53"},
        {"term": "muffled", "verdict": "out", "score": "60-59"}]})
    assert "IN   atmospheric" in with_history, with_history
    assert "OUT  muffled" in with_history, with_history
    for name in AGENTS:
        built = AGENTS[name]["interactive"]("PROMPT", "SYSTEM", None, None)
        assert any("PROMPT" in part for part in built), name
        assert any("SYSTEM" in part for part in built), name
        with_model = AGENTS[name]["interactive"]("P", "S", "some/model", None)
        assert "some/model" in with_model, name
        with_session = AGENTS[name]["interactive"]("P", "S", None, "sess-1")
        assert "sess-1" in with_session, name
        assert "--model" in AGENTS[name]["oneshot"]("P", "m"), name
        assert "--model" not in AGENTS[name]["oneshot"]("P", None), name

    # Only nete's own commands are pre-approved, and nothing wider than that.
    claude_cmd = AGENTS["claude"]["interactive"]("P", "S", None, None)
    assert ALLOWED in claude_cmd
    assert "--dangerously-skip-permissions" not in claude_cmd
    assert "--allow-dangerously-skip-permissions" not in claude_cmd

    assert (PROMPTS / "build_preset.md").exists()
    assert (PROMPTS / "reflect.md").exists()

    # Claude needs a real UUID for --session-id; OpenCode takes any string.
    uuid.UUID(new_session_id("claude"))
    assert new_session_id("opencode").startswith("nete-")

    # Finding a Claude transcript is a glob across project dirs, newest wins.
    import tempfile
    base = pathlib.Path(tempfile.mkdtemp(prefix="nete_agent_check_"))
    try:
        (base / "proj-a").mkdir()
        (base / "proj-a" / "abc.jsonl").write_text("{}")
        assert claude_transcript("abc", base).name == "abc.jsonl"
        assert claude_transcript("missing", base) is None
    finally:
        shutil.rmtree(base)
    print("agent self-check ok")
