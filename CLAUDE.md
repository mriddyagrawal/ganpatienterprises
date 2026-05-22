# Working in this repo

This is the **Ganpati Enterprises** Django app. Source-of-truth documents:
- [PLAN.md](PLAN.md) — the implementation plan and locked-in decisions
- [BUSINESS_DESCRIPTION.md](BUSINESS_DESCRIPTION.md) — the business context
- [futureplans.md](futureplans.md) — deferred ideas, do not implement
- [comments.md](comments.md) — reviewer feedback (see below)

## Two-Claude workflow

A second Claude instance ("the reviewer") is watching every commit to this repo and appending observations to [comments.md](comments.md). When you commit substantive work, expect a new review section to land in `comments.md` within ~1 minute.

**Before starting any new phase of PLAN.md, do this:**

1. Open `comments.md` and read every review section pertaining to your last commit (and any older sections you haven't yet addressed).
2. For each item:
   - **Issue** — must be fixed before moving on. Address it, commit it, then wait ~1 minute for the follow-up review section to confirm. Do not start the next phase until your Issues have been confirmed clear.
   - **Watch** — judgment call. Fix if it's cheap and obviously right; otherwise note an explicit defer in your commit message ("watch item X deferred because …") so the reviewer can stop re-raising it.
   - **Question** — answer it in your next commit message or as a PLAN.md edit if it's a real decision point.
3. Reference the reviewer commit (or the SHA the review was *about*) in your fix-up commit message — e.g. *"Phase 1 follow-up: address reviewer feedback on `ebda7eb`"*.
4. After your fix-up lands, wait for the reviewer's confirmation section before proceeding.

## What the reviewer is looking at

- PLAN.md adherence (entity shapes, locked-in decisions, vocabulary)
- Money-handling correctness (positive amounts, soft-delete invariants, audit trails, scoping)
- The salesman-vs-admin scoping rule (PLAN §1) treated as a **permission boundary**, not just a UI filter
- Mobile-first Hinglish UI conventions in templates (red Udhar, green Jama, large touch targets)
- Test coverage on load-bearing logic before marking a phase done

## Conventions

- English in code, Hinglish in user-facing strings (PLAN §1).
- Tests for any money-handling logic before marking a phase done.
- Migrations: one-per-change; do not squash retroactively without owner approval.
- Commits should be phase-scoped or topic-scoped, with descriptive messages.
- Do **not** implement anything from `futureplans.md` unless PLAN.md is updated to pull it in.

## When the owner is unreachable

If you need a decision the owner hasn't made (e.g. a real new ambiguity, not something PLAN already addresses), pick the most reasonable interpretation, document the assumption in the commit message under a `## Assumptions` section, and continue. The reviewer will flag if your choice doesn't survive scrutiny.
