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

"""The `nete` command line: parsing, dispatch, and printing.

Every command either prints something useful or explains what to run next. A
NeteError means the user needs to read one line and act on it, so those print
without a traceback.
"""

import argparse
import json
import pathlib
import random
import shutil
import sys

from . import (agent, audio, describe, mixer, player, references, rounds,
               server, setup_cmd, store, terms)

VERSION = "0.1.0"


# --------------------------------------------------------------------- helpers

def human(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def preset_from(args):
    return store.resolve_preset(getattr(args, "preset", None) or getattr(args, "name", None))


def ensure_server():
    if not server.is_up():
        print("starting the model server (first request after a start takes a minute)")
        server.start()


# -------------------------------------------------------------------- commands

def cmd_init(args):
    setup_cmd.run(ace_step=args.ace_step, start_server=not args.no_server)


def cmd_status(args):
    cfg = store.read_config()
    info = server.health()
    print(f"home            {store.HOME}")
    print(f"model code      {cfg.get('ace_step') or 'not installed, run: nete init'}")
    print(f"server          {'up, ' + str(info.get('loaded_model')) if info else 'down'}")
    print(f"default preset  {cfg.get('default_preset') or 'none set'}")
    print(f"agent           {cfg.get('agent')}  (installed: "
          f"{', '.join(agent.available()) or 'none'})")
    presets = store.list_presets()
    print(f"presets         {', '.join(presets) if presets else 'none yet'}")


def cmd_preset_list(args):
    names = store.list_presets()
    if not names:
        print("no presets yet. Make one: nete preset create")
        return
    default = store.read_config()["default_preset"]
    for name in names:
        p = store.load_preset(name)
        mixes = store.list_mixes(name)
        mark = "*" if name == default else " "
        print(f"{mark} {name}")
        print(f"    {p['prompt']}")
        refs = p.get("references") or []
        print(f"    {len(p['variants'])} version(s), {len(mixes)} mix(es), "
              f"{len(refs)} reference(s)"
              + (f", {p['description']}" if p["description"] else ""))


def cmd_preset_show(args):
    print(json.dumps(store.load_preset(args.name), indent=2))


def _rename_after_create(working_name):
    """Ask what to call the preset the agent just built, now that it exists."""
    if working_name not in store.list_presets():
        print(f"\nNo preset was saved as {working_name!r}, so there is nothing to name.")
        return None
    print(f"\nSaved as {working_name!r}, which was a placeholder.")
    while True:
        answer = input(f"What should it be called? [{working_name}] ").strip()
        if not answer or answer == working_name:
            return working_name
        try:
            store.check_name(answer)
            if answer in store.list_presets():
                print(f"{answer!r} already exists, pick another")
                continue
            _move_preset(working_name, answer)
            print(f"renamed to {answer}")
            return answer
        except store.NeteError as e:
            print(e)


def _move_preset(old, new):
    """Rename a preset: one directory move, then the name inside the file."""
    preset = store.load_preset(old)
    store.preset_dir(old).rename(store.preset_dir(new))
    preset["name"] = new
    store.save_preset(preset)
    cfg = store.read_config()
    if cfg["default_preset"] == old:
        cfg["default_preset"] = new
        store.write_config(cfg)


def _session_then_reflect(args, preset=None):
    """Run the listening session, then read its own transcript back and learn from it."""
    name = agent.pick(args.agent)
    working_name = None
    if preset is None:
        working_name = getattr(args, "name", None) or store.temp_name()
        store.check_name(working_name)
        if working_name in store.list_presets():
            raise store.NeteError(f"{working_name!r} already exists. "
                                  f"Change it with: nete preset edit {working_name}")
        print(f"Building a preset called {working_name!r}"
              + ("" if getattr(args, "name", None) else ", which you can rename at the end"))

    transcript, session_id = agent.run_session(name, preset=preset, model=args.model,
                                               new_name=working_name)
    if working_name and not getattr(args, "name", None):
        _rename_after_create(working_name)

    if transcript is None:
        print(f"\nNo transcript for session {session_id}; nothing to reflect on.")
        return
    print(f"\nTranscript saved: {transcript}")
    if args.no_reflect:
        print(f"Reflection skipped. Run it later: nete reflect {transcript.name}")
        return
    print("Reading the session back to improve the instructions "
          "(skip this next time with --no-reflect)\n")
    agent.reflect(name, transcript, model=args.model)


def cmd_preset_create(args):
    _session_then_reflect(args)


def cmd_preset_edit(args):
    _session_then_reflect(args, preset=store.load_preset(args.name))


def cmd_preset_delete(args):
    preset = store.load_preset(args.name)
    folder = store.preset_dir(preset["name"])
    if not args.yes:
        print(f"This removes {folder}: the preset, "
              f"{len(store.list_mixes(preset['name']))} mix(es), its tracks, "
              f"its rounds and its transcripts.")
        if input("Type the preset name to confirm: ").strip() != preset["name"]:
            print("left alone")
            return
    shutil.rmtree(folder)
    cfg = store.read_config()
    if cfg["default_preset"] == preset["name"]:
        cfg["default_preset"] = None
        store.write_config(cfg)
        print("that was the default, so there is no default now")
    print(f"deleted {preset['name']}")


def cmd_preset_rename(args):
    store.load_preset(args.old)
    store.check_name(args.new)
    if store.preset_path(args.new).exists():
        raise store.NeteError(f"{args.new!r} already exists")
    _move_preset(args.old, args.new)
    print(f"{args.old} is now {args.new}")


def cmd_preset_default(args):
    preset = store.load_preset(args.name)
    cfg = store.read_config()
    cfg["default_preset"] = preset["name"]
    store.write_config(cfg)
    print(f"default preset is now {preset['name']}")


def cmd_preset_save(args):
    """Write a preset from a JSON file or stdin. This is how an agent saves one."""
    text = sys.stdin.read() if args.file == "-" else open(args.file).read()
    preset = {**store.PRESET_DEFAULTS, **json.loads(text)}
    path = store.save_preset(preset)
    adopted = store.adopt_scratch(preset["name"])
    if adopted:
        print(f"moved {adopted} item(s) from scratch into {store.preset_dir(preset['name'])}")
    if not store.read_config()["default_preset"]:
        cfg = store.read_config()
        cfg["default_preset"] = preset["name"]
        store.write_config(cfg)
        print(f"saved {path} and made it the default")
    else:
        print(f"saved {path}")


def cmd_generate(args):
    preset = preset_from(args)
    ensure_server()
    rng = random.Random()
    wanted = args.tracks
    for variant in (args.variant and [args.variant] or list(preset["variants"])):
        have = store.tracks_for(preset["name"], variant)
        for i in range(max(0, wanted - len(have))):
            print(f"  {variant}: track {len(have) + i + 1}/{wanted}", flush=True)
            track = server.generate_track(
                preset, variant, args.duration, store.track_dir(preset["name"], variant), rng)
            if preset.get("slow"):
                audio.slow_file(track, preset["slow"])
    if args.tracks_only:
        return
    print(f"building a {args.minutes}-minute mix")
    meta = mixer.build(preset, minutes=args.minutes, seed=args.seed,
                       on_segment=lambda i, n, v, f: print(f"  [{i}/{n}] {v}"))
    print(f"\n{store.mix_dir(preset['name']) / (meta['id'] + '.flac')}  "
          f"{human(meta['seconds'])}, {len(meta['cues'])} segments")
    print(f"play it: nete play {preset['name']}")


def cmd_play(args):
    preset = preset_from(args)
    player.play(preset)


def cmd_mix_list(args):
    preset = preset_from(args)
    mixes = store.rank_mixes(store.list_mixes(preset["name"]))
    if not mixes:
        print(f"no mixes for {preset['name']}. Make one: nete generate {preset['name']}")
        return
    print(f"{'id':22} {'length':>7} {'size':>8} {'plays':>6} {'rating':>12}  versions")
    for m in mixes:
        versions = ", ".join(sorted({c[1] for c in m.get("cues", [])}))
        size = m["audio"].stat().st_size / 1e6
        print(f"{m['id']:22} {human(m['seconds']):>7} {size:7.0f}M {m.get('plays', 0):6} "
              f"{store.rating_word(m.get('rating')):>12}  {versions}")
        marks = m.get("variant_ratings") or {}
        if marks:
            print("    " + "   ".join(f"{v}: {store.rating_word(r)}"
                                      for v, r in sorted(marks.items())))


def cmd_mix_export(args):
    preset = preset_from(args)
    mixes = store.list_mixes(preset["name"])
    if args.id == "latest":
        chosen = mixes[:1]
    else:
        chosen = [m for m in mixes if m["id"] == args.id]
    if not chosen:
        raise store.NeteError(f"no mix {args.id!r} for {preset['name']}. "
                              f"See: nete preset mix list {preset['name']}")
    out_dir = pathlib.Path(args.out).expanduser() if args.out else pathlib.Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    for m in chosen:
        target = out_dir / f"{preset['name']}-{m['id']}.m4a"
        print(f"converting {human(m['seconds'])} of audio")
        used = audio.to_m4a(m["audio"], target, bitrate=args.bitrate * 1000)
        before = m["audio"].stat().st_size / 1e6
        after = target.stat().st_size / 1e6
        note = "" if used == args.bitrate * 1000 else \
            f" (asked for {args.bitrate}k; this sample rate would not take it)"
        print(f"{target}\n  {before:.0f} MB -> {after:.0f} MB "
              f"({after / before * 100:.0f}% of the original) at {used // 1000}k{note}")


def cmd_mix_delete(args):
    preset = preset_from(args)
    found = [m for m in store.list_mixes(preset["name"])
             if args.id in ("all", m["id"])]
    if not found:
        raise store.NeteError(f"no mix {args.id!r} for {preset['name']}")
    for m in found:
        m["audio"].unlink(missing_ok=True)
        m["meta"].unlink(missing_ok=True)
    print(f"deleted {len(found)} mix(es)")


def cmd_clip(args):
    preset = store.load_preset(args.preset) if args.preset else None
    ensure_server()
    made = rounds.clip(args.round, args.prompts, preset=preset, seed=args.seed,
                       vary_seed=args.vary_seed, duration=args.duration,
                       seconds=args.seconds, slow=args.slow, lowpass=args.lowpass,
                       bpm=args.bpm, key=args.key)
    d = rounds.round_dir(args.round, args.preset)
    for i, path in enumerate(made, 1):
        print(f"{i}. {path}")
    print(f'\nplay: for f in {d}/*.flac; do echo "$(basename "$f")"; afplay "$f"; done')


def cmd_blind(args):
    out, key = rounds.blind(args.round, args.with_dir, args.without_dir,
                            seed=args.seed, preset_name=args.preset)
    print(f"{len(key)} pairs in {out}")
    print(f'play: for f in {out}/pair*.flac; do echo "$(basename "$f")"; afplay "$f"; done')
    print("the answer key is in that folder; do not open it until they have answered")


def cmd_rounds(args):
    found = rounds.list_rounds(args.preset)
    if not found:
        print("no rounds yet")
        return
    for name, header in found:
        print(f"{name:28} {header}")


def cmd_analyse(args):
    if references.is_url(args.file):
        print("downloading before measuring (this is not stored; "
              "use `nete reference add` to keep it)")
        record = references.add(args.file, preset_name=None,
                                report=_download_progress, progress=_measure_progress)
        _status_line("")
        print("\r" + json.dumps(record["measurements"], indent=2))
        references.remove(record["slug"], None)
        return
    result = rounds.analyse(args.file, report=_measure_progress)
    _status_line("")
    print("\r" + json.dumps(result, indent=2))


def _status_line(text):
    """One line that keeps being overwritten, so a long job does not scroll away."""
    width = shutil.get_terminal_size((100, 24)).columns - 1
    sys.stdout.write("\r" + text[:width].ljust(width))
    sys.stdout.flush()


def _download_progress(line):
    # yt-dlp with --newline gives one line per update; only the useful ones.
    if line.startswith("[download]") or line.startswith("[ExtractAudio]"):
        _status_line("  " + line.strip())


def _measure_progress(done, total):
    share = done / total if total else 0
    bar = "#" * int(share * 20) + "." * (20 - int(share * 20))
    _status_line(f"  measuring [{bar}] {human(done)} of {human(total)}")


def cmd_reference_add(args):
    for source in args.sources:
        print(f"adding {source}")
        record = references.add(source, preset_name=args.preset,
                                report=_download_progress, progress=_measure_progress)
        _status_line("")
        print("\r", end="")
        print(f"  {record['title'] or record['slug']}")
        for key, value in record["measurements"].items():
            print(f"    {key:22} {value}")
    middle = references.average(args.preset) if args.preset else None
    if middle and len(references.listing(args.preset)) > 1:
        print("\naverage across every reference for this preset:")
        for key, value in middle.items():
            print(f"  {key:22} {value}")


def cmd_reference_list(args):
    records = references.listing(args.preset)
    if not records:
        where = f"named by {args.preset}" if args.preset else "stored"
        print(f"no references {where}. Add one: nete reference add <url or file>"
              + (f" --preset {args.preset}" if args.preset else ""))
        return
    using = {} if args.preset else {
        r["slug"]: [n for n in store.list_presets()
                    if r["slug"] in store.load_preset(n).get("references", [])]
        for r in records}
    for r in records:
        m = r.get("measurements", {})
        print(f"{r['slug']}")
        print(f"    {r.get('title') or '-'}")
        print(f"    {r.get('source')}")
        if m:
            print(f"    {human(m.get('seconds', 0))}  {m.get('tempo_bpm')} bpm  "
                  f"beat {m.get('beat_strength')}  {m.get('percussive_pct')}% beats  "
                  f"{m.get('brightness_hz')} Hz bright  "
                  f"{m.get('under_120hz_pct')}% deep")
        else:
            print("    not measured yet. Run: nete reference measure")
        if not args.preset:
            names = using.get(r["slug"]) or []
            print(f"    used by: {', '.join(names) if names else 'no preset'}")
    middle = references.average(args.preset) if len(records) > 1 else None
    if middle:
        print("\naverage:")
        for key, value in middle.items():
            print(f"  {key:22} {value}")


def cmd_describe(args):
    if args.install:
        describe.install()
        print("ready. Now: nete describe <reference slug>")
        return
    records = references.listing(args.preset)
    wanted = [r for r in records if args.slug in (None, r["slug"])]
    if not wanted:
        raise store.NeteError(f"no reference {args.slug!r}. See: nete reference list")
    todo = [r for r in wanted if args.again or not describe.load(r["slug"])]
    if not todo:
        print("already described. Use --again to redo one.")
        return
    print(f"describing {len(todo)} reference(s), one at a time")
    for record in todo:
        print(f"\n{record['slug']}  {record.get('title') or ''}")
        found = describe.describe_file(
            record["audio"], report=lambda t: print(f"  {t}"),
            progress=_measure_progress)
        _status_line("")
        describe.save(record["slug"], found)
        print(f"\r  {len(found['chunks'])} window(s) described")
        print("  " + found["chunks"][0]["text"][:400] + "...")


def cmd_terms(args):
    preset = preset_from(args)
    texts = [describe.text_for(r["slug"]) for r in references.listing(preset["name"])]
    texts = [t for t in texts if t]
    if not texts:
        raise store.NeteError(
            f"{preset['name']} has no described references yet.\n"
            f"  nete describe --preset {preset['name']}")
    found = terms.candidates_for(preset, texts, limit=args.limit)
    print(f"{len(found)} candidate wording(s) from {len(texts)} description(s)\n")
    for c in found:
        mark = "thing" if c["names_a_thing"] else "quality"
        print(f"  {c['wording']:46} {c['dimension'] or 'ears only':12} {mark:8} "
              f"x{c['mentions']}")


def cmd_screen(args):
    preset = preset_from(args)
    texts = [describe.text_for(r["slug"]) for r in references.listing(preset["name"])]
    found = terms.candidates_for(preset, [t for t in texts if t], limit=args.limit)
    if not found:
        raise store.NeteError("nothing to screen. Run: nete describe, then nete terms")
    ensure_server()
    measurable = [c for c in found if c["dimension"]]
    print(f"{len(found)} candidates, {len(measurable)} of them measurable. "
          f"Screening those, one at a time.\n")
    screened = []
    for i, candidate in enumerate(found, 1):
        print(f"[{i}/{len(found)}] {candidate['wording']}")
        screened.append(terms.screen_one(preset, candidate, pairs=args.pairs,
                                         base_seed=args.seed,
                                         report=lambda t: print(t, flush=True)))
        last = screened[-1]
        if last["status"] == "moved":
            print(f"  moved {last['metric']} by {last['effect'] * 100:.0f}% "
                  f"({last['without']} -> {last['with']})")
        elif last["status"] == "flat":
            print(f"  moved {last['metric']} by {last['effect'] * 100:.0f}%, "
                  f"so nothing a listener would notice")
        else:
            print("  nothing measures this; it goes to you unscreened")

    ordered = terms.rank(screened)
    path = store.preset_dir(preset["name"]) / "candidates.json"
    path.write_text(json.dumps(ordered, indent=2) + "\n")
    print(f"\nRunning order written to {path}\n")
    for i, c in enumerate(ordered, 1):
        effect = f"{c['effect'] * 100:3.0f}%" if c.get("effect") is not None else "  -"
        print(f"{i:3}. {effect}  {c['status']:13} {c['wording']}")


def cmd_reference_measure(args):
    done = references.measure_missing(args.preset, report=lambda t: print(t),
                                      progress=_measure_progress)
    _status_line("")
    if not done:
        print("\reverything is already measured")
        return
    print("\r", end="")
    for record in done:
        print(f"{record['slug']}")
        for key, value in record["measurements"].items():
            print(f"    {key:22} {value}")


def cmd_reference_remove(args):
    references.remove(args.slug)
    print(f"removed {args.slug}, and stopped any preset naming it")


def cmd_reference_link(args):
    changed = references.link(args.slug, args.preset)
    print(f"{args.preset} now uses {args.slug}" if changed
          else f"{args.preset} already used {args.slug}")


def cmd_reference_unlink(args):
    references.unlink(args.slug, args.preset)
    print(f"{args.preset} no longer uses {args.slug}; the audio is still stored")


def cmd_reflect(args):
    name = agent.pick(args.agent)
    agent.reflect(name, args.transcript, model=args.model)


def cmd_server(args):
    if args.action == "start":
        print(server.start())
    elif args.action == "stop":
        print("stopped" if server.stop() else "was not running")
    else:
        info = server.health()
        print(json.dumps(info, indent=2) if info else "down")


def cmd_self_check(args):
    for module in (store, audio, server, mixer, player, rounds, references,
                   describe, terms, agent, setup_cmd):
        module.self_check()
    print("all self-checks ok")


# ---------------------------------------------------------------------- parser

def build_parser():
    p = argparse.ArgumentParser(
        prog="nete", description="Background music, generated and mixed on this machine.")
    p.add_argument("--version", action="version", version=f"nete {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="install the model server and set up ~/.nete")
    init.add_argument("--ace-step", help="use an existing ACE-Step checkout instead of cloning")
    init.add_argument("--no-server", action="store_true", help="set up but do not start it")
    init.set_defaults(func=cmd_init)

    sub.add_parser("status", help="what is installed, running and set").set_defaults(
        func=cmd_status)

    srv = sub.add_parser("server", help="start, stop or query the model server")
    srv.add_argument("action", choices=["start", "stop", "status"], nargs="?", default="status")
    srv.set_defaults(func=cmd_server)

    gen = sub.add_parser("generate", help="generate tracks and build a mix")
    _add_generate_args(gen)
    gen.set_defaults(func=cmd_generate)

    play = sub.add_parser("play", help="play mixes with a small screen and rating keys")
    play.add_argument("preset", nargs="?")
    play.set_defaults(func=cmd_play)

    preset = sub.add_parser("preset", help="create, list, edit and delete presets")
    psub = preset.add_subparsers(dest="preset_command", required=True)

    for alias in ("list", "ls"):
        psub.add_parser(alias, help="list presets and their prompts").set_defaults(
            func=cmd_preset_list)

    show = psub.add_parser("show", help="print one preset as JSON")
    show.add_argument("name")
    show.set_defaults(func=cmd_preset_show)

    create = psub.add_parser("create", help="build a preset by listening to clips")
    create.add_argument("name", nargs="?",
                        help="what to call it; left out, nete picks a placeholder "
                             "and asks you at the end")
    _add_agent_args(create)
    create.set_defaults(func=cmd_preset_create)

    edit = psub.add_parser("edit", help="change an existing preset the same way")
    edit.add_argument("name")
    _add_agent_args(edit)
    edit.set_defaults(func=cmd_preset_edit)

    delete = psub.add_parser("delete", help="remove a preset, its tracks and its mixes")
    delete.add_argument("name")
    delete.add_argument("-y", "--yes", action="store_true")
    delete.set_defaults(func=cmd_preset_delete)

    rename = psub.add_parser("rename", help="rename a preset and move its files")
    rename.add_argument("old")
    rename.add_argument("new")
    rename.set_defaults(func=cmd_preset_rename)

    default = psub.add_parser("default", help="use this preset when none is named")
    default.add_argument("name")
    default.set_defaults(func=cmd_preset_default)

    save = psub.add_parser("save", help="write a preset from a JSON file or stdin")
    save.add_argument("file", nargs="?", default="-")
    save.set_defaults(func=cmd_preset_save)

    pgen = psub.add_parser("generate", help="same as: nete generate <preset>")
    _add_generate_args(pgen)
    pgen.set_defaults(func=cmd_generate)

    mix = psub.add_parser("mix", help="list, delete and build mixes")
    msub = mix.add_subparsers(dest="mix_command", required=True)
    for alias in ("list", "ls"):
        m = msub.add_parser(alias, help="mixes for a preset, with plays and ratings")
        m.add_argument("preset", nargs="?")
        m.set_defaults(func=cmd_mix_list)
    mexp = msub.add_parser("export", help="write a small, universally playable copy")
    mexp.add_argument("id", nargs="?", default="latest", help="a mix id, or 'latest'")
    mexp.add_argument("preset", nargs="?")
    mexp.add_argument("--out", help="where to write it (default: here)")
    mexp.add_argument("--bitrate", type=int, default=96,
                      help="kbps (default 96; these mixes sit at 24 kHz and are "
                           "low-passed, so 64 is usually transparent too)")
    mexp.set_defaults(func=cmd_mix_export)

    mdel = msub.add_parser("delete", help="delete one mix, or all of them")
    mdel.add_argument("id", help="a mix id, or 'all'")
    mdel.add_argument("preset", nargs="?")
    mdel.set_defaults(func=cmd_mix_delete)
    mgen = msub.add_parser("generate", help="same as: nete generate <preset>")
    _add_generate_args(mgen)
    mgen.set_defaults(func=cmd_generate)

    clip = sub.add_parser("clip", help="generate comparison clips for a listening round")
    clip.add_argument("round")
    clip.add_argument("prompts", nargs="+")
    clip.add_argument("--preset", help="take generation settings from this preset")
    clip.add_argument("--seed", type=int)
    clip.add_argument("--vary-seed", action="store_true",
                      help="give each clip its own seed; repeat one prompt to use this")
    clip.add_argument("--duration", type=int, default=20, help="seconds generated (min 10)")
    clip.add_argument("--seconds", type=int, help="seconds heard, after slowing")
    clip.add_argument("--slow", type=int, metavar="PCT")
    clip.add_argument("--lowpass", type=int, metavar="HZ")
    clip.add_argument("--bpm", type=int)
    clip.add_argument("--key")
    clip.set_defaults(func=cmd_clip)

    blind = sub.add_parser("blind", help="shuffle two matched sets into A/B pairs")
    blind.add_argument("round")
    blind.add_argument("with_dir")
    blind.add_argument("without_dir")
    blind.add_argument("--seed", type=int)
    blind.add_argument("--preset", help="store the pairs under this preset")
    blind.set_defaults(func=cmd_blind)

    rnd = sub.add_parser("rounds", help="list listening rounds")
    rnd.add_argument("preset", nargs="?")
    rnd.set_defaults(func=cmd_rounds)

    analyse = sub.add_parser("analyse",
                             help="measure a track or a link: tempo, beat, brightness, weight")
    analyse.add_argument("file", help="a path, or a URL to download and measure")
    analyse.set_defaults(func=cmd_analyse)

    desc = sub.add_parser("describe", help="have a model say what a reference sounds like")
    desc.add_argument("slug", nargs="?", help="one reference, or all of them")
    desc.add_argument("--preset", help="only this preset's references")
    desc.add_argument("--again", action="store_true", help="redo one already described")
    desc.add_argument("--install", action="store_true", help="set up the describe model")
    desc.set_defaults(func=cmd_describe)

    tm = sub.add_parser("terms", help="candidate wordings pulled from the descriptions")
    tm.add_argument("preset", nargs="?")
    tm.add_argument("--limit", type=int)
    tm.set_defaults(func=cmd_terms)

    scr = sub.add_parser("screen", help="try each wording and measure what it changes")
    scr.add_argument("preset", nargs="?")
    scr.add_argument("--limit", type=int)
    scr.add_argument("--pairs", type=int, default=3, help="seed-matched pairs each (default 3)")
    scr.add_argument("--seed", type=int, default=0)
    scr.set_defaults(func=cmd_screen)

    ref = sub.add_parser("reference", help="music a preset should sound like")
    rsub = ref.add_subparsers(dest="reference_command", required=True)
    radd = rsub.add_parser("add", help="download or copy a track, keep it, measure it")
    radd.add_argument("sources", nargs="+", help="YouTube links or local files")
    radd.add_argument("--preset", help="keep them with this preset (default: scratch)")
    radd.set_defaults(func=cmd_reference_add)
    for alias in ("list", "ls"):
        rls = rsub.add_parser(alias, help="references and what they measured")
        rls.add_argument("--preset")
        rls.set_defaults(func=cmd_reference_list)
    rmeasure = rsub.add_parser("measure", help="measure any reference missing its numbers")
    rmeasure.add_argument("--preset")
    rmeasure.set_defaults(func=cmd_reference_measure)

    rlink = rsub.add_parser("link", help="have a preset use a reference that is already stored")
    rlink.add_argument("slug")
    rlink.add_argument("preset")
    rlink.set_defaults(func=cmd_reference_link)

    runlink = rsub.add_parser("unlink", help="stop a preset using one; the audio stays")
    runlink.add_argument("slug")
    runlink.add_argument("preset")
    runlink.set_defaults(func=cmd_reference_unlink)

    rrm = rsub.add_parser("remove", help="delete a reference and unname it everywhere")
    rrm.add_argument("slug")
    rrm.set_defaults(func=cmd_reference_remove)

    reflect = sub.add_parser("reflect", help="improve the instructions from a session transcript")
    reflect.add_argument("transcript")
    reflect.add_argument("--agent", choices=sorted(agent.AGENTS))
    reflect.add_argument("--model")
    reflect.set_defaults(func=cmd_reflect)

    sub.add_parser("self-check", help="run every module's checks").set_defaults(
        func=cmd_self_check)
    return p


def _add_agent_args(parser):
    parser.add_argument("--agent", choices=sorted(agent.AGENTS))
    parser.add_argument("--model", help="model for the agent, e.g. "
                                        "opencode/ling-3.1-flash-free")
    parser.add_argument("--no-reflect", action="store_true",
                        help="skip reading the session back afterwards")


def _add_generate_args(parser):
    parser.add_argument("preset", nargs="?")
    parser.add_argument("--tracks", type=int, default=2,
                        help="tracks per version to have on hand (default 2)")
    parser.add_argument("--duration", type=int, default=300,
                        help="seconds per generated track (default 300, max 600)")
    parser.add_argument("--minutes", type=int, default=60, help="length of the mix")
    parser.add_argument("--variant", help="only generate this version")
    parser.add_argument("--tracks-only", action="store_true", help="skip building a mix")
    parser.add_argument("--seed", type=int)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except store.NeteError as e:
        print(f"nete: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    return 0
