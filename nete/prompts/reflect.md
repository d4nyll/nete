# Improving the preset-building instructions

A listening session just finished. Read the transcript at the path you were
given, then read the instructions that drove it at `nete/prompts/build_preset.md`.

Your job is to make the next session go better for the person who sat through
this one. Propose edits to the instructions. Do not rewrite the preset.

Look for these, in order of how much they cost the listener:

**Rounds that told them nothing.** A round where every clip sounded the same is
wasted attention. Could it have been screened without them, with `nete analyse`?
Was the control too quiet for the thing being tested to show up? Was the
wording in a category already known not to work?

**Corrections they had to make.** Every time they said "that is not what I
meant", "this is still too long", or "you are testing the wrong thing", the
instructions were missing something they had to supply. Those are the highest
value edits, because they will be supplied again otherwise.

**Claims that turned out to be wrong.** If the session produced evidence that
contradicts a line in the instructions, change the line and say what the evidence
was. The instructions are a record of what has been tested, not a style guide.

**Questions asked more than once.** If the agent had to ask the same thing in two
sessions, it belongs in the preset schema or in a default, not in a conversation.

**Steps that ran in the wrong order.** Look for work that had to be redone
because something was settled too early or too late.

## What to write

For each proposed edit:

- the line or section you would change
- the replacement text, written out in full
- one sentence saying what happened in the transcript that prompted it

Rank them by how much listener attention they save. Leave out anything you
cannot point to a specific moment in the transcript for.

If a round went badly because of a bug in `nete` rather than the instructions,
say so separately and name the command and the behaviour. Do not try to paper
over a bug with wording.

Finish with a note on anything the session discovered about the model that is
worth recording: a wording category that worked, one that did not, a setting
that turned out to be ignored. Those go in the "what is known to work" section,
and they are the part that compounds.
