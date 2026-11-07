# Design research and typography decision

Date: 2026-11-07.

## What could and could not be researched

**Not reachable from this environment** (the network policy refused or the host did not resolve): typewolf.com, fontsinuse.com, fontshare.com (including its download API), utopia.fyi, muz.li, fonts.google.com, economist.com, semafor.com, foreignpolicy.com and the daily-briefing sites. A proposal to allow them was rejected by the environments service, so the page-by-page reading of those sites you asked for **was not done**. Nothing below claims to be an observation of those pages' CSS or layout.

**What was done instead, and is verified:**

- Web search (standard) for 2026 font-pairing guidance and for Switzer's licence. Results were mostly low-authority SEO pages and are used only as weak context (see below).
- npm and GitHub were reachable. All OFL candidates were downloaded from npm (`@fontsource/*` 5.3.x) and **measured** with fontTools: x-height, cap height, average width, coverage of å ä ö and typographic punctuation, glyph count, tabular figures, file size per weight.
- Every candidate was rendered with identical text (specimen sheet, seen at 1400 px) and judged visually. The comparison is reproducible in the app at `/design/typografi` (with `DESIGN_PREVIEW=1`).
- Fluid type and space scales were computed with the method Utopia publishes (linear interpolation of a modular scale between a minimum and a maximum viewport, expressed as `clamp()`). That method is from my own knowledge; utopia.fyi itself could not be opened to check it.

**Design principles that come from memory, not from this session's reading** (treat as inspiration, not as evidence): The Economist's habit of a single small brand mark and strong sectional rules; Semafor's practice of separating what is known from what is argued with labelled sections; daily-brief publications' use of short numbered lead items before long text. These informed the ideas of the thin-rule structure, the labelled sections and the numbered lead items. If you allow the hosts, I can replace this paragraph with checked observations.

If you want the research done properly, allow these hosts for the session and ask again: `www.typewolf.com`, `fontsinuse.com`, `www.fontshare.com`, `api.fontshare.com`, `utopia.fyi`, `muz.li`, `www.economist.com`, `www.semafor.com`, `foreignpolicy.com`, `www.foreignpolicy.com`.

## What the search results said (weak sources)

- Serif display with sans text is the dominant 2026 pairing pattern; monospace works as a third, supporting voice for labels and metadata, not for text. (Several low-authority pages; no muz.li article was found.)
- Switzer: sources agree it is free for commercial use under the ITF Free Font License but **disagree on self-hosting**. One says self-hosting a webfont needs ITF's written consent and points to Fontshare's API; Adobe's ITF page lists self-hosting as a purchasable service; an older launch article says OTF/WOFF/WOFF2 downloads are offered for self-hosting. The official terms at fontshare.com/terms could not be read.

## Measurements (latin subset, weight 400)

| Font | Type | x-height | Cap height | Avg width | å ä ö | Tabular figs | File |
|---|---|---|---|---|---|---|---|
| IBM Plex Sans | static | 0.516 | 0.698 | 0.533 | yes | no | 22 kB |
| IBM Plex Mono | static | 0.516 | 0.698 | 0.600 | yes | no | 14 kB |
| Newsreader | static | 0.426 | 0.670 | 0.528 | yes | yes | 21 kB |
| Newsreader variable (opsz) | variable | 0.426 | 0.670 | 0.543 | yes | yes | 128 kB |
| Source Sans 3 | static | 0.486 | 0.660 | 0.471 | yes | no | 15 kB |
| Public Sans | static | 0.517 | 0.723 | 0.528 | yes | yes | 14 kB |
| Geist | static | 0.530 | 0.710 | 0.589 | yes | yes | 32 kB |
| Instrument Serif | static | 0.510 | 0.720 | 0.422 | yes | no | 20 kB |
| Switzer (third-party copy, evaluation only) | static | 0.531 | 0.680 | 0.557 | yes | no | 16 kB |

Findings:

- Every candidate covers å ä ö. **None** has U+2011 (non-breaking hyphen), so the design never relies on it. Baltic and Polish names (š, ž, ā, ė ...) live in each family's `latin-ext` file, which loads only when a page contains such characters.
- Newsreader's x-height (0.426) is much lower than Plex's (0.516). At equal size it reads smaller, so it is used for headlines, standfirsts and the analysis, where it is set larger, and not for lists, tables or small text.
- Plex's tabular figures were reported "no" because the font has no `tnum` feature, but its numerals are tabular by default. That is why dates and numbers align in Plex Mono and Plex Sans tables.
- Static weights are 14–32 kB each. The variable Newsreader (opsz) is 128 kB for one style, which is not worth it here.

## Visual judgement

| Candidate | Verdict |
|---|---|
| Newsreader + IBM Plex Sans | Editorial and credible. The serif headline against sans body gives clear contrast without decoration. |
| Switzer + Plex Mono | Clean, but the most neutral: the "modern product" look the brief wants to avoid. |
| IBM Plex Sans + Plex Mono | More character than Switzer (the a, g and the numerals) but with a technical, engineering flavour. |
| Newsreader for body too | Excellent for reading, but the low x-height needs larger sizes and makes labels, lists and tables less scannable. |
| Instrument Serif | Strong character but display-only, one weight; falls apart at section-heading size. |
| Public Sans, Geist | Correct but generic. Geist reads as a software product. |
| Source Sans 3 | Good humanist text face, with no distinct identity. |

## Three directions (same content at `/design/typografi`)

- **A. Modern editorial.** Newsreader 400/500 for headline, standfirst and the analysis; IBM Plex Sans 400/500/600 for body and interface; IBM Plex Mono 400/500 only for dates and metadata.
- **B. Nordic and functional.** IBM Plex Sans throughout, Plex Mono for metadata. (This is also your Alternative 3. Alternative 1, Switzer, is not shown: no verified file to ship.)
- **C. International security publication.** Newsreader for headline and body, Plex Sans for labels and navigation, Plex Mono for metadata.

## Decision: A (chosen by the editor after viewing all three)

Recommended in the analysis below and then chosen by the editor after comparing the three directions in the browser (`/design/typografi`). B and C stay in the preview as reference only.

The rule behind it is not taste: **serif is the publication's own voice, sans is the evidence.** The summary, standfirst and editorial analysis are set in Newsreader; verified facts, official assessments, media reports, sources and all interface text are set in IBM Plex Sans; metadata is Plex Mono. A reader can tell, from the type alone, whether they are reading what the publication argues or what it reports. C would blur that line and costs scannability; B is safe but has no editorial identity.

IBM Plex Sans and Plex Mono are one superfamily, so the design uses two families, not three.

## Principles carried into the design

1. Thin rules and numbering instead of cards: structure comes from lines, spacing and type size.
2. One small orange brand mark; orange otherwise only for "new / changed / attention" and focus.
3. Labels as text first, colour second, square, never pills. A situation marker is a small square **plus words**.
4. Fixed section order, each section marked by what kind of statement it holds.
5. The most important content is visible without scrolling on a phone: kicker, headline, standfirst and the first lead items.
6. A 66-character reading column; wider screens add a contents column, not wider text.
