# Claude Design Prompt — SRA Engine Full UX Redesign

---

## PROJECT CONTEXT

This is **SRA** (Système de Réconciliation Automatisé) — a real internal enterprise tool used daily by a financial operations team at a Moroccan company. It automates the reconciliation of banking data across multiple Moroccan banks (Attijariwafa, BMCE, CIH, Credit Agricole, Albarid Banque, etc.).

The app is a single-page HTML application with a Python FastAPI backend. It has two pages:
1. **Overview / Dashboard** — the main daily workspace
2. **Configuration Editor** — a rarely-touched admin panel for managing bank mappings

This tool is used **every single day** by one or a small team of operators. Their daily ritual is simple and always the same:
- Receive RAR archive files from the banking network
- Upload them into the system
- Run the automated reconciliation pipeline
- Monitor execution in real time
- Download the output files (CSVs, Excel reports, TXT)

The current design was built in HTML/Tailwind and feels like a generic AI-generated dashboard — it's stylish but disconnected, with too many visual decorations and no clear sense of operational flow. The goal is a **complete 180° UX redesign** — not a reskin, but a rethinking of the entire experience.

---

## THE REAL PROBLEM WITH THE CURRENT UX

The current layout treats all elements as equal-weight panels placed side by side. But the actual user workflow is **strictly sequential**: Upload → Run → Monitor → Download. The interface needs to reflect this.

Problems to fix:
- The file upload zone and pipeline controls sit side by side as if they're equal — they're not. Upload comes first, always.
- The pipeline steps are shown as a 2×3 card grid — there's no visual sense of sequence or progression.
- The terminal log is buried below — when the pipeline runs, the log IS the most important thing on screen.
- Output files appear at the very bottom with no visual connection to the run that produced them.
- The "Run Full Pipeline" button looks like just another card in a grid.
- Navigation feels heavy and the sidebar adds visual noise.
- Dark decorative blur elements and grain textures make it feel like a consumer app, not an enterprise tool.

---

## WHAT THE APP ACTUALLY DOES (technical detail for design decisions)

### Overview Page — Core Features:

**1. File Intake**
- User drops RAR files into a drop zone
- Two modes: **Single** (one batch, 4 RAR files required) and **Multi** (multiple days, adjustable day count from 2 to 31)
- Required files: `ETATS_GAB.rar`, `ETATS_ACHAT_GAB.rar`, `ETATS_PAIEMENT.rar`, `report_mobile.rar`
- Files are uploaded immediately via POST to `/api/intake/upload?day=N`
- Upload failures are logged to the terminal

**2. Pipeline Operations**
Five sequential processing steps, each triggerable individually OR all at once:
- **Generate Mobile** — processes mobile banking data
- **Generate Hpsswitch** — processes HPS switch (card) data
- **Parse + Combine** — merges both data sources
- **Validate Data** — validates the reconciliation
- **Fill Excel Reports** — generates the final Excel output

Plus a **"Run Full Pipeline"** batch action that runs all 5 steps sequentially.

Each step has states: `pending` (gray dot) / `running` (animated blue, "Running..." badge) / `done` (green dot) / `error` (red dot).

There are also two **merge flags** (toggles): one for HPS Switch and one for Mobile — these enable/disable including those sources in the merge step.

**3. Pipeline Log Terminal**
Real-time SSE stream of pipeline events. Log entries have types: `INFO` (white), `WARN` (yellow), `ERROR` (red), `SUCCESS` (green). Has a "Clear" action. Currently uses a macOS-style dark terminal with traffic-light dots.

**4. Output Files Table**
Shows files produced by the last pipeline run. Columns: filename, file type (CSV/EXCEL/TXT), row count, actions (preview for text files, download for all). Preview opens a full-screen modal with the file content.

### Configuration Editor Page — Core Features:

**System Type toggle**: Mobile ↔ Monetique  
**Config File toggle**: BANK.csv ↔ BANK_PDF.csv

Each combination shows a different table of bank mappings:
- BANK.csv: Code, BIC/SWIFT, Label 1, Label 2, Extra
- BANK_PDF.csv: Code, BIC/SWIFT, Label 1, PDF Key

Table rows are **inline-editable** — clicking the edit icon turns the row into an input form inline. Rows can be deleted. Modified rows get a "MODIFIED" indicator, new rows get a "NEW" indicator.

**Add Member** opens a modal with a detailed form to add a new bank or split an umbrella bank into sub-members. Fields: Member Type, Operation (Add/Split), Code, SWIFT/BIC, Display Name, Short Name, PDF Key, CSV Aliases, Sign (Credit/Debit).

**Save Changes** pushes all dirty rows to the API.

---

## THE NEW UX TO DESIGN

Design a completely new interface that feels like a **professional financial operations console** — think Bloomberg meets Linear. Clean, dense, purposeful. Every pixel serves the workflow. No decorations. No gradients. No blur orbs. No grain textures.

### Overall Layout

**Left sidebar** — slim, always visible on desktop (not collapsible by default). Dark background. Only 2 navigation items: Overview and Config Editor. Shows a small "SRA" wordmark at top and the environment badge (PROD) at bottom. No user avatar section needed.

**Top bar** — minimal. Just the current page title on the left, and on the right: last run timestamp + notification bell. No hamburger on desktop.

**Main content** — generous left padding, clean white/near-white background, no decorative elements.

---

### Overview Page — New Structure

Redesign this page as a **top-to-bottom operational flow** — the user's eye should travel down the page in the exact order they work:

#### Zone 1: File Intake (top, prominent)
This is where every session starts. Design it as a **full-width intake station**:
- Single/Multi mode is a small segmented control (not prominent tabs) — most users always use Single
- In Single mode: a wide, clear drop zone with a simple dotted border and a file upload icon. Show a checklist of the 4 required RAR files as they are dropped in. Each file shows a checkmark when uploaded successfully, a spinner while uploading, an error state if failed.
- The required file names should be listed clearly beneath the drop zone as a reference
- In Multi mode: horizontally scrollable day columns, each with its own drop zone
- Day count control (+/−) appears only in Multi mode
- No "Select RARs" button is needed if the zone is large enough to be obviously clickable

#### Zone 2: Pipeline Execution (directly below intake)
Replace the card grid with a **horizontal pipeline track** — a visual progress bar showing the sequence of steps:

```
[Generate Mobile] → [Generate Hpsswitch] → [Parse + Combine] → [Validate Data] → [Fill Excel Reports]
```

Each step node shows:
- Step name
- A small icon
- A status indicator (dot or ring): idle / running (animated) / done (checkmark) / error (×)

Below the track, show the merge flag toggles (HPS Switch ON/OFF, Mobile ON/OFF) as small, clearly labeled inline toggles.

The **"Run Full Pipeline"** button should be large, full-width (or nearly), prominently placed below the stepper — clearly the primary call to action on the page. It should feel like a "launch" button, but without cheesy rocket icons. Just a clean, solid, high-contrast button with a play icon.

Individual step buttons: collapse into a **"Run individual step ▾"** disclosure, a small secondary action that expands to show the 5 steps individually. This removes clutter while keeping the functionality accessible.

#### Zone 3: Live Execution Monitor (auto-expands during run)
The terminal log should be **collapsed by default** when the pipeline is idle. When the pipeline starts, it auto-expands with a smooth animation. It should feel like a console panel that slides down.

Keep the dark terminal aesthetic — it's appropriate and useful. The macOS traffic-light buttons are fine. But reduce the height and make it feel integrated with the page, not like an afterthought.

#### Zone 4: Output Files (bottom, same page)
Show the output files table directly below the log — no separate route or scroll needed after a run. When the pipeline completes, this section should smoothly populate.

Clean table: filename (with file type icon inline), a minimal type badge (CSV/EXCEL/TXT), row count in monospace, and icon-only actions (preview eye, download arrow). Remove the "View All" link — show all files by default.

---

### Configuration Editor Page — New Structure

This page is rarely used, so it can be more utilitarian. But it should still feel like a professional data management interface.

**Header area**: Page title + description. Then a horizontal toolbar with:
- System Type segmented control (Mobile | Monetique) — use an underline tab style, not pill buttons
- Config File segmented control (BANK.csv | BANK_PDF.csv)
- Spacer
- "Add Member" as an outlined button
- "Save Changes" as a solid primary button — but only make it prominent/active when there are unsaved changes (dirty state)

**Table**: Tighter rows. The table should feel data-dense. Inline editing is fine as-is. The "MODIFIED" and "NEW" row indicators should be left-border colored strips (green for new, blue for modified) — subtle, not badges.

**Add Member modal**: This is complex and should feel like a proper form dialog — not a generic modal. The two-step flow (choose mode: Add vs Split) and the various field groups should be clearly sectioned. Field labels should be small and uppercase. The form should feel like filling out a Bloomberg terminal entry form — precise, functional, no fluff.

---

## DESIGN SYSTEM TO APPLY

Do not decorate. Every design decision should ask: "does this help the operator do their job faster?"

**Typography**: Inter. Clean hierarchy:
- Page titles: large, bold, dark
- Section labels: small, uppercase, letter-spaced, muted
- Body/table data: regular weight, dark
- Monospace for codes, BIC/SWIFT, file names, log output

**Colors**: A professional cool-neutral palette:
- App background: very light neutral (almost white, with a slight cool gray tint)
- Cards/surfaces: pure white, with a 1px light border — no shadows beyond a very subtle 1px definition
- Primary action: a confident, professional blue (not bright/electric — closer to Stripe's #2563EB or a deep navy)
- Success: a clean green for done states and success logs
- Error: a clear red for error states
- Warning: amber for WARN log entries
- Sidebar: dark — deep navy or slate-900
- Terminal: keep very dark (near-black) — it's appropriate for a log console

**No decorative elements**: Remove all:
- Background blur orb gradients
- Grain texture overlay
- Glass morphism effects (backdrop-blur on cards)
- Decorative shadows
- Gradient buttons
- Animated background elements

**Borders**: 1px, consistent, light. Cards have borders, not shadows.

**Border radius**: 8px on cards and panels. 6px on buttons. 4–6px on inputs. Full pill only for small status badges.

**Spacing**: Dense but breathable. Internal company tools should feel efficient, not spacious. Reduce padding compared to the current design.

**Interactive states**: All buttons and rows have clear hover states (background color change). Active/pressed states use scale(0.98). Focus states use a visible ring.

**Status indicators**: Small colored circles (8–10px) with appropriate animations. Running = slow pulse animation. Done = solid green. Error = solid red. Idle = light gray.

---

## STRICT CONSTRAINTS

- Output must be a **single HTML file** — do not split into multiple files
- Keep all existing element IDs and class names that are referenced in JavaScript:
  - `btn-gen-mobile`, `btn-gen-hpsswitch`, `btn-parse-combine`, `btn-validate`, `btn-fill-excel`, `btn-run-pipeline`
  - `terminal-log`, `btn-clear-terminal`
  - `files-table-body`, `config-table-body`, `config-table-head`
  - `add-member-modal`, `preview-modal`, and all their child element IDs
  - `merge-hpsswitch-toggle`, `merge-mobile-toggle`
  - `pipeline-status-badge`, `pipeline-status-dot`
  - `last-run-display`
  - All `data-page`, `data-type`, `data-file`, `data-action`, `data-index` attributes
  - All `data-member-type`, `data-member-mode`, `data-member-sign` attributes
- Do NOT modify any `fetch()` calls, SSE logic, event listeners, or API URLs
- Do NOT add backend logic — this is purely a frontend redesign
- You may add Google Fonts (Inter already present) and Lucide Icons via CDN (replacing Material Symbols if needed, but preserving all icon slot elements)
- Tailwind CDN is already loaded — you may keep it or replace with custom CSS, but do not break existing Tailwind utility classes that are manipulated by JavaScript (e.g., `hidden`, color classes added dynamically by PipelineManager)
- The dark mode toggle should remain functional
- The sidebar page navigation (Overview ↔ Config Editor) must remain functional
- The app must work identically after the redesign — only the visual layer changes

---

## DELIVERABLE

A complete single HTML file with the full redesigned interface. Analyze the entire structure first, then redesign from scratch with the above principles. The result should feel like a tool built by a senior product team for internal financial operations — not like a demo, not like a template, not like something generated by AI.
