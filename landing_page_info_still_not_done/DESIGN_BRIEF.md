# Redraft — Design & Content Brief (source of truth)

**Hand this to whoever designs/writes the marketing site.** The current landing page
(`index(1).html`) is a finished *layout* filled with **placeholder copy and inaccurate
claims** — **ignore its text as fact**. Everything below is the correct, verified product
information. Anything the product does **not** do yet is clearly marked, and a roadmap is
at the end so the design can accommodate future features.

> Rule of thumb for the writer: **only present features marked "LIVE today" as available.**
> Treat "Roadmap" items as future — don't imply they exist.

---

## 1. What Redraft is (one-liner + elevator)

**One-liner:** Edit and mass-produce PDFs in your browser — without re-typing or breaking
the layout.

**Elevator:** Redraft opens a PDF, lets you **click any text or number on the page and
change it** while keeping the original fonts and layout intact, then export a clean file.
For repetitive documents (invoices, certificates, contracts), you **pick the fields that
change, drop in a spreadsheet, and it generates one finished PDF per row**.

**Category:** PDF editing + document automation / bulk generation.

---

## 2. Who it's for

- Freelancers & small businesses issuing **invoices, quotes, receipts**.
- Anyone producing **certificates, contracts, agreements, ID cards, forms** in volume.
- Ops/admin people who currently retype the same PDF over and over or fight with Adobe.

**Core jobs-to-be-done:** "change a few values on a PDF without it looking edited," and
"make 200 versions of this document from a list."

---

## 3. What Redraft does **TODAY** (LIVE — safe to feature)

1. **Inline PDF editing** — click a word, price, or date on the rendered page and edit it.
   The surrounding layout is preserved (it erases the original text and re-stamps the new
   text in place).
2. **Font matching** — it auto-resolves the document's fonts (Google Fonts) so edits look
   native. If a font can't be matched, it tells you and lets you **upload the real `.ttf`/
   `.otf`** for a pixel-perfect result. Applies to single edits and bulk.
3. **Bulk generator (document-first)** — click only the spots that change, then **type the
   values, paste from Excel/Sheets, or upload a CSV**, and generate **one PDF per row,
   delivered as a ZIP**. It pre-fills each field with the original value and **auto-saves
   your setup per template** so you only paste fresh data next time.
4. **Recent documents / history** — re-open recent files with live thumbnails (stored
   locally in the browser).
5. **Light & dark mode**, real URLs/routing, smooth UI.

**Tech (for an honest "how it works" / trust section):** runs in the browser (React) with
a document engine (Python/PyMuPDF) that reconstructs and re-stamps text deterministically.
**It is precise and repeatable — not "AI guesswork."**

---

## 4. How it works (3 steps — good for a "How it works" section)

1. **Open** your PDF in the browser.
2. **Change** what you need — click text to edit it, or pick the fields that vary for a batch.
3. **Export** — download the edited PDF, or a ZIP of every generated document.

---

## 5. Value props / differentiators (lead with these)

- **It stays looking like the original.** Fonts + layout preserved — exports don't look
  "edited."
- **No Adobe, no re-typing, nothing to install.** Works in the browser.
- **One document → hundreds.** Turn a template + a spreadsheet into a batch in seconds.
- **Your real fonts.** Upload the exact font for an indistinguishable match.

---

## 6. ❗ Do **NOT** claim (these are on the current page but are false today)

- ❌ **"AI-Powered / AI reconstructs your PDF."** The engine is **deterministic, not AI.**
  Use *"automatic / smart / precise,"* not *"AI,"* unless/until a real AI feature ships.
- ❌ **"Secure cloud storage · 256-bit encryption · GDPR compliant · 99.9% uptime SLA."**
  There's **no cloud account, server storage, or SLA yet** — files are processed locally
  and stored in your browser. (You *can* truthfully say *"your files stay on your machine."*)
- ❌ **Fake social proof** — "500+ users," and the trust logos (Notion, Stripe, Linear,
  Vercel, Figma). Don't show customer logos you don't have rights to.
- ❌ **Unbuilt Pro features** — "team collaboration," "API access," "30-day version history."
  Don't list features that don't exist.

When in doubt, **under-claim**. The real product is strong enough on its own.

---

## 7. Brand & visual direction

- **Name:** Redraft. **Voice:** confident, plain-spoken, a little playful; no jargon, no
  buzzwords ("AI-powered synergy" = no). Short sentences. Show the product, don't oversell.
- **Type:** **Plus Jakarta Sans** for display/headlines, **Inter** for body. (Matches the app.)
- **Aesthetic:** modern SaaS, glassy/translucent surfaces, soft *natural* shadows, generous
  rounding (~16px), a clean product-window mock, subtle motion. Dark-mode-first, with a
  proper light mode.
- **Color (recommended — please make it consistent; today the app uses cyan while the
  landing uses blue):**
  - **Primary accent: blue `#2563eb`** (works in light + dark; professional).
  - **Optional energy accent: cyan `#00f5ff`** for highlights/glows only (it's the app's
    signature accent).
  - **Dark:** near-black slate background (`#020617`–`#131314`), white text, slate-400 muted.
  - **Light:** `#f8fafc` background, white cards, slate-900 text, slate-300 borders.
  - Keep one accent dominant — don't use blue and cyan with equal weight.
- **Theme toggle:** keep the existing circular **sun/moon** button (the real app uses the
  same one) — light via `data-theme="light"` on `<html>`, dark is default.
- **Logo:** a wordmark "Redraft" exists; a real mark/icon (`assets/icon-logo.svg`) still
  needs to be designed.

---

## 8. Recommended page structure + ready-to-use copy

Reuse the current sections, but swap the content:

1. **Nav** — Redraft · Features · How it works · Pricing · Sign in · **Get started**.
   (Drop "Tools / Enterprise / Docs" unless those pages exist.)
2. **Hero**
   > **Edit any PDF — right in your browser.**
   > Click any text or number, change it, and export a clean PDF. Fonts and layout stay
   > exactly as they were. No Adobe. No re-typing.
   > **[Start editing free]** **[See how it works]**
3. **Product showcase** — a real screenshot/GIF of the editor (preferred) or the existing mock.
4. **Features (3 cards — replace the generic ones):**
   - **Edit anything on the page** — Click a word, a price, a date; change it inline. The
     layout is preserved, so the result looks untouched.
   - **Your exact fonts** — Auto-matched fonts, or upload the real file for a pixel-perfect match.
   - **Generate hundreds at once** — Pick the fields that change, drop in a spreadsheet, get
     one finished PDF per row as a ZIP.
5. **How it works** — the 3 steps from §4.
6. **Bulk spotlight** — a section showing the spreadsheet → many-PDFs flow (it's the
   strongest differentiator; give it room).
7. **Pricing** — see §9 (numbers TBD by owner).
8. **FAQ** — suggested: *Does it change the look of my PDF? / What file types? / Where are my
   files stored? / Can I use my own fonts? / Is there a free tier?*
9. **Footer** — product links, real legal pages, real socials, correct year.

---

## 9. Business data the OWNER must confirm (don't invent)

- [ ] **Pricing** — Free tier limits; Pro **monthly** price; Pro **yearly** price (the
      "Save 30%" toggle); any Enterprise tier. (Current page shows Pro $3.99/mo — confirm.)
- [ ] **Real metrics** — any user/customer count, only if true.
- [ ] **Domain** — the mock shows `redraft.app`; confirm.
- [ ] **CTAs** — where do "Get started / Sign in / Start free" go? Is there auth yet?
- [ ] **Links** — which footer/nav pages exist (Docs, Enterprise, Privacy, Terms, socials)?
- [ ] **Support & company** — support email, legal name, address, copyright year, design credit.

---

## 10. Roadmap — future features to design around

Mark these as **coming soon / not yet** on the site (or omit). Useful so the layout leaves
room for them.

**Next (the obvious gaps to make the current claims real):**
- **Accounts + cloud sync** — real sign-up/login (the auth modals are designed but not wired),
  saved documents across devices, server-side storage. *(Unlocks the "secure storage" story.)*
- **Templates library** — save a configured template (picked fields + mapping) and reuse it.
- **E-signature / signing fields.**
- **More inputs for bulk** — connect Google Sheets / Airtable instead of a CSV upload.

**Later:**
- **Genuine AI features** — "rewrite this paragraph," summarize, translate, auto-fill from a
  prompt. *(Only then can the site honestly say "AI.")*
- **More formats** — DOCX import/export, images, **OCR for scanned PDFs**.
- **API access** for programmatic bulk generation.
- **Team workspaces & sharing / comments.**
- **Version history.**
- **Integrations** — Drive, Dropbox, Zapier; export to email.
- **Compliance** — encryption, GDPR, SLA (needed before claiming them).
- **Mobile / tablet experience.**

---

## 11. Assets still needed (referenced by the page, not yet created)

- **Logo mark:** `icon-logo.svg`, `icon-logo-nav.svg`.
- **Doc-type icons (SVG):** invoice, certificate, contract, agreement, form, idcard,
  report, sparkle, wand.
- **Photos:** real user avatars (or remove the social-proof row).
- **Behavior scripts:** `app.js` + `modal.js` (nav, theme toggle, pricing toggle, modals)
  are referenced but not present — write them, or link the CTAs into the real app.

---

*Summary for the designer: build a clean, dark-first SaaS landing page for **Redraft**, a
browser PDF editor + bulk generator. Lead with "edit any PDF, keep the look" and "one
template → hundreds of PDFs from a spreadsheet." Use the truthful copy in §8, avoid the
false claims in §6, keep the brand in §7, and leave room for the roadmap in §10. Fill
pricing/links/company from the owner (§9).*
