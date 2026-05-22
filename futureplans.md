# Future Plans & Possibilities

A running list of ideas, features, and integrations that are **out of scope for the current build** but worth coming back to once the core app is stable. Each entry should have enough detail to be picked up months later without context loss.

---

## 1. Daily Memo Digitization

**The idea.** Each morning, Jio (the telecom company) issues a paper memo to every salesman listing which retailers they are supposed to visit that day. Right now the web app deliberately ignores this — the salesman just opens the app and logs visits to whichever retailers he actually went to. The memo stays on paper, in the salesman's pocket.

**What "digitization" could look like.**
- **Manual entry by admin/owner.** The owner enters tomorrow's memo into the app each evening (or each morning) by selecting retailers for each salesman.
- **Bulk import.** If Jio provides the memo as an Excel/CSV/PDF, the app supports uploading the file and parsing it.
- **API integration.** If Jio ever exposes a distributor-facing API for daily memos, the app pulls them automatically.

**Value if built.**
- Salesman opens the app and immediately sees today's assigned route, sorted by area.
- Owner can compare *assigned* retailers vs *actually-visited* retailers — a soft accountability layer.
- Enables features like "skipped retailer" tracking and route-completion percentage on the admin dashboard.

**Why deferred.**
- Jio's memo format is not consistently structured today (sometimes excludes udhar shops, sometimes paper-only).
- The core value of the app — replacing paper bookkeeping — is delivered without memos.
- Adding memo handling now would complicate the salesman UI for marginal gain.

**Triggers to revisit.**
- Jio starts providing memos in a structured digital format.
- The owner wants a "who skipped whose shop" accountability view.
- The owner wants to assign extra (udhar-collection) visits to specific salesmen as part of an official route.

---

## 2. Offline-First / Forgiving Save

**The idea.** Today, V1 of the app requires an active internet connection — every "Save" hits the server immediately, and if the salesman has no signal he sees an error or a spinner. In a future iteration, the app should let the salesman log entries even when offline, queue them locally, and sync them to the server automatically once signal returns.

**What this could look like.**
- **Light version (recommended first):** "forgiving save" — when the salesman taps Save, the app shows an instant success on screen and queues the entry in browser `localStorage`. A background process retries the save every few seconds. The salesman never sees a failure unless the entry stays unsynced for very long (>5 min), at which point a banner warns him.
- **Full version:** a true offline-first Progressive Web App (PWA) with a service worker and IndexedDB. Entire app shell and the salesman's retailer list are cached locally. The app works in airplane mode and syncs on reconnect. This is what Khatabook and OkCredit do.

**Value if built.**
- Salesmen in markets with patchy 4G/5G or inside basement shops can log entries without friction.
- Removes a class of "I'll just write it in my notebook for now" excuses that defeat the whole point of the app.

**Why deferred.**
- The owner chose to ship online-only for V1 to keep the build simple and fast. The decision is to validate the core flows first, then revisit if offline turns out to be a real daily blocker.
- Offline-first introduces real complexity: conflict resolution, retry logic, stale data, sync UI, edge cases around editing-then-going-offline.

**Triggers to revisit.**
- Salesmen report (or admin observes via logs) frequent save failures from poor signal.
- Salesmen are still falling back to paper notebooks during field visits.
- Adoption is stalling and offline UX is named as the reason.

---

## 3. Public Hosting

**The idea.** V1 runs only on the owner's local computer (`http://localhost`). That's enough for development and testing, but salesmen in the field can't actually use the app until it's reachable from the public internet.

**Realistic options when the time comes.**
- **Managed PaaS** — Railway, Render, or Fly.io. One-click deploys, managed Postgres, automatic HTTPS. Cost: roughly $5–15 / month at this scale. Easiest path; least ops overhead. Recommended starting point.
- **VPS** — DigitalOcean / Hetzner / AWS Lightsail droplet, manually configured. Cheaper at scale but you (or someone) has to manage backups, certificates, OS updates, etc.
- **Self-hosted on a fixed-IP machine** — e.g., a server at the office with a static IP. Cheapest in cash, most ops effort. Only worth it if there's already a competent sysadmin around.

**Why deferred.**
- The owner doesn't want to commit to a hosting decision yet.
- Phase 0–4 can be fully built and tested locally; the work to deploy is small once the choice is made (couple of hours for a managed PaaS).

**Triggers to revisit.**
- A salesman is ready to start using the app from the field.
- The owner wants to demo the app from his phone outside the office network.

---

## 4. Failed Recharge / Reversal Handling

**The idea.** Today the data model has no concept of reversing a Sale or Payment. If a recharge fails (rare, per the owner) or a payment was wrongly recorded, the only workaround is to soft-delete the offending entry, or to add a manually compensating opposite entry with a note.

**What this could look like.**
- A first-class **"Reversal"** action on any Sale or Payment that creates a linked opposite-direction entry, preserves the audit trail, and labels both entries as part of a reversal pair.
- Optional reason codes for common reversals: "recharge failed," "wrong amount entered," "wrong retailer selected."

**Why deferred.**
- The owner reports that failed recharges effectively don't happen, so the operational need is near-zero.
- Soft-delete with `deleted_reason` covers ~all real correction scenarios in V1.

**Triggers to revisit.**
- Recharge failures or correction cases start happening with any regularity.
- Accountant reconciliation flags the lack of explicit reversal records as a gap.

---

## 5. Margin / Commission Tracking

**The idea.** Track the business's margin on every Sale — i.e., the difference between what Ganpati Enterprises pays Jio for recharge credit and what it charges the retailer — and surface it in reports.

**What this could look like.**
- A configurable commission rate (could be flat percentage, or tiered, or per-retailer).
- Each Sale row stores or computes a `margin` figure.
- New report: monthly margin earned, per salesman / per retailer / per area.

**Why deferred.**
- Owner doesn't need margin visibility inside this app yet; his accountant handles it externally.

**Triggers to revisit.**
- Owner starts asking "how much did I actually make on this retailer last month."
- Commission rates start varying enough that off-app tracking becomes painful.

---

## 6. Multi-Business-Line Support (Airtel, VI, FMCG, …)

**The idea.** Today the app is Jio-recharge-only by design. The data model has no `business_line` column on Sale or Payment. If Ganpati Enterprises starts distributing for another telecom (Airtel, VI) or branches into FMCG or other products, the schema would need a new dimension.

**What this could look like.**
- A `BusinessLine` entity (Jio, Airtel, VI, FMCG, …).
- A `business_line` FK on `Sale` and `Payment` (and maybe `Retailer`, since a retailer might sell only Jio).
- All reports gain a "filter by business line" control.
- The salesman UI either shows a business-line picker when logging entries, or salesmen are scoped to one business line.

**Why deferred.**
- V1 is Jio-only.
- Adding this dimension now without a concrete second line would force design decisions on incomplete information.

**Triggers to revisit.**
- The owner adds a second business line.
- Even a small experiment (say, FMCG samples sold alongside Jio) starts producing entries that don't fit the current schema.

---

## 7. Messaging & Notifications

**The idea.** Bundle of communication features that send messages to retailers, salesmen, or the owner via SMS, email, or WhatsApp. Currently the app sends nothing — every notification is the owner's responsibility outside the system.

**Capabilities under this umbrella.**
- **Retailer transaction SMS** — when a salesman records a Sale or Payment for retailer R, send R an SMS (or WhatsApp message) confirming the transaction and showing the new Baaki. Acts as a digital receipt and reduces disputes.
- **Salesman OTP login** — replace username + password with phone-number + 6-digit OTP. Friendlier for low-tech users, removes the "forgot password" support load.
- **Daily summary email to the owner** — automated end-of-day email with headline numbers (today's Udhar, Jama, top Baaki).
- **WhatsApp reminder to retailers** with overdue Baaki — owner-initiated, prefilled message ("Mobile Shoppy, aap par ₹X Baaki hai. Kab clear hoga?").

**Provider options.**
- SMS / OTP: MSG91, Twilio (~₹0.20–0.30 per SMS in India).
- WhatsApp: WhatsApp Business API (requires verified business + provider partnership), or simple deep links (`wa.me/<phone>?text=…`) for owner-initiated messages.
- Email: SES / Postmark / Resend — basically free at this volume.

**Why deferred.**
- Each individual feature is small but they share infrastructure (provider account, templates, rate limits, opt-out tracking). Better to design once rather than retrofit piece by piece.
- V1 should prove the core ledger flow first; messaging is a multiplier on adoption, not a prerequisite for it.

**Triggers to revisit.**
- Retailers ask "how do I see my Baaki" — first sign that one-way visibility (transaction SMS) would help.
- Owner asks for an automated end-of-day digest.
- Owner wants to send dunning / collection reminders without doing it manually.
