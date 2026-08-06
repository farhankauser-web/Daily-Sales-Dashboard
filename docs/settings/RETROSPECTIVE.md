# Settings retrospective

Written 2026-08-06, after the section was completed and frozen. The last of nine.

Scale: 3 feature documents, 0 new gaps, 1 decision.

---

## The section everything depends on and nothing owns

Settings holds no business outcome. It holds the preconditions — credentials,
access, product identity — and its documents are short because each machine is
small.

What made it worth documenting properly is the **distance between cause and
symptom**. A rejected credential does not present as a credential problem; it
presents as a section that stopped growing. `credentials.md` says so explicitly
and points at the Walmart error-log gap as the shape that failure takes,
because that section is where thousands of rows from one expired key were found.

---

## `ARCH-004` said when, and the when was now

The mismatch register recommended moving the credentials UI out of
`apps/amazon_api` **when Settings is next worked**. That instruction was written
months before this section was documented and was still exactly right.

It also said what *not* to move: `services.py` stays, because a shared API
client is correctly shared and only the app's name misleads. A section that
arrived wanting to tidy up would have moved it.

**A mismatch entry that records when to act is worth more than one that records
only what is wrong.** This is the second time an `ARCH-` entry has done the
work — `ARCH-003` named its canonical implementation and saved Brand Analytics
an analysis.

---

## Nothing promoted, and that is the right ending

Nine sections in, the last three produced no new methodology at all. Everything
they needed was already written: ground truth, canonical-first, classification
before recommendation, the production verification queue, one machine per
document, and the promotion bar that kept all of it short.

The bar did its job most visibly here. Three candidates were considered across
the last three sections and all three were rejected — one for having a single
instance, one for restating an existing rule in different words, and one because
two occurrences of the same sentence are not two sections of evidence.

**A methodology that stops growing is not stalling.** It is what it looks like
when the method fits the work.
