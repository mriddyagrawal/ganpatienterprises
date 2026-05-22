# Build review — Ganpati Enterprises

I am a second Claude instance watching the build. Another Claude is implementing the app per [PLAN.md](PLAN.md). I append observations here every time it commits or makes substantive changes. The goal: catch deviations from the plan, bugs, and UX/code-quality issues early.

Conventions:
- **Good** — done well, worth keeping.
- **Issue** — something that's wrong or off-plan; needs fixing.
- **Watch** — minor / stylistic; flag for now, revisit if it compounds.
- **Question** — ambiguity in PLAN.md or the implementation that the owner may want to weigh in on.

Each section is timestamped and pinned to the commit it reviews.

---

## Baseline review — commit `b535563` (Phase 0)

*Scaffold Django + Tailwind + HTMX project foundation.*

### Good
- Settings split into `base.py` + `dev.py` with a `settings/__init__.py` that dispatches on `DJANGO_ENV`. Matches PLAN §5 Phase 0 task 6 cleanly. Prod path is stubbed (`from .prod import *`) which is fine since `prod.py` is correctly deferred.
- `TIME_ZONE = "Asia/Kolkata"` — correct for India. Easy to get wrong; good catch.
- `django-unfold` installed and listed **before** `django.contrib.admin` in `INSTALLED_APPS`. That's the documented requirement; getting it wrong breaks Unfold silently. Good.
- `django_htmx.middleware.HtmxMiddleware` present in `MIDDLEWARE`. HTMX is wired both server-side (middleware) and client-side (CDN `<script>` in `base.html`).
- `theme-color` meta tag set to `#EF4444` (Udhar red). Small, but it follows PLAN §4 color language — when the salesman saves to home screen the chrome will match the visual identity.
- Mobile-first viewport: `viewport-fit=cover` is included, which is what you want for iOS notched phones.
- Inter font preconnect-loaded; matches PLAN §4 typography decision.
- `DATABASES` defined via `env.db_url(... default=sqlite:///...)`. Drop-in Postgres later via `DATABASE_URL` — exactly what PLAN §1 promises.
- `.env.example` is comprehensive, comments tell the user how to generate `DJANGO_SECRET_KEY`.
- `INSTALLED_APPS` already includes `accounts` and `core` (first-party) and `theme` (Tailwind app). No dangling app references.
- `.gitignore` covers `db.sqlite3`, `__pycache__`, `.env`, `staticfiles/`, `media/`. Reasonable.
- Commit message itself is descriptive and lists exactly what's in this phase. Future archaeology will be easy.

### Issue
- **Django version is 6.0.5, not 5.x.** PLAN §2 explicitly says *"Django 5.x"*. The commit message acknowledges this (`Django 6.0.5`) but the deviation from PLAN is not flagged anywhere and PLAN was not updated. Django 6 was just released; it's likely fine technically, but going off-plan on the very first commit without recording the decision is a process smell. Either pin to 5.x as PLAN says, or update PLAN §2 to "Django 6.x" and note why.
- **`theme/templates/base.html` is the unmodified django-tailwind boilerplate** (`Django + Tailwind = ❤️`, `font-serif`, no Ganpati branding). It was committed verbatim. It's harmless because `core/home.html` extends the *project-level* `templates/base.html`, not this one — but it's dead code on the very first commit. Should be deleted, or repurposed.
- **`SECRET_KEY` has a hardcoded fallback default** (`"dev-only-insecure-replace-in-prod"`) instead of failing loudly when missing in prod. Convenient for dev, dangerous in prod. Recommend: in `dev.py` set a dev-only fallback; in `base.py` require it via `env("DJANGO_SECRET_KEY")` (no default) so prod fails fast. Not urgent, but worth fixing before any prod work.

### Watch
- `pyproject.toml` shows Python 3.12 required (`requires-python = ">=3.12"`), but the commit message says "Python 3.14". The repo will accept 3.12+, but if the dev environment is locked to 3.14 specifically, there's no `.python-version` file (it's gitignored) so a fresh clone could land on a different minor version. Not a bug, just a consistency note.
- `theme-color` is hardcoded red on every page, including the Jama (green) flows. Standard practice; could conditionally swap for the "naya entry → Jama" screens later if it ever matters, but probably not worth it.
- `.gitignore` has a comment "*Tailwind build output — committed for now…*" but doesn't actually ignore anything related. The intent doesn't match the code; either remove the comment or commit the staleness of the rule.
- `cookiecutter` is in `dev` dependency-group. It's not used yet and seems out of scope for this project. Trim if it stays unused.

### Question (owner-facing)
- Did you intend Django 6.x? If yes, I'll quietly update PLAN §2; if no, the next Claude should downgrade to `django>=5.2,<6`.

---

*Monitoring on. Next review will be appended when the next commit lands or substantive uncommitted changes accumulate.*
