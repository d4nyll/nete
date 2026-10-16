# nete

Background music for working, generated and mixed on your own machine. Nothing
leaves the laptop.

You describe what you want by listening, not by writing a prompt. A coding agent
plays you short clips, you say which is better, and after a few rounds the
wording that produced the one you liked is saved as a preset. From then on `nete
generate` makes hours of it and `nete play` plays it.

## Install

Needs macOS on Apple Silicon, plus `git` and `uv`.

```sh
uv tool install -e .
nete init
```

`uv tool install` gives nete its own environment and puts the `nete` command in
`~/.local/bin`. If your shell cannot find it afterwards, run `uv tool
update-shell` once and open a new terminal.

`-e` means editable: nete runs from this directory, so editing the source
changes what the command does without reinstalling. Drop the `-e` if you would
rather have a fixed copy.

To update or remove it later:

```sh
uv tool upgrade nete
uv tool uninstall nete
```

`nete init` clones the model server, builds its Python environment and starts
it. The first run downloads about 20 GB of model weights and takes a while. If
you already have ACE-Step somewhere, point at it instead:

```sh
nete init --ace-step ~/src/ACE-Step-1.5
```

## Make a preset

```sh
nete preset create              # nete picks a placeholder name
nete preset create solarpunk    # or name it yourself
```

Left unnamed, nete calls it something like `salty-crackers` while you work and
asks what to call it once the agent is done, so naming happens when you know
what the thing sounds like.

This hands the session to a coding agent with instructions describing the
method: what kind of wording works on this model, what kind does not, how to run
a listening round, and how to score one. The agent asks you questions, generates
clips, and writes the preset when you are happy.

```sh
nete preset create --agent opencode
nete preset create --agent opencode --model opencode/ling-3.1-flash-free
```

Claude Code and OpenCode both work. OpenCode's own free models run without
logging in.

When you quit the agent, nete reads the session back on its own and proposes
edits to those instructions, so the next preset takes fewer rounds than this one.
It knows which transcript to read because it chose the session id before
launching the agent. Skip that step with `--no-reflect`, or run it later with
`nete reflect <transcript>`.

## References

Music you want a preset to sound like. Paste a YouTube link or point at a file.

```sh
nete reference add "https://www.youtube.com/watch?v=..." --preset cyberpunk
nete reference add ~/Music/track.flac another.mp3 --preset cyberpunk
nete reference list --preset cyberpunk
```

nete downloads the audio with yt-dlp, keeps the whole track as FLAC in
`~/.nete/references`, and measures all of it however long it is. An hour takes
about a minute, and it prints how far it has got as it goes.

References are stored once and shared. A preset names the ones it uses, so two
presets can aim at the same track without a second copy, and deleting a preset
never deletes the music it was aiming at.

```sh
nete reference link nutawesk28g cyberpunk     # use one that is already stored
nete reference unlink nutawesk28g cyberpunk   # stop using it; the audio stays
nete reference remove nutawesk28g             # delete it, and unname it everywhere
nete reference measure                        # fill in any missing numbers
```

FLAC is lossless, so a ninety-minute reference is about 950 MB. Keep the ones
you are aiming at and `nete reference remove` the rest.

YouTube sometimes answers a download with a 403 for no lasting reason, so nete
retries up to three times, asking for a different audio format each time.

```
    seconds                285.6
    tempo_bpm              100
    beat_strength          0.807
    percussive_pct         31.7
    brightness_hz          1584
    under_120hz_pct        27.1
```

A preset can have as many references as you like, and `nete reference list`
prints their average as well. The agent building the preset aims at those
numbers rather than guessing what a genre name means.

Downloading from YouTube is against their terms of service. nete keeps the audio
and the numbers on your machine and sends nothing anywhere.

## Make music and play it

```sh
nete generate                 # tracks and a mix for the default preset
nete generate cyberpunk --minutes 90
nete play                     # a small screen with rating keys
```

The player shows five lines: what played before, what is playing, what is next,
how far through you are, and the keys.

```
 ▶ NOW     pulse         (4:52)  cyberpunk-pulse-1552719052.flac  [4 good]
 playing 13:36 / 40:12  [########................]  played 0x  mix -  1 queued
 space pause   rate what is playing: 1 bad  2 poor  3 OK  4 good  5 excellent
```

The number keys rate whatever is playing at the moment it bothers you. When the
mix ends the screen holds, and the same keys rate the whole thing.

Ratings decide what plays next time: a mix you have never heard comes first,
then higher rated ones, then whatever you have played least. An unrated mix sits
where a 3 would.

## Commands

| | |
|---|---|
| `nete init` | install the model server, set up `~/.nete` |
| `nete status` | what is installed, running and set |
| `nete preset create [name]` | build a preset by listening |
| `nete preset edit <name>` | change one the same way |
| `nete preset list` | presets and their prompts |
| `nete preset show <name>` | one preset as JSON |
| `nete preset rename <old> <new>` | rename it and move its files |
| `nete preset default <name>` | use it when none is named |
| `nete preset delete <name>` | remove it, its tracks and its mixes |
| `nete generate [preset]` | generate tracks and build a mix |
| `nete play [preset]` | play mixes, rate as you go |
| `nete preset mix list` | mixes with length, plays and ratings |
| `nete preset mix delete <id>` | delete one mix, or `all` |
| `nete reference add <url or file>` | keep and measure a track you want to sound like |
| `nete reference list` | references and what they measured |
| `nete reference link <slug> <preset>` | have a preset use one already stored |
| `nete reference unlink <slug> <preset>` | stop using it; the audio stays |
| `nete reference remove <slug>` | delete it, and unname it everywhere |
| `nete reference measure` | fill in any missing numbers |
| `nete analyse <file or url>` | measure something without keeping it |
| `nete reflect <transcript>` | improve the instructions from a past session |
| `nete clip`, `nete blind`, `nete rounds` | listening rounds, used by the agent |
| `nete self-check` | run every module's checks |

## Where things live

Everything belonging to one preset lives in one directory.

```
~/.nete/
  config.json                 default preset, where the server is
  ace-step/                   the model server
  references/<slug>.flac      music a preset should sound like
  references/<slug>.json      where it came from and what it measured
  scratch/rounds/<name>/      clips from before a preset has a name
  presets/
    cyberpunk/
      preset.json             the preset, naming which references it uses
      tracks/<version>/*.flac generated audio, the ingredients
      mixes/<id>.flac         a finished mix
      mixes/<id>.json         its cue sheet, plays and ratings
      rounds/<name>/*.flac    clips from its listening rounds
      transcripts/*.jsonl     what the agent and the listener said
```

So deleting, renaming, backing up or sending someone a preset is one move, and a
rename cannot half-fail across several trees.

References sit outside that because they are large, they outlive the preset that
prompted them, and two presets can want the same track.

Tracks are the ingredients and mixes are the meals. Deleting a mix leaves the
tracks alone, so rebuilding takes a minute rather than an hour.

## What a preset is

```json
{
  "name": "cyberpunk",
  "prompt": "dark ambient atmospheric electronic, ominous heavy bass, repeating kick drum pattern",
  "bpm": [100, 100],
  "keys": ["A minor"],
  "model": "acestep-v15-base",
  "steps": 32,
  "guidance": 7.0,
  "slow": 50,
  "bass": -18,
  "min_seconds": 20,
  "crossfade": 8,
  "variants": {
    "calm":  {"prompt": "dark ambient atmospheric electronic, ominous heavy bass", "seconds": 300},
    "pulse": {"prompt": "... repeating kick drum pattern", "seconds": 240}
  },
  "pattern": ["calm", "pulse", "calm", "pulse"],
  "references": ["nutawesk28g"],
  "decisions": [
    {"term": "atmospheric", "verdict": "in",  "round": 9,
     "score": "60-53 unanimous", "method": "6 blind pairs, matched seeds"},
    {"term": "muffled",     "verdict": "out", "round": 11,
     "score": "60-59 majority draw", "method": "6 blind pairs, matched seeds"}
  ]
}
```

`decisions` records why each word is in the prompt, or why it is not. It is what
makes `nete preset edit` safe: the editing agent is handed the list and told not
to retest or quietly undo anything on it, so a preset does not drift back toward
wordings that already lost. `verdict` is `in`, `out` or `untested`.

`variants` are named versions of the preset. Each carries a `seconds` number
saying how long it may play before it stops being background, and that number
does two jobs: it sets how long each segment of a mix runs, and it sets how
bright the segment sounds. The version that can play longest keeps the least
bright content, because it is the one that has to stay out of your way.

For a preset that is calm the whole way through, give every version the same
`seconds`. Then nothing plays longer than anything else, they all sound equally
bright, and the names only mean "a different flavour".

## Three things done after generation, not asked for in the prompt

The model responds to words naming **what a thing is**: a genre, an instrument,
a sound source. It ignores words describing **how something should sound**.
`muffled`, `slow`, `subdued`, `tape saturation`, `bitcrushed` and `low-pass
filtered` were each put through blind listening tests and none of them changed
anything a listener could hear.

| job | how | setting |
|---|---|---|
| speed | replay the samples at a lower rate, like slowing a tape | `slow` |
| brightness | remove the fastest wiggles in the sound | per version, from its `seconds` |
| weight | make everything below 120 wiggles per second quieter | `bass` |

Slowing also drops the pitch by the same amount, which is where the extra depth
comes from, and it doubles how much listening one generation yields: the model
will not generate more than 600 seconds at a time, so at half speed that is 20
minutes.

## Two rules that came out of listening

**Every segment of a mix plays at the speed it was generated at.** An earlier
version sped some segments up and slowed others down to vary the energy, which
moved them into different musical keys, and back to back they sounded like
someone walking on a keyboard. Energy comes from which version plays.

**Nothing runs for less than `min_seconds`.** Versions carry a seconds budget
set by ear on isolated clips, but inside a mix anything under about 20 seconds
arrives and leaves too fast to be anything but distracting.

## Licence

nete is free software under the **GNU Affero General Public License, version 3
or later**. The full text is in [LICENSE](LICENSE).

In plain terms: use it, change it, share it. If you give someone a changed
version, or run one as a service other people use over a network, they are
entitled to your source under the same terms. The licence also grants you a
patent licence from every contributor, and that grant ends for anyone who sues
over patents in this software.

### What the rest of the stack is under

nete's own code has no Python dependencies. It runs these as separate programs:

| | licence |
|---|---|
| ACE-Step, and the `acestep-v15-base` weights | MIT, commercial use allowed |
| yt-dlp | Unlicense |
| ffmpeg | GPL-3.0-or-later as built by Homebrew |
| afconvert, afplay, afinfo | part of macOS |

### One restriction worth knowing before you sell anything

`nete describe` downloads **`nvidia/audio-flamingo-next-captioner-hf`**, which
is under the NVIDIA OneWay Noncommercial License: non-commercial research only.
Parts of its training data also fall under the Qwen Research License and
OpenAI's terms.

That model is not distributed with nete and is fetched on demand, so it does not
affect nete's own licence. It does mean **using `nete describe` commercially
would breach NVIDIA's terms**. Every other part of the stack, including the
music the generator produces, is clear for commercial use.
