# Fonts: licences and technical use

All fonts are **self-hosted** from `static/fonts/` (declared in `static/fonts/fonts.css`). The site loads nothing from a font service. The files are the unmodified `latin` and `latin-ext` woff2 subsets published in the npm packages `@fontsource/newsreader`, `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono` (version 5.3.0, package licence field `OFL-1.1`). We did not alter, re-subset or convert them. The licence text and copyright notice shipped in each package is copied next to the files as `LICENSE-*.txt`, as the OFL requires.

| | Newsreader | IBM Plex Sans | IBM Plex Mono |
|---|---|---|---|
| Author | The Newsreader Project Authors (Production Type; see the project's own notice) | IBM Corp. | IBM Corp. |
| Original source | github.com/productiontype/Newsreader (named in its licence text) | IBM's Plex project (copyright line in its licence text) | same |
| Obtained from | npm `@fontsource/newsreader` 5.3.0 | npm `@fontsource/ibm-plex-sans` 5.3.0 | npm `@fontsource/ibm-plex-mono` 5.3.0 |
| Licence | SIL Open Font License 1.1 | SIL Open Font License 1.1 | SIL Open Font License 1.1 |
| Commercial use | Yes | Yes | Yes |
| Self-hosting / embedding | Yes | Yes | Yes |
| Weights used | 400, 500, 400 italic | 400, 500, 600, 400 italic | 400, 500 |
| Å Ä Ö | Yes (checked in the files) | Yes (checked) | Yes (checked) |
| Latin Extended (š ž ā ė ł ...) | Yes, via `latin-ext` file, loaded only when needed | Yes | Yes |
| Size on first load (latin only) | ~21–24 kB per style | ~22–24 kB per style | ~14–15 kB per style |

OFL terms that matter here: the fonts may be used commercially, embedded and redistributed with the site; the copyright notice and licence must accompany them (done); the fonts may not be sold on their own. If anyone later modifies a font (for example re-subsets it), check the Reserved Font Name clause in the licence text first.

Loading: `font-display: swap`, `unicode-range` on every face, preloading only the three faces every page uses (Plex Sans 400, Newsreader 500, Plex Mono 400, latin). `font-synthesis: none`, so a missing weight is never faked. A test fails if a font file is unused, a face has no licence file, or a CSS rule names a family other than the three design tokens.

## Not included: Switzer, General Sans, Neue Montreal

- **Switzer** (Indian Type Foundry, distributed through Fontshare): the only copy reachable here was a third-party npm package (`@carrot-kpi/switzer-font`) whose metadata claims licence `MIT`, which cannot be right for Switzer, and which has no licence file. There is no official npm package. fontshare.com was blocked, and public sources disagree on whether self-hosting is permitted without ITF's consent. It was used **only in a local scratch directory to judge the design**, and no file or screenshot of it is in the repository. Before using Switzer: read the licence at fontshare.com/terms, and if self-hosting is permitted download the files from Fontshare itself.
- **General Sans** (Fontshare) and **Neue Montreal** (Pangram Pangram): not obtainable here, not evaluated, licences not checked. Neue Montreal is, as far as I know, a commercial family with a free personal-use version; verify before considering it.
