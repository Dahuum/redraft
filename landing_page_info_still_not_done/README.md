# Redraft — Landing Page Completion Guide

This is the to-do / content brief for finishing the landing page. The **design and
layout are done** (`index(1).html` + `styles(1).css`, `theme(1).css`, `tour(1).css`,
`modal.css`). What's missing to actually ship is: **(1) real copy** in a few sections,
**(2) image assets**, **(3) the two JS files the page loads**, and **(4) decisions** on
pricing, links, and a handful of claims that aren't true yet.

Work top-to-bottom; each section below tells you exactly what to provide.

---

## 0. The product, in plain words (use this so the copy is accurate)

Redraft is a **browser PDF editor + bulk document generator**:

- **Editor** — open a PDF, **click any text or number on the page and change it**; the
  original layout and fonts are kept. Export a clean PDF.
- **Fonts** — it auto-matches the document's fonts (Google Fonts), and you can **upload
  the real font** for a pixel-perfect match; it flags any font it had to substitute.
- **Bulk generator** — **click the few spots that change** (name, amount, date…), then
  either type the values or **paste / upload a CSV**, and it generates **one PDF per row**
  as a ZIP.
- **History** — recent documents with live thumbnails; reopen by URL.
- **Light & dark mode.**

**Tech reality (matters for honest copy):** the editing engine is **deterministic**
(Python + PyMuPDF redact-and-restamp with font resolution). It is **not AI**, and files
are currently processed by a local backend + stored in the browser — there is **no cloud
account, encryption, or SLA yet**. See §5 before repeating any marketing claim.

---

## 1. Assets the page needs (the `assets/` folder is missing)

The HTML/CSS reference these files, but there is **no `assets/` folder** here. Create it
and add:

**Icons (SVG):**
`icon-logo.svg`, `icon-logo-nav.svg`, `icon-invoice.svg`, `icon-certificate.svg`,
`icon-contract.svg`, `icon-agreement.svg`, `icon-form.svg`, `icon-idcard.svg`,
`icon-report.svg`, `icon-sparkle.svg`, `icon-wand.svg`

**Photos (JPG) — the hero "social proof" avatars:**
`user-1.jpg`, `user-2.jpg`, `user-3.jpg`

> If you don't have real user photos, either remove the avatar stack + "500+ users" line
> (see §5) or use neutral placeholder avatars.

---

## 2. Scripts the page expects (also not in this folder)

The HTML ends with:

```html
<script src="app.js"></script>
<script src="modal.js"></script>
```

Neither file is in this folder. They need to provide the page's behavior:

- **`app.js`** — sticky/scrolled nav (`.nav-wrap--scrolled`), mobile burger menu,
  the **theme toggle** (set/remove `data-theme="light"` on `<html>`, persist the choice,
  add `.theme-transitioning` during the switch), the **pricing Monthly/Yearly toggle**
  (swap `#pro-price` / `#pro-billed`), and the scroll-reveal / `.reveal-word` animation.
- **`modal.js`** — open/close the **Sign In / Sign Up / Forgot-password** modals
  (`#auth-overlay`, `#modal-signin`, `#modal-signup`, `#modal-forgot`) and the resend logic
  (`#resend-btn`).

> Decide whether you want these written from scratch, or whether the landing page should
> just **link into the real app** (e.g. "Get Started" → the app's sign-up). Tell me which
> and I can implement it.

---

## 3. Section-by-section: what to fill in

| Section (id) | What's there now | What YOU need to provide |
|---|---|---|
| **Nav** | Links: Features, Tools, Pricing, Enterprise, Docs | Which of these pages actually exist? Remove or build `#tools`, `#enterprise`, `#docs`. Confirm "Sign In" / "Get Started" destinations. |
| **Hero** | "Edit any PDF. Exactly how you want it." + "AI-Powered PDF Editing — No Adobe needed" + "Start Editing Free" | Final headline + sub-headline (see §6 for a truthful option). Decide the **AI** wording (§5). CTA target. |
| **Social proof** | "Already used by 500+ freelancers and businesses" + 3 avatars | A **real** number (or remove). Real avatars. |
| **Showcase (`#product`)** | A fake editor window (NDA contract demo) | Optional: replace the mocked window with a **real screenshot/GIF** of the app, or keep the mock. Update the demo text if you like. |
| **Mission** | Generic "AI integration is the way of the future…" | Your real one- or two-sentence mission. |
| **Features (`#features`)** | 3 **generic** cards: "Automate tasks", "Track and improve", "Track Your Success" — **none describe Redraft** | Replace with Redraft's 3 real pillars: **Edit anything on the page**, **Exact fonts**, **Bulk generate from a sheet** (draft copy in §6). |
| **Pricing (`#pricing`)** | Free ($0) and Pro ($3.99/mo, "Most Popular"), Monthly/Yearly toggle "Save 30%" | **Confirm real prices** + the **yearly price** the toggle should show. Confirm the **feature lists** per tier are accurate (e.g. "API access", "Team collaboration", "30-day version history" — do these exist?). |
| **"All plans include"** | Secure cloud storage · 256-bit encryption · GDPR compliant · 99.9% uptime SLA | **Only keep what's true today** (§5). |
| **Trust logos** | Notion, Stripe, Linear, Vercel, Figma | Are these **real customers**? If not, remove (using their logos implies endorsement — legal risk). |
| **Footer** | Product/Legal/Socials link lists, blurb, "© 2024 … by Casablanca" | Real social URLs, real legal pages (Privacy/Terms/Cookies), updated **year**, final blurb + credit. |
| **Auth modals** | Sign in / Sign up / Forgot / email-verify UI | The actual auth flow/back-end these submit to (or wire to the real app). |

---

## 4. Decisions checklist (fill these in)

- [ ] **Pricing** — Free tier limits? Pro monthly price? Pro **yearly** price (toggle)? Any third tier (Enterprise)?
- [ ] **Domain** — the chrome bar shows `redraft.app`; is that the real domain?
- [ ] **CTAs** — where do "Get Started" / "Start Editing Free" / "Sign In" go?
- [ ] **Nav/footer links** — which of Tools / Enterprise / Docs / Privacy / Terms / Cookies / socials are real?
- [ ] **Support** — support email / contact for the Free tier's "Email support".
- [ ] **Company** — legal name, address, the "by Casablanca" credit, copyright year.

---

## 5. ⚠️ Claims to verify before publishing (don't ship these if untrue)

These are currently asserted on the page but **don't match the app today**:

- **"AI-Powered" / "let AI reconstruct it"** — the engine is deterministic, not AI.
  Either add a genuine AI feature, or soften to *"automatic / smart"* PDF editing.
- **"Already used by 500+ freelancers and businesses"** — only if real.
- **Trust logos (Notion/Stripe/Linear/Vercel/Figma)** — only if they're real customers;
  otherwise remove (implied endorsement is a legal risk).
- **"Secure cloud storage · 256-bit encryption · GDPR compliant · 99.9% uptime SLA"** —
  there is no cloud/account/SLA yet (files are local + in-browser). Remove or rephrase to
  what's actually true (e.g. *"Your files never leave your browser unencrypted"* only if
  that's the case).
- **Pro feature list** — "Team collaboration", "API access", "30-day version history"
  aren't built; don't list features you can't deliver.

---

## 6. Optional starting copy (truthful, ready to paste)

**Hero**
> **Edit any PDF — right in your browser.**
> Click any text or number on the page, change it, and export a clean PDF.
> Fonts and layout stay exactly as they were. No Adobe, no re-typing.

**Features (replace the 3 bento cards)**
1. **Edit anything on the page** — Click a word, a price, a date — change it inline. The
   original layout is preserved, so the result looks untouched.
2. **Your exact fonts** — Redraft matches the document's fonts automatically, and you can
   upload the real font for a pixel-perfect result.
3. **Generate hundreds at once** — Pick the fields that change, drop in a spreadsheet, and
   get one finished PDF per row as a ZIP. Perfect for invoices, certificates, contracts.

**Mission**
> We're building the fastest way to change what a document *says* without breaking how it
> *looks* — so anyone can edit and generate professional PDFs in seconds.

---

## 7. What to hand back to me

If you fill in §4 (decisions) and tell me how you want §2 (the JS) handled — **write the
behavior from scratch** vs. **link the landing page into the real app** — I can finish the
page: add the missing scripts, swap the placeholder copy, and wire the CTAs/auth.
