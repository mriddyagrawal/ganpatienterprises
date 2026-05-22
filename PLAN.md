# Ganpati Enterprises — Implementation Plan

This is the source-of-truth document for how the web app gets built. Business context lives in [`BUSINESS_DESCRIPTION.md`](BUSINESS_DESCRIPTION.md); deferred ideas live in [`futureplans.md`](futureplans.md).

---

## 1. Locked-in decisions

| Decision | Choice |
|---|---|
| **Tech stack** | Django + HTMX + Tailwind CSS. **Database: SQLite for V1 local dev** (zero install), switching to **PostgreSQL** when public hosting is set up. The Django ORM hides the difference; the only change is a `DATABASE_URL` env var. |
| **Hosting** | Local development only in V1. Code lives on GitHub; the running app lives on the owner's computer at `http://localhost`. Real hosting (Railway / Render / VPS) is deferred — see `futureplans.md`. |
| **Auth** | Django session auth, username + password. OTP login is in `futureplans.md`. |
| **Ledger model** | Pure two-direction: every sale is **Udhar** (debit on retailer), every payment is **Jama** (credit). The running balance is **Baaki**. |
| **No memo handling** in V1 — salesman just opens app and logs to any retailer |
| **`Visit` entity, auto-grouped** — every Sale and Payment is attached to a Visit. Visits are created automatically using a 15-minute activity window (see §3). The salesman never manually "starts" or "ends" a visit. |
| **No recharge SKUs.** The business sells rupee-value recharge credits, not specific Jio plans. The data model tracks amounts only. |
| **Online-only** in V1 (offline-first is in `futureplans.md`) |
| **Per-entry notes** field on both `Sale` and `Payment` |
| **UI language** | Hinglish — Hindi vocabulary written in Latin script: *Udhar*, *Jama*, *Baaki*, *Naya Entry* |
| **Visual language** | Red = Udhar / "dena hai" / outgoing. Green = Jama / "mil gaya" / incoming. |
| **Code vs UI naming** | English in code (`Sale`, `Payment`, `balance`), Hindi-in-Latin in user-facing strings (*Udhar*, *Jama*, *Baaki*). |

---

## 2. Stack details

- **Python** 3.14 (managed by `uv`; `.python-version` is committed)
- **Django** 6.0.x (uv resolved the latest available stable when Phase 0 was bootstrapped; 6.0 is GA and supports Python 3.12–3.14 cleanly. Older `5.x` from earlier plan revisions has been superseded.)
- **Database:** SQLite for V1 local dev. When public hosting goes up (see `futureplans.md` #3), swap to managed **PostgreSQL 16** via `DATABASE_URL`.
- **Tailwind CSS** via `django-tailwind` (standalone v4 template — no Node dependency)
- **HTMX** for partial-page updates (form submissions without full reloads, live Baaki updates, retailer autocomplete)
- **django-unfold** for a modern Django Admin theme — optional, but cheap upgrade
- **Lucide icons** (MIT-licensed, large library, mobile-friendly)
- **WhiteNoise** for static file serving in prod
- **gunicorn** as the WSGI server

No JavaScript framework, no separate frontend codebase. One Django project, one deployment.

---

## 3. Data model

### Entities

#### `User` (extending Django's `AbstractUser`)
- `username` (login)
- `password` (hashed by Django)
- `full_name`
- `phone`
- `role` — enum: `admin` | `salesman`
- `is_active` (Django default)
- `created_at`

#### `Retailer`
- `id`
- `name` — shop name (e.g., "Mobile Shoppy")
- `owner_name` — proprietor's name (optional)
- `phone`
- `area` — short text (e.g., "Bhagat Singh Market")
- `address` — long text (optional)
- `notes` — free text
- `is_active` — boolean (soft-archive)
- `created_at`, `updated_at`

#### `Visit` *(auto-grouped session at a retailer by a salesman)*
- `id`
- `salesman` (FK → User)
- `retailer` (FK → Retailer)
- `started_at` — timestamp of the first Sale/Payment that opened this visit
- `last_activity_at` — timestamp of the most recent Sale/Payment attached
- `notes` — optional, free text (visit-level remarks, separate from per-entry notes)
- `created_at`, `updated_at`

A visit is never created directly by a user. It is created or extended by the `attach_to_visit` helper described in **§3.5** below, every time a Sale or Payment is saved.

#### `Sale` *(an Udhar entry — recharge given to retailer)*
- `id`
- `visit` (FK → Visit) — assigned automatically; see §3.5
- `retailer` (FK → Retailer) *(denormalized for queries; matches `visit.retailer`)*
- `salesman` (FK → User) *(denormalized; matches `visit.salesman`)*
- `amount` (positive decimal, max 9 digits incl 2 decimal)
- `occurred_at` (defaults to now; admin can backdate)
- `notes` (free text, optional)
- `is_deleted` (soft delete)
- `deleted_reason` (free text, required if `is_deleted=True`)
- `created_at`, `updated_at`

#### `Payment` *(a Jama entry — money received from retailer)*
- `id`
- `visit` (FK → Visit) — assigned automatically; see §3.5
- `retailer` (FK → Retailer) *(denormalized; matches `visit.retailer`)*
- `salesman` (FK → User) *(denormalized; matches `visit.salesman`)*
- `amount` (positive decimal)
- `mode` — enum: `cash` | `upi`
- `occurred_at`
- `notes`
- `is_deleted`, `deleted_reason`
- `created_at`, `updated_at`

#### `AuditLog`
- `id`
- `actor` (FK → User)
- `entity_type` (e.g., "Sale")
- `entity_id`
- `action` — `create` | `update` | `delete`
- `before` (JSON snapshot, nullable)
- `after` (JSON snapshot, nullable)
- `at` (timestamp)

Every create/update/delete on `Sale` and `Payment` writes an AuditLog row. Non-negotiable for a money-handling system.

### 3.5 Visit auto-grouping rule

The Visit entity is invisible to the salesman — he never starts or ends one. Every time a Sale or Payment is saved, the backend runs this helper:

```
attach_to_visit(salesman, retailer, occurred_at):
    1. Look for the most recent Visit with the same salesman, same retailer,
       and `last_activity_at >= occurred_at - 15 minutes`.
    2. If found:
         - Set the new entry's `visit` to that Visit.
         - If `occurred_at > visit.last_activity_at`, bump `visit.last_activity_at = occurred_at`.
    3. Else:
         - Create a new Visit with `started_at = last_activity_at = occurred_at`.
         - Set the new entry's `visit` to that new Visit.
```

**Effects of this rule:**
- A salesman who logs Udhar at 11:00 and Jama at 11:08 at the same shop → both attach to one Visit.
- The same salesman logging Udhar at 11:00 and then again at 11:30 at the same shop → two separate Visits.
- The salesman moving between shops → each shop gets its own Visit, naturally.
- The salesman never has to think about visits. They just emerge from his activity, and become a clean denominator for "kitne dukaan visit kiye aaj" in reports.

Re-assigning an entry to a different visit (e.g., admin corrects a misattribution) is allowed only via Django Admin / shell — not a salesman action.

### Computed values

- **Retailer.baaki** = `Σ Sale.amount (not deleted) − Σ Payment.amount (not deleted)` for that retailer.
  - Positive → retailer owes the business
  - Zero → settled
  - Negative → business owes retailer (overpayment scenario)
- Implemented as a method/annotation on the Retailer queryset, not a stored field. Recomputed on read. At our scale this is fine.

### Validation rules

- `Sale.amount > 0` and `Payment.amount > 0`
- `Payment.mode` ∈ {`cash`, `upi`}
- Soft delete only from the UI; hard delete only via Django shell / admin if absolutely needed
- Salesmen can edit/delete their **own** entries within **24 hours** of creation; after that, only admin can modify
- Admin can edit/delete any entry at any time
- Every delete requires a `deleted_reason`
- Optional sanity cap: warn (don't block) if a single Sale or Payment exceeds ₹1,00,000 (₹1 lakh) — likely a typo

---

## 4. UI/UX principles

These apply across every screen the salesman sees. The owner-facing admin can be denser and more text-heavy because the owner is the literate, technical user.

### Vocabulary (salesman-facing strings)
| English concept | UI text |
|---|---|
| Sale on credit | **Udhar** |
| Payment received | **Jama** |
| Outstanding balance | **Baaki** |
| New entry | **Naya Entry** |
| Today | **Aaj** |
| Cash | **Cash** *(everyone uses the English word)* |
| UPI | **UPI** |
| Retailer / shop | **Dukaan** |
| Save | **Save** *(universally understood)* |
| Edit | **Edit** |
| Delete | **Delete** |

### Color & icon language
- **Red** `#EF4444` — Udhar, outgoing, "dena hai" (owed)
- **Green** `#10B981` — Jama, incoming, "mil gaya" (received)
- **Grey** `#6B7280` — chrome, deleted entries
- Lucide icon `arrow-up-right` (red) for Udhar; `arrow-down-left` (green) for Jama
- Always **icon + word together** on action buttons — never icon-alone, never word-alone

### Typography
- **Inter** or **Plus Jakarta Sans** (system font fallback)
- Amounts shown in `text-4xl font-bold` with prominent ₹ symbol — these are the most important pixels on every screen
- Body text `text-base`; button labels `text-lg`

### Layout
- Mobile-first: design at 360px width, expand for larger screens
- **Bottom tab bar** for salesman with three tabs:
  1. **Dukaan** (retailers list)
  2. **Naya Entry** (the central red "+" button — visually the largest tab)
  3. **Aaj** (today's summary)
- Flat navigation, never more than 2 taps deep
- Big touch targets: every button ≥ 56px tall, ≥ 16px of vertical padding around inputs

### Feedback
- Save → immediate green flash on the saved row + updated Baaki in header
- Validation errors → inline below the field, in red, plain language ("Amount zero nahi ho sakta")
- Confirm dialogs use the same Hindi-first style

### What we are deliberately not doing
- No Hindi (Devanagari) script — research shows Hindi keyboard input is painful even for literate users. Latin script Hinglish is the sweet spot.
- No voice input in V1 (future possibility)
- No nested settings menus
- No long forms with multiple sections
- No tooltips that require reading

---

## 5. Phases

Time estimates are rough — they assume one developer working at a steady pace.

### Phase 0 — Foundation *(~½ day)*

**Outcome:** A Django project running locally on the owner's computer with Tailwind, HTMX, and SQLite wired up. Nothing user-visible yet beyond a styled placeholder page.

Tasks:
1. `uv init` and pin Python 3.12; add Django + deps via `uv add`
2. `django-admin startproject ganpati` + initial app structure (`accounts`, `core`)
3. SQLite as the dev database (Django default, no install)
4. Install Tailwind (via django-tailwind), HTMX, django-unfold
5. Create base templates: `base.html` (root layout, mobile chrome, viewport meta), `partials/_nav.html`
6. Set up `settings.py` with proper dev split; use env vars for secrets via `django-environ`
7. Add `.gitignore` (uv produces `pyproject.toml` + `uv.lock`)
8. Push initial commit to the GitHub repo
9. Confirm `python manage.py runserver` works locally and the placeholder page renders in the browser

**Deliverable:** A working local dev environment. Owner can run the app on his computer and see a styled placeholder. No public hosting in V1.

---

### Phase 1 — Data model + Django Admin *(~1–2 days)*

**Outcome:** All entities exist, are protected by validation, and can be managed through Django Admin.

Tasks:
1. Implement `User` model (custom, with `role` field)
2. Implement `Retailer`, `Sale`, `Payment`, `AuditLog` models
3. Write the `Retailer.baaki` queryset annotation
4. Add validation (clean methods, model validators, DB constraints where appropriate)
5. Implement audit-log signal handlers on `Sale` and `Payment`
6. Register all models in Django Admin with sensible list/filter/search configuration; theme with Unfold
7. Build login page (Django's built-in auth views, restyled with Tailwind)
8. Build role-based redirect: admin → `/admin/`, salesman → `/` (their dashboard, stub for now)
9. Seed a few test retailers and a test salesman account
10. Confirm: owner can log into admin, add retailers, view all sales (none yet), view all payments (none yet)

**Deliverable:** Owner can log in, manage retailers and salesman accounts entirely through Django Admin.

---

### Phase 2 — Salesman field app *(~3–5 days)*

**Outcome:** A salesman can log in on his phone, browse retailers, see each retailer's Baaki and history, and log Udhar / Jama entries.

#### Screens

**S1. Login** — username + password, "remember me" 30 days.

**S2. Dukaan tab (retailers list)**
- Top: search bar (live filter, HTMX)
- Sort toggle: **Baaki (sabse zyada)** [default] | **Naam (A → Z)** | **Recent activity**
- Each row: shop name, area (small text), Baaki on the right in red (if owed) or green (if zero/credit)
- Tap row → S3

**S3. Retailer detail (the ledger)**
- Top card: shop name, owner name, phone, current **Baaki** (huge ₹ figure, red/green)
- "Naya Entry" red+green pair of buttons (or single FAB → S4)
- Below: vertical timeline of entries
  - Udhar entries: red `↗️ Udhar ₹500` with date + notes + small "edit" pencil if within 24h
  - Jama entries: green `↘️ Jama ₹300 (Cash)` with date
  - Deleted entries shown struck-through in grey
- Infinite scroll or "Load more"

**S4. Naya Entry flow**
- Step 1 (skip if entered from retailer context): pick retailer (searchable list)
- Step 2: two huge buttons
  - 🔴 **Udhar** (recharge diya)
  - 🟢 **Jama** (paisa mila)
- Step 3: amount entry — large numeric keypad-friendly input, ₹ symbol prefix
- Step 4 (Jama only): mode — two buttons
  - **Cash**
  - **UPI**
- Step 5: notes (optional, single-line, "Notes...")
- Step 6: confirm screen showing "Aap **₹500 Udhar** add kar rahe ho Mobile Shoppy ke liye. New Baaki: **₹12,900**" with **Save** button
- After save: success animation, return to retailer detail, see the new row at the top

**S5. Aaj tab (today's report — salesman view)**

This is the salesman's daily report. It scopes activity numbers to his own entries; the "Total Baaki" section is system-wide so he sees every retailer's current standing.

Sections, top to bottom:

1. **Header** — date, salesman name.
2. **Aaj ka Udhar** — total ₹ of his Sales today, with sub-text "N entries · M dukaan."
3. **Aaj ka Jama** — total ₹ of his Payments today; below, a list:
   - 💵 Cash: ₹X
   - 📱 UPI: ₹Y
4. **Aaj ka Udhar — Detail** — list of today's Sale entries (retailer name + amount), edit/delete pencil if within 24h. Tap "Sab dekho" → full list.
5. **Total Baaki — Top dukaan** — system-wide list of retailers with Baaki > 0, sorted descending. Top 10 inline + summary stat ("System total: ₹X across N dukaan") + tap to expand.
6. **Aaj ke Visits** — small stat: "Aaj N dukaan visit kiye." (Computed from distinct Visits with today's activity.)

Edit/delete on today's entries follows the 24h window rule.

#### Other tasks
- Salesman role guard on all views (decorator / middleware)
- Edit form for an entry (reuses the new-entry flow)
- Delete confirms with required reason

**Deliverable:** Salesman uses the app on his phone for one real workday. Owner sees the data flow into Django Admin in real time.

---

### Phase 3 — Admin dashboard *(~2–3 days)*

**Outcome:** Owner has a real dashboard, separate from Django Admin, that's actually nice to look at.

#### Screens

**A1. Today's Report — admin view**

Same sections as the salesman's S5 (Aaj tab), unscoped and with filters. Specifically:

- **Date picker** at the top (default = today; admin can view any past date).
- **Salesman filter** (default = "All salesmen"; can pick one).
- **Aaj ka Udhar / Jama / Cash / UPI** totals, scoped to the date + salesman filter.
- **Per-salesman cards** (shown when filter = "All salesmen"): each salesman's Udhar issued, Jama collected, Cash/UPI split, # entries, # visits.
- **Aaj ka Udhar — Detail** list (filtered).
- **Total Baaki — Top dukaan** list (system-wide, not date-filtered — Baaki is a live number).
- **Live transaction feed** at the bottom: last 50 entries across all salesmen, auto-refresh via HTMX every 60 seconds.

**A2. Retailers** — searchable, sortable
- Columns: name, area, current Baaki, last entry date, days-since-activity
- Click → A3

**A3. Retailer detail**
- Same ledger view as salesman's S3, but with full edit/delete powers and no 24h limit
- "Add manual entry" (admin can record a payment that came in via bank deposit, etc.)
- Edit retailer profile

**A4. Salesmen** — list of salesmen, per-salesman stats (week / month), drill-down to per-salesman timeline

#### Other tasks
- Admin role guard
- Navigation: top nav (admin is desktop-first but still mobile-friendly)
- HTMX-powered auto-refresh on Today screen

**Deliverable:** Owner can ditch Django Admin for daily ops and use the custom dashboard.

---

### Phase 4 — Reports & exports *(~2–3 days)*

**Outcome:** Owner can produce reports for his accountant and for himself.

Reports:
1. **Daily closing** — for any date: Σ Udhar, Σ Jama by mode, per-salesman breakdown, list of all entries
2. **Baaki aging** — outstanding Baaki per retailer, bucketed by oldest unpaid entry age (0–7 / 8–15 / 16–30 / 31–60 / 60+ days). The flagship report.
3. **Salesman performance** — date range filter; per salesman: # entries, Udhar issued, Jama collected, by mode
4. **Retailer statement** — for one retailer, a printable PDF (use `weasyprint` or `xhtml2pdf`) showing every entry over a date range with running Baaki
5. **CSV / Excel export** for every report (using `openpyxl` for Excel)

Optional but high-value:
- **Daily summary email** sent to owner at a configurable time each evening with headline numbers

**Deliverable:** Owner can hand any of these reports to his accountant in two clicks.

---

### Phase 5 — Polish & nice-to-haves *(ongoing)*

Picked up only after Phases 0–4 are stable and being used daily.

- 2FA for admin login
- Audit log viewer UI (currently visible only via Django Admin)
- Bulk retailer import from Excel
- "Send reminder to retailer" — WhatsApp deep link with prefilled Baaki message
- PWA shell (home screen icon, splash screen)
- Voice input prototype for amounts
- Photo attachment per entry (proof of payment, etc.)

Anything that becomes a real need gets graduated up to its own mini-phase; otherwise stays here.

---

## 6. Open questions (track for later — none block Phase 0)

All previously-open questions have been resolved as of the latest planning round:

- **Recharge SKUs:** never. The business sells rupee-value recharge credits, not specific Jio plans. Locked in as a design decision (see §1).
- **Failed recharges:** deferred — captured in `futureplans.md`.
- **Commission / margin tracking:** deferred — captured in `futureplans.md`.
- **Other business lines (Airtel, VI, FMCG):** deferred — captured in `futureplans.md`.
- **Messaging (OTP login, retailer SMS on transactions, daily email):** deferred — captured in `futureplans.md`.
- **Salesman edit/delete window:** 24 hours, confirmed.

No questions block Phase 0. New questions, when they arise, will be added here.

---

## 7. Definitions / Glossary

| Term | Meaning |
|---|---|
| **Udhar** | A sale — recharge handed to retailer on credit. Increases retailer's Baaki. |
| **Jama** | A payment received from retailer. Decreases retailer's Baaki. |
| **Baaki** | Retailer's current outstanding balance = Σ Udhar − Σ Jama. |
| **Dukaan** | A retailer / shop in the system. |
| **Aaj** | Today's view for the salesman. |
| **Naya Entry** | A new Udhar or Jama entry. |

---

## 8. Workflow for changes to this plan

- Any change to the locked-in decisions section requires the owner's explicit confirmation.
- Open questions get answered inline as the relevant phase begins.
- New deferred ideas go into [`futureplans.md`](futureplans.md), not into this plan.
