# Review protocol — the human/AI learning loop

The automatic weight adaptation (see METHODOLOGY.md) is deliberately narrow:
it can only re-balance the four existing factors. Everything else — new
metrics, bug discoveries, data-quality problems, structural changes — goes
through a written review, so that every change to the system is deliberate,
justified and on the record.

## Monthly review (first scan of each month, or thereabouts)

Work through this checklist and write the answers to
`journal/review-YYYY-MM.md`:

1. **Track record.** For every matured scan: did the top decile beat the scan
   median at 21/63/126 days? Is the cumulative spread positive? Is it
   *shrinking* over time (post-publication decay showing up live)?
2. **Factor ICs.** Which factors are pulling their weight? Any factor with a
   persistently negative IC across 6+ matured scans deserves investigation
   before the automatic floor keeps it limping along.
3. **Post-mortems.** Pick the 3 worst-performing past top-decile stocks.
   *Why* did they fall? Was the information knowably wrong at scan time
   (data bug — fixable), or unknowable (earnings shock, fraud — the price of
   doing business)? Data bugs become fixes; unknowables become notes.
4. **False negatives.** Pick 3 of the period's best performers that the
   screener ranked poorly. Which factor missed them? Is there a documented,
   evidence-backed metric that would have caught them?
5. **Data health.** Coverage percentages trending down? A source drifting?
6. **Proposed changes.** Each with: the evidence, the expected effect, and
   how we'll know if it worked. Changes ship as commits referencing the
   review entry — never silent edits.

## Rules

- **No change without a written reason.** The journal is the lab notebook;
  the experiment is worthless if the instrument changes silently.
- **One structural change at a time** where practical — otherwise outcomes
  can't be attributed.
- **Never delete history.** Bad calls stay in the database and the journal.
  The record of being wrong is the most valuable data this project produces.
