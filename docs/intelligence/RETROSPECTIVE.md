# Intelligence retrospective

Written 2026-08-06, after the section was completed and frozen.

Scale: 2 feature documents, 1 gap (pre-existing), 2 decisions.

---

## A section that owns no data

Intelligence reads every other domain and owns none of it. That made the
document boundary unusually clean — the machines here are *alerting* and
*narrative*, and neither is defined by a table.

It also made the **Data source** section the most valuable part of both
documents, for a reason the ordering principle predicted: a reader who does not
know where a briefing's numbers come from cannot judge whether to trust its
prose.

---

## The decision that mattered

`INT-D-001` — the model writes prose and never computes a figure — is the one
that would be easiest to erode and most expensive to lose. The reasoning is
recorded in the form that resists erosion: **a wrong figure computed in code is
reproducible and testable; a wrong figure in generated prose is neither, and it
is stated confidently.**

The alternative that had to be argued down was not "let the AI do everything"
but the plausible middle — *let it compute, then validate afterwards* — which is
rejected on the grounds that the validation **is** the calculation, so having
done it there is no reason to have asked twice.

---

## Nothing promoted

One candidate: *a section that reads everything should own nothing*. It is
already stated twice — in `command-center.md` as "a reader, not a source", and
here — but both are within the same architectural idea and one of them is a page
rather than a section. Two instances of the same sentence are not two sections
of evidence.

---

## For the last section

Settings is next and last. It carries `ARCH-004` and `SET-001`. Per
`ARCH-004`'s own recommendation, the credentials UI moves to Settings **when
Settings is next worked** — which is now. That entry should be read before
anything is proposed, and its `services.py` guidance in particular: the shared
API client is correctly shared and only its *naming* misleads.
