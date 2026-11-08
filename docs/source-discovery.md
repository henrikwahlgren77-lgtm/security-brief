# Source discovery

Discovery finds today's candidate pages on the **approved sources** so the operator no longer has to type URLs. It reads feeds (RSS/Atom) and index pages listed in `config/feeds.toml`, keeps items that are recent, new, on approved hosts and relevant, ranks them, and hands the URLs to the analysis job. It never calls a model and never treats a feed as evidence: the chosen pages are fetched, stored and checked like any other source.

## Set up (once)

`config/feeds.toml` **ships with no feeds.** No feed address could be verified from this repository (the build environment could not reach the sites), and an invented address is worse than none. Add the feeds you have checked in a browser, using the examples in the file. Every feed must be `https` and on an approved host (`config/approved_sources.toml`); anything else is refused when the file is loaded.

Then verify them. This fetches each feed, parses it, and reports status, item count and newest date, and writes nothing:

```bash
docker compose run --rm --entrypoint python analysis -m app.analysis.discovery --check-feeds
```

Output looks like `OK   Säpo: ok HTTP 200, 12 item(s), 12 with a date, newest 2026-11-08 05:10 UTC` or `FAIL ...: http_error HTTP 404`. Exit code 1 if any feed fails. Fix or disable failing feeds (`enabled = false`) before relying on them.

## Each run

```bash
docker compose run --rm analysis --date 2026-11-08 --discover
```

`--discover` runs discovery first, adds the selected URLs to any you give with `--url` or `--urls-file`, and then runs the normal job (fetch, analyse, validate, submit a **draft**).

To look at what discovery would pick without analysing anything, run it alone. Every decision is stored, and `--last` prints each one with its reason:

```bash
docker compose run --rm --entrypoint python analysis -m app.analysis.discovery
docker compose run --rm --entrypoint python analysis -m app.analysis.discovery --last
```

`--out FILE` writes the selected URLs as a normal URL list (titles become sanitised comment lines). It is meant for running discovery directly on a machine with the repository, not in the Compose service: `/input` is read-only there and a file written to the container's `/tmp` disappears when it stops.

## How items are chosen

For every feed item, in this order, with the first failing rule recorded as the reason:

1. a valid `https` URL on an **approved host**; otherwise skipped (a feed can link anywhere; only approved hosts are ever used);
2. not a duplicate of an item already seen in this run (URLs are compared after removing fragments, tracking parameters and trailing slashes; the newest copy is kept);
3. published **within the window** (default 36 hours). A date more than a day in the future is not trusted and treated as missing;
4. **not already used**: a page fetched successfully before the window started is old news, not new (`--include-fetched` turns this off). A page fetched earlier *today* does not count, so a same-day rerun still works;
5. **relevant**: for `relevance = "keywords"` feeds the title or summary must contain a keyword (matched at the start of a word, so "försvar" also matches "försvarsmakten"). Feeds that are entirely on topic use `relevance = "all"`.

Items without a date are kept but marked "age unverified" and ranked lower; the page's own date is checked later by the normal fetch. Remaining items are ranked by score: distinct keyword hits, +2 for a tier 1 source (authority), +1 if under 12 hours old, −1 if undated, then newest first. The top `max_urls` (default 20, at most 30) are selected; the rest are recorded as skipped with the reason "over the per-run limit".

Every item, selected or skipped, and every feed's outcome are stored (`discovery_runs`, `discovery_feeds`, `discovery_items`), so you can see exactly why a page was or was not included.

## Safety

Feeds are untrusted input.

- They are fetched through the same guarded fetcher as sources: `https` only, standard port, approved host (also after redirects), public addresses only, size, time and redirect limits. Nothing from a feed is stored as a source.
- The parser refuses any `DOCTYPE` or entity declaration (no entity expansion, no external entities), limits nesting depth, item count and field size, and a feed that is not well-formed XML is reported as failed, not guessed at.
- Text taken from feeds is stripped of control, format and bidirectional-override characters before it is stored, printed or written to a file, and URLs can never be smuggled in through a title.
- Discovery makes no model calls and uses no API keys. A test enforces that the module imports neither the adapters, the pipeline nor its own HTTP client.

## Limits

- No feed addresses are included (see above).
- A feed that is not well-formed XML (for example one with HTML entities such as `&nbsp;` and no DTD) fails instead of being repaired. This is deliberate and shows up in `--check-feeds`.
- No sitemaps. Index pages need an `include` pattern and carry no dates.
- Relevance is a transparent keyword match, not understanding: it can miss a relevant item that uses other words and keep an irrelevant one that matches. Edit the keyword list under `[keywords]` as you learn what is missed. The analysis still judges relevance later.
- Two feeds covering one story under different URLs are not merged.
- Not tested against any live feed from this repository.

## Existing solutions reviewed (GitHub First, 2026-11-07)

Method: shallow clones read as text (nothing executed): latest commit, licence, declared dependencies, size. Stars, open issues, security advisories and CVEs were **not** available (git read access only), so security status is **unknown** for both; no weighted score is given.

| Candidate | Latest commit | Licence | Runtime dependencies | Size |
|---|---|---|---|---|
| kurtmckee/feedparser 6.0.14 | 2026-07-30 (regular activity through 2026) | BSD-2-Clause | `feedparser-sgmllib`, `requests` | ~8,400 lines |
| tiran/defusedxml (0.8.0rc2 on main) | 2023-10-25 | PSF License 2 | none | ~1,000 lines |

- feedparser is the established tolerant parser, but it brings `requests` (a second HTTP stack next to the `httpx` already used and guarded here) and a vendored `sgmllib`, for the need to read a title, a link and a date. Its tolerance is also not wanted: this system prefers a failed, visible feed to a guessed one.
- defusedxml is the standard hardening wrapper, but main has not moved since 2023 and Python's bundled expat already defends against entity amplification. What we need beyond that, refusing every DOCTYPE and entity declaration, is a few lines on expat itself.

Decision: build a small strict parser on Python's standard-library expat (about 100 lines, no new dependency), behind the existing guarded fetcher. Revisit feedparser if real feeds turn out to be too messy for the strict parser and you accept its dependencies.
