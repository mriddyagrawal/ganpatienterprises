# Ganpati Enterprises — Business Description (Jio Recharge Distribution Line)

## 1. What the business does

Ganpati Enterprises operates as a **distributor for Reliance Jio**, a major Indian telecom company. The business buys recharge stock (mobile data packs, talktime plans, etc.) from Jio at a distributor rate and resells it to **retailers** — small mobile shops, kirana stores, and similar outlets — across an assigned territory. Those retailers, in turn, sell the recharges to end consumers. Ganpati Enterprises sits in the middle of this chain:

> **Jio → Ganpati Enterprises → Retailer → Consumer**

The end consumer is not the user's direct concern; the relationship the business manages is with the retailer.

## 2. The field team

The owner currently has roughly **four salesmen** working on the ground. Their job is to physically visit retailers, sell them recharge stock, and collect payment (immediately or later).

## 3. The daily workflow

Every morning, **Jio issues a memo** to each salesman listing the specific retailers that salesman is supposed to visit that day. The salesman then heads out and works through that list, visiting each shop on the route. Each visit produces one (or more) transactions that need to be recorded.

## 4. What can happen at a visit

At any given visit, the salesman is doing one or both of these:

### (a) A new sale

The retailer buys recharge worth some rupee amount — say ₹10,000. The retailer can pay for this sale in **any combination of three modes**:

| Mode | Description |
|------|-------------|
| **Cash** | Physical currency notes handed over on the spot |
| **UPI / online** | Digital transfer (PhonePe, GPay, BHIM, bank transfer, etc.) |
| **Udhar (credit)** | Retailer takes the recharge now, promises to pay later. No interest. No fixed due date — could be 3 days, could be longer. It's effectively an interest-free loan extended on trust. |

A single sale can be split — e.g., ₹4,000 cash + ₹2,000 UPI + ₹4,000 udhar — and the three components must add up to the sale total.

### (b) An udhar repayment

The retailer simply hands the salesman money to settle some portion of a previous outstanding udhar balance. This payment can come as **cash or UPI**. It is unrelated to any sale happening that day — the shopkeeper is just clearing past dues.

### (c) Both at once

It is perfectly possible that on the same visit, the retailer (i) makes a fresh purchase **and** (ii) repays some old udhar. The system must let the salesman log both cleanly without conflating them, because they hit the books differently: a new sale increases revenue (and possibly outstanding udhar), while a repayment only reduces outstanding udhar.

## 5. Why this needs an app

Right now this is all tracked in **paper notebooks**. The result is messy handwriting, arithmetic mistakes, lost notes, and — most importantly — the owner has no real-time view of what's happening in the field. Reconciling outstanding udhar across dozens of retailers becomes a painful manual exercise. The web app's job is to:

- Let salesmen log transactions on the spot from a phone browser.
- Maintain an accurate, up-to-date **udhar ledger per retailer**.
- Give the owner an **admin/dashboard view** of cash collected, UPI collected, udhar issued, udhar pending, and salesman-wise performance.
- Eliminate notebook errors and reconciliation pain.

## 6. How the app models all of this

To keep the salesman UI as simple as possible, the app treats the business as a **two-direction ledger per retailer**:

- **Udhar** — every recharge sale, regardless of whether the retailer pays immediately or later, is logged as an outgoing entry that increases what the retailer owes.
- **Jama** — every rupee that comes back from the retailer (whether right after the sale or days later, in cash or UPI) is logged as an incoming entry that reduces what the retailer owes.

The retailer's outstanding balance (**Baaki**) at any moment is simply `Σ Udhar − Σ Jama`.

The full implementation plan (data model, screens, phases, stack) lives in [`PLAN.md`](PLAN.md).

## 7. Roles the app needs to serve

- **Salesman** — logs Udhar (sales) and Jama (payments) for any retailer; sees each retailer's current Baaki and history; sees his own day's totals.
- **Admin / Owner** — manages salesmen and retailers, sees all entries live, sees Baaki and aging per retailer, sees per-salesman collection figures, runs reports.

> **Note on memos.** Jio issues paper daily memos to salesmen each morning listing which retailers to visit. The current app deliberately does **not** model these — the salesman just opens the app and logs whatever happened. Digitizing memos is captured as a future possibility in [`futureplans.md`](futureplans.md).
