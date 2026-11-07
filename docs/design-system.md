# Design system

Everything lives in `static/site.css` (tokens at the top) and `static/fonts/`. The site's CSP forbids inline styles and scripts, so there are none: no `style=` attributes, no `<style>`, no JavaScript. Tests enforce the rules marked ✔ below.

## Identity

Calm, factual, independent. A specialised international security publication, not a product or dashboard. Structure comes from thin rules, numbering, spacing and type, not from cards, shadows or colour fields.

## Colour

| Token | Value | Job | Contrast on white / on #F3F3F3 |
|---|---|---|---|
| `--charcoal` | `#282B28` | text, strong rules | 14.3 / 12.9 |
| `--orange` | `#D36135` | brand mark, rules, markers, focus ring. **Never small text** | 3.8 / 3.4 |
| `--grey-100` | `#F3F3F3` | the only tinted surface (footer, code) | |
| `--white` | `#FFFFFF` | page | |
| `--orange-ink` | `#AE4519` | small text in orange: new, changed, attention | 5.7 / 5.2 |
| `--blue` | `#1B5A96` | facts, sources, verified information, links in reports | 7.1 / 6.4 |
| `--red` | `#A8231B` | serious development, elevated risk | 7.2 / 6.5 |
| `--green` | `#2A6A38` | confirmation, positive development | 6.5 / 5.9 |
| `--muted` | `#5A5F5A` | background, history, unchanged, secondary text | 6.5 / 5.9 |
| `--line` / `--line-strong` | `#CFD1CF` / `#8E928E` | hairlines / borders that identify controls (3.2:1) | |

The brand orange reaches only 3.8:1 on white, which fails WCAG AA for running text, so it is used for lines, markers and large display only, and `--orange-ink` (same hue, darker) carries orange text. ✔ (no `color: var(--orange)`; all text colours ≥ 4.5:1; measured on 944 rendered text elements, lowest 5.73:1.)

Colour never carries a threat assessment alone: every marker comes with words ("Ökad uppmärksamhet", "Oförändrat"). Backgrounds are neutral white and cool grey; no warm or cream tones. ✔

## Typography

Families: Newsreader (the publication's voice: headline, standfirst, summary, analysis), IBM Plex Sans (evidence and interface: body, labels, lists, sources), IBM Plex Mono (dates and metadata only). ✔ (a test lists the only selectors that may use mono.) See `docs/fonts.md` and `docs/design-research.md`.

Fluid scale, 360 px → 1280 px, ratio 1.2 → 1.25, body 17 → 19 px (`--step--2` … `--step-5`, all `clamp()`). Smallest text is 12 px. ✔

| Role | Token | Notes |
|---|---|---|
| Home headline | `--step-5` (42 → 58 px) | Newsreader 500, line height 1.08, balanced wrapping |
| Report headline | `--step-4` | |
| Section heading | `--step-2` / `--step-1` | Newsreader 500 |
| Standfirst | `--step-1` | Newsreader 400, max 40 rem |
| Body | `--step-0` | Plex Sans, line height 1.6, measure 66ch ✔ |
| Metadata | `--step--1` | Plex Mono |
| Labels | `--step--2` | Plex Sans 600, uppercase, tracked |

Hierarchy comes from size, weight, line height and space; there is no all-bold text. Real font files only: `font-synthesis: none`.

## Space and geometry

Fluid space scale `--space-3xs` … `--space-2xl` (16 → 20 px base). Page width 80 rem with a fluid gutter. Border radius is 0, or 2 px on form controls only. ✔ No shadows, gradients, blur, animation or transitions. ✔ Rules: 1 px hairline for lists and sections, 2 px charcoal for the start of a primary section.

## Labels

Square, outlined, text first. One definition in `templates/_macros.html`.

| Label | Class | Colour | Meaning |
|---|---|---|---|
| BEKRÄFTAT | `label--facts` | blue | confirmed by a primary source or enough independent verification |
| MYNDIGHETSBEDÖMNING | `label--official` | charcoal | what an authority says or judges; not independently verified |
| MEDIERAPPORTERING | `label--media` | grey | reported, not confirmed by a primary source |
| ANALYS | `label--analysis` | filled charcoal | the publication's own judgement (the only filled label) |
| OSÄKERHET | `label--uncertainty` | charcoal, dashed | unclear, contradictory or missing |
| NYTT / FÖRÄNDRING | `label--new` / `label--change` | orange-ink | new or changed since the previous report |
| BEKRÄFTELSE | `label--confirmed` | green | an earlier claim is confirmed |
| BAKGRUND / OFÖRÄNDRAT | `label--background` | grey | context or no change |

Situation level (`level--*`): a 0.6 em square **and** words. Elevated uses red, attention orange, unchanged grey, positive green. The design never uses gauges, dials or large coloured shapes.

## Components

Masthead (brand bar, wordmark, navigation with `aria-current`), dateline, lede with numbered developments, regional situation table (stacks to labelled rows below 48 em), "since the previous report" list, issue list, report header with metadata, contents column (sticky on desktop; a native `<details>` on mobile, no JavaScript), report sections by kind (summary and analysis in serif; facts, official and media with a left rule; uncertainty in a dashed box; numbered sources with host names), archive filters (plain GET form, works without JavaScript), topic marks, method legend.

## Responsive behaviour

Mobile first. Breakpoints 48 em (tablet) and 64 em (desktop). One reading column on phones; on desktop an asymmetric layout (lead column + facts column; contents column + 66-character text). Checked with no horizontal scroll at 320, 375, 768, 1024, 1280 and 1600 px on every page.

## Pages and routes

| Page | Route | Template |
|---|---|---|
| Today | `/` | `index.html` |
| Report | `/reports/{date}` | `report.html` |
| Archive (filter by month, topic, text) | `/arkiv` | `archive.html` |
| Method | `/metod` | `method.html` |

The report page splits the rendered Markdown at the publication's fixed `##` headings (`app/presentation.py`), so the visual treatment follows from the section order the analysis already produces. Reports have no topic metadata yet, so the topic filter appears only when reports carry topics.

The method page describes the process the project is designed around (open sources, independent review by a second model, human approval). **Have the editor approve its wording before the site goes live.**

## Design preview

`DESIGN_PREVIEW=1` (off by default, never set it on the public site) serves the same templates with **fictional** sample content at `/design/`, `/design/rapport/2026-11-08`, `/design/arkiv`, `/design/metod` and `/design/typografi` (three typographic directions with identical text). Every preview page carries a "Designexempel, allt innehåll är påhittat" banner and `noindex`. Sample sources are named "(exempel)" and point at the reserved `.example` domain.

```bash
DESIGN_PREVIEW=1 uvicorn app.main:app --reload
```

## Files

`static/site.css`, `static/design.css` (comparison page only), `static/fonts/`, `templates/base.html`, `_macros.html`, `index.html`, `report.html`, `archive.html`, `method.html`, `typography.html`, `app/presentation.py` (dates, sections, summaries, filters), `app/pages.py` (view contexts shared by site and preview), `app/design_sample.py` (fictional content), `tests/test_design.py`.
