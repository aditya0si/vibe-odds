# Phase 0 — Historical NBA odds: what THIS machine can actually retrieve

**Probe date:** 2026-09-21 (all statements below are from live HTTP calls made on that date from this host).
**Host:** Windows 11, Hermes agent venv, Python 3.11.16, `requests` + `r.jina.ai` reader proxy.
**Raw evidence (this directory):** `probe-odds-espn.json` (60 KB, incl. two full odds payloads), `probe-odds-sbr.json` (7.5 KB), `probe-odds-kaggle.json` (8.4 KB).
**Relationship to prior work:** `nba-prediction-research/01-data-and-odds-sources.md` is the starting point; this probe **corrects four of its claims** (§8) and is the machine-specific "can we actually pull it" verdict.

---

## 0. Bottom line

1. **ESPN core API is the only source that fully passes, and it is materially better than previously documented.** It lists events per season (`seasons/{year}/types/2|3/events`) and returns per-book odds payloads that, **from 2023-24 onward, carry explicit per-book `open` AND `close` objects for spread and moneyline** — i.e. a free opening **and** closing moneyline. 2013-14 → 2016-17 also expose an aggregate `"Opening"` pseudo-provider with an opening ML. 2017-18 → 2022-23 give only a single frozen snapshot per book (no open/close split). 2012-13 is useless (junk providers only).
2. **SBR direct fetch FAILS from this machine** (egress-proxy category block, `307 → 45.112.151.2:8090/ips/block/webcat?cat=21`), not the SSL issue reported earlier. Through `https://r.jina.ai/<url>` it works, the index lists **16 season files (2007-08 → 2022-23)**, and header + full-season rows come back. **SBR has NO opening moneyline** — one `ML` column per team, aligned with the *close*.
3. **Kaggle: the URL in the task is 404.** The real slug is `...-october-2007-to-june-2024` (title still says "October 2007 to June 2026"). The whole domain is reCAPTCHA-walled from this host — the "HTTP 200, 20.5 KB" seen earlier was the *challenge page*, a false positive. Metadata is readable via `r.jina.ai`; the file itself is **not downloadable from this machine without an API key**.
4. Cheapest working stack: **ESPN core (free, 2012-13 → 2025-26) + SBR via reader proxy (free, 2007-08 → 2022-23)**. The Odds API remains the only *timestamped* book source (NBA from 2020-06-27, paid for historical).

---

## 1. ESPN core API (`sports.core.api.espn.com`) — PASS

All paths below are relative to `https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba`. Plain `requests` with a desktop UA works; no key, no cookie.

### 1.1 The event-LISTING endpoint (question 1a — answered)

| Form | URL | Verified result |
|---|---|---|
| Season + season type **(use this)** | `/seasons/{year}/types/2/events?limit=10&page=1` | **HTTP 200.** `{"count","pageCount","pageSize","pageIndex","items":[{"$ref": ".../events/<id>?lang=en&region=us"}]}` |
| Postseason | `/seasons/{year}/types/3/events?limit=3` | HTTP 200 |
| Season only, no `/types/` | `/seasons/{year}/events?limit=10` | **HTTP 404** `{"error":{"message":"Page Not Found"...}}` |
| Date | `/events?dates=YYYYMMDD&limit=10` | HTTP 200 |

Verified counts (the `seasons/{year}` argument is the **season-ending year**: 2013-14 → `2014`):

| season | `types/2` count (regular) | `types/3` count (playoffs, up to 2024-25) |
|---|---|---|
| 2013-14 (`2014`) | **1233** (pageCount 247 @ limit=5, 124 @ limit=10) | 89 |
| 2016-17 (`2017`) | **1234** (247 @ limit=5) | 79 |
| 2020-21 (`2021`) | **1113** (223 @ limit=5) | 85 |
| 2024-25 (`2025`) | **1239** (248 @ limit=5) | 84 |

`/events?dates=20140115&limit=10` → `count=12`; `/events?dates=20210115&limit=10` → `count=10`. **Note:** appending `&dates=` to the `/seasons/{year}/types/2/events` form is silently ignored (same 1233 count for 2013-14), so date filtering must use the `/events?dates=` form.

### 1.2 The odds endpoint

`GET /events/{eventId}/competitions/{eventId}/odds` → HTTP 200, `{"count", "items":[ {provider, details, spread, overUnder, spreadOdds, overOdds, underOdds, awayTeamOdds, homeTeamOdds, moneylineWinner, spreadWinner, ...} ]}`.

Outstanding detail: **`{eventId}` and `{competitionId}` are the same value** for NBA (e.g. both `400489439`). The `$ref` inside the event payload resolves to exactly this URL (note it uses `http://`, which works too).

### 1.3 What comes back per season (one event sampled per season, all HTTP 200)

| season | sample event | items | real sportsbooks | book names (sample) | item `open`/`close` | team-level `open`/`close` | ML | spread | total |
|---|---|---|---|---|---|---|---|---|---|
| 2012-13 | 400278284 | 3 | **0** | consensus, numberfire, teamrankings only | – | – | ⚠ junk feeds only¹ | ✔ | ⚠ junk feeds only¹ |
| 2013-14 | 400489439 | 9 | 6 | 5Dimes.eu, BETONLINE.ag, BOVADA.lv, Fantasy911.com, **Opening**, SportsBetting.ag | – | – | ✔ | ✔ | ✔ |
| 2014-15 | 400578877 | 9 | 6 | same set as 2013-14 | – | – | ✔ | ✔ | ✔ |
| 2015-16 | 400828480 | 10 | 7 | + PinnacleSports.com, Westgate | – | – | ✔ | ✔ | ✔ |
| 2016-17 | 400900029 | 13 | 10 | 5Dimes, BETNOW.eu, BETONLINE.ag, BOVADA.lv, BetUS.COM, **Opening**, SportsBetting.ag, SportsInteraction, Sportsbook.com, Westgate | – | – | ✔ | ✔ | ✔ |
| 2017-18 | 400975384 | 10 | 6² | CG Technology, Caesar's, Caesars Sportsbook, Unibet, Westgate, Wynn | – | – | ✔ | ✔ | ✔ |
| 2018-19 | 401071327 | 9 | 6 | Caesars variants, Unibet, Westgate, Wynn | – | – | ✔ | ✔ | ✔ |
| 2019-20 | 401161246 | 11 | 7 | CG Technology, Caesars, DraftKings, SugarHouse, Unibet, Westgate | – | – | ✔ | ✔ | ✔ |
| 2020-21 | 401267339 | 2 | **0** (junk only) | numberfire, teamrankings | – | – | ✗ | ✔ | ✔ |
| 2021-22 | 401360460 | 14 | 11² | Caesars (CO/NJ/PA), DraftKings, MGM, SugarHouse, Titanbets, Unibet, Westgate | – | – | ✔ | ✔ | ✔ |
| 2022-23 | 401468803 | 16 | 13² | + BetfairSportsbook, **ESPN BET**, Holland Casino, PointsBet | – | – | ✔ | ✔ | ✔ |
| 2023-24 | 401585183 | 12 | 11² | Caesars variants, DraftKings, ESPN BET, MGM, SugarHouse, Titanbets, Unibet | **YES** | **YES** | ✔ | ✔ | ✔ |
| 2024-25 | 401705127 | 2 | 2 | **ESPN BET, ESPN Bet - Live Odds** | **YES** | **YES** | ✔ | ✔ | ✔ |
| 2025-26 | 401810433 | 1 | 1 | **Draft Kings** (only) | **YES** | **YES** | ✔ | ✔ | ✔ |

¹ `consensus` (id 1004) in 2012-13 carries `spread` + `details` only — no `overUnder`, no `moneyLine`, no odds. The ML/total values that do appear for 2012-13 come only from `numberfire` (id 1003) and `teamrankings` (id 1002), which are **prediction feeds, not sportsbooks** — 2012-13 has **no book-level prices at all** (15 events sampled across 8 dates, incl. playoffs). ² counts exclude `accuscore` (a prediction feed, not a book).

Provider-count trend (question 1b): **0 → 6 → 6 → 7 → 10 → 6 → 6 → 7 → 14 → 11 → 13 → 11 → 2 → 1.** The list collapses after 2023-24: 2024-25 is ESPN BET only (plus an `"ESPN Bet - Live Odds"` in-game pseudo-book), and 2025-26 is **a single book ("Draft Kings")** on every event sampled (2025-11-01, 2026-01-15, 2026-03-20). Verified by resampling 3 dates × 3 events per season, not one event.

### 1.4 Open vs close — the decisive capability (question 1c)

Three distinct eras, all verified:

| Era | Opening price | Closing price | Moneyline |
|---|---|---|---|
| **2012-13** | ✗ | ✗ (junk only) | ✗ |
| **2013-14 → 2016-17** | **`provider id=23 name="Opening"`** item: opening spread + total + **opening moneyline**. Also every real-book item carries `initialSpread` / `initialOverUnder` (= the game's opening spread/total). | each book's single frozen snapshot | ✔ both open and close |
| **2017-18 → 2022-23** | ✗ (no `"Opening"` provider, no `open` key) | each book's single frozen snapshot | ✔ close only |
| **2023-24 → 2025-26** | **team-level `.open`** = `{favorite, pointSpread, spread, moneyLine}` per book — **opening moneyline AND opening spread**. Item-level `.open` holds only `over`/`under`/`total`. | **team-level `.close`** = same shape; item-level `.close` holds total + juice | ✔ both open and close |

Evidence for the `"Opening"` provider (2013-14, event 400489439, CHI @ ORL):

```json
{"provider":{"id":"23","name":"Opening"},
 "details":"CHI -4","overUnder":182.0,"spread":4.0,
 "initialSpread":4.0,"initialOverUnder":182.0,
 "awayTeamOdds":{"favorite":true,"moneyLine":-190,"spreadOdds":-110.0},
 "homeTeamOdds":{"favorite":false,"moneyLine":165,"spreadOdds":100.0},
 "moneylineWinner":false,"spreadWinner":false}
```

Evidence for the per-book open/close (2024-25, event 401705127, ESPN BET — **raw JSON of one event, as requested**):

```json
{"provider":{"id":"58","name":"ESPN BET"},
 "details":"NY -5.5","overUnder":219.5,"spread":5.5,"overOdds":-105.0,"underOdds":-115.0,
 "awayTeamOdds":{"moneyLine":-210,"spreadOdds":-110.0,
   "open":{"favorite":true,"pointSpread":{"alternateDisplayValue":"-6.5"},
           "spread":{"american":"-115"},
           "moneyLine":{"value":1.37,"american":"-270","decimal":1.37,"fraction":"10/27"}},
   "close":{"pointSpread":{"alternateDisplayValue":"-5.5"},
            "spread":{"american":"-110"},
            "moneyLine":{"value":1.476,"american":"-210","decimal":1.476,"fraction":"10/21"}}},
 "homeTeamOdds":{"moneyLine":175,"spreadOdds":-110.0,
   "open":{"favorite":false,"pointSpread":{"alternateDisplayValue":"+6.5"},
           "spread":{"american":"-105"},
           "moneyLine":{"value":3.2,"american":"+220","decimal":3.2,"fraction":"11/5"}},
   "close":{"pointSpread":{"alternateDisplayValue":"+5.5"},
            "spread":{"american":"-110"},
            "moneyLine":{"value":2.75,"american":"+175","decimal":2.75,"fraction":"7/4"}}},
 "open":{"over":{"american":"-115"},"under":{"american":"-105"},"total":{"american":"219.5"}},
 "close":{"over":{"american":"-105"},"under":{"american":"-115"},"total":{"american":"219.5"}},
 "moneylineWinner":false,"spreadWinner":false}
```

(Event 401705127 = *New York Knicks at Philadelphia 76ers*, tip `2025-01-16T00:00Z`, `details="NY -5.5"`. Full unmodified payloads for 400489439 and 401705127 are in `probe-odds-espn.json` → `full_payloads`. `propBets` and `links` keys also exist on 2024-25 items — not probed.)

Gotcha: the per-book `open` is **not universally populated** — in event 401585183 (2023-24) the DraftKings item has `close` but **no** `open`, while ESPN BET/Caesars/MGM/PointsBet/Unibet/accuscore on the same event have both. Treat `open` as best-effort per (event, book).

### 1.5 Is it the CLOSING price, or a live snapshot? (question 1d)

Verdict: **it is a finalised closing-grade snapshot, and for 2023-24+ it is explicitly labelled `close`.** Evidence, in order of strength:

1. **Independent cross-check against SBR.** ESPN event 400489439 = CHI @ ORL on 2014-01-15 local (tip `2014-01-16T00:00Z`). ESPN's 5Dimes snapshot: spread **CHI −6**, total **181**, ML **+210 / −230**; ESPN's `"Opening"` provider: CHI −4, total 182, ML +165/−190. SBR's 2013-14 page, date token `115`: `Chicago V Open 3.5 Close 6 ML -240` / `Orlando H Open 181 Close 180.5 ML 200`. **ESPN's numbers match SBR's `Close` column (6 / −240), not its `Open` column (3.5)** → ESPN's top-level numbers are the closing line.
2. In 2023-24+, the top-level `spread`/`overUnder`/`moneyLine` are **identical to the `.close` object** and differ from `.open` (2024-25 example above: top-level ML −210/+175 = `close`, vs `open` −270/+220).
3. Every item carries `moneylineWinner` / `spreadWinner` booleans — fields that only exist once the game is over. This is a post-game-finalised record, not a live feed.
4. **There is no timestamp anywhere in the payload.** Counted occurrences of `time`, `date`, `stamp`, `updated`, `snapshot` across event 400489439's full odds JSON: **0**. So as-of-ness cannot be proven from the payload; the closing interpretation rests on points 1–3.

**Consequence for the pipeline:** use these numbers as the *market baseline* (benchmark), never as an as-of feature, and record `source='espn_core'`, `basis='close'` (or `'open'` for the explicit open objects).

### 1.6 Caveats / traps (all verified)

- **`accuscore`, `numberfire`, `teamrankings`, `consensus`** are prediction feeds, not books. They carry fabricated-looking prices (`moneyLine: 0`, `spreadOdds: 53.0`, `overOdds: 99.99`) and must be filtered out by provider id: **keep {1,2,3,4,15,18,19,20,21,23,25,26,31,35,36,38,40,41,43,45,46,47,48,50,52,53,55,57,58,100}; drop {1001 accuscore, 1002 teamrankings, 1003 numberfire, 1004 consensus}.**
- **Per-event odds gaps exist inside otherwise-covered seasons.** On 2021-01-15, **2 of 10 games** (401267339, 401267345) had only numberfire+teamrankings, while the other 8 had 11–13 books. Same-day, same-season inconsistency → always null-check per event.
- `"… - Live Odds"` pseudo-providers (IDs 46, 59) are in-game feeds: they have `open` but **no `close`** — exclude them from closing-line work.
- Provider names are messy and duplicative ("Caesar's", "Caesars", "Caesars Sportsbook", per-state variants). Canonicalise before aggregating.
- 2012-13 is **not** a usable odds source despite the endpoint answering HTTP 200.

---

## 2. SBR (`sportsbookreviewsonline.com`) — direct FAIL, via reader PASS, **no opening moneyline**

### 2.1 Direct fetch with `verify=False` (question 2a)

**Fails — and not for the reason previously recorded.** `verify=False` does bypass the `SSLCertVerificationError`, but the request is then **redirected by the machine's egress proxy to a category block page**:

```
GET https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm
  → HTTP 307 → http://45.112.151.2:8090/ips/block/webcat?cat=21&pl=1&lu=0&url=...
    (category 21 = gambling; connection to the block host itself then times out)
```

Verified identically for `www.`/bare host, `http://`/`https://`, and with `trust_env=True/False`. **From this machine, SBR is unreachable directly, period.**

### 2.2 Via the reader proxy (works)

`GET https://r.jina.ai/<original-url>` (add header `x-respond-with: html` to get raw HTML rather than markdown) → HTTP 200.

- **Index** `https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm` → HTTP 200, 40,533 bytes of HTML, `<title>🏀Historical NBA Scores and Odds Archives …`. **Yes — the index lists season files**, via 16 links:

```
/scoresoddsarchives/nba-odds-2007-08   …   /scoresoddsarchives/nba-odds-2022-23
(2007-08, 2008-09, 2009-10, 2010-11, 2011-12, 2012-13, 2013-14, 2014-15,
 2015-16, 2016-17, 2017-18, 2018-19, 2019-20, 2020-21, 2021-22, 2022-23)
```

No `2005-06`, `2006-07`, or anything after `2022-23` → **16 seasons, 2007-08 → 2022-23.**

- **Exact season URL pattern:** `https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba-odds-<YYYY-YY>` (canonical redirects to the same with a trailing `/`). These are **HTML pages, not files** — there is no CSV/XLSX to download (`nbaoddsarchives.htm` for other sports are the same shape).

### 2.3 The "first 500 bytes" question — answer: **NO**

The first 500 bytes of a season page are pure HTML `<head>`; the odds table starts ~9.6 KB in.

```python
# season 2013-14, first 500 bytes of the returned document:
'<html lang="en-us">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,
 initial-scale=1, shrink-to-fit=no">\n<title>NBA 2012-13 - Sportsbook Reviews Online</title>\n
 <link rel="canonical" href="https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba-odds-2013-14/">…'
# "<td>" present in first 500 bytes: False.  First "Rot" (header) at byte offset 9744–9992 (varies by fetch).
```

So a 500-byte Range probe reveals **no** column header. You need roughly the first **10 KB** of the page to reach the header row. The **header row is** (13 cells, identical on the 2007-08 page):

```
Date | Rot | VH | Team | 1st | 2nd | 3rd | 4th | Final | Open | Close | ML | 2H
```

and the first data rows (2013-14, opening night) are:

```
1029 | 501 | V | Orlando  | 18 26 20 23 | 87  | 187   | 188.5 | 675   | 94.5
1029 | 502 | H | Indiana  | 23 17 29 28 | 97  | 12.5  | 12    | -1025 | 9.5
1029 | 503 | V | Chicago  | 15 18 25 37 | 95  | 187.5 | 188.5 | 180   | 3.5
1029 | 504 | H | Miami    | 17 37 24 29 | 107 | 5     | 5     | -220  | 93.5
```

**Through the reader the page comes back complete:** the 2013-14 fetch yielded **2,639 data rows spanning date tokens `1029` → `615`** (Oct 29 → Jun 15) — i.e. the reader-rendered page includes the part of the table that the earlier direct/plain fetch lost behind "Show more…". This is a significant practical win: no headless clicking needed *if* you go through `r.jina.ai`.

### 2.4 The `Open` / `Close` / `ML` semantics — and the opening-moneyline answer (question 2b)

**Plain answer: SBR has NO opening moneyline. There is exactly one `ML` column per team row, and it matches the CLOSE.** What you get per game is: **open spread + open total + closing spread + closing total + one (closing) moneyline per side + a 2H line.**

The trick (confirmed again this probe): **the two rows of a game hold different markets.**

- the **favourite's** row → `Open`/`Close` are the **point spread**;
- the **underdog's** row → `Open`/`Close` are the **game total**.

Examples harvested this probe:

| season | date | rows | reading |
|---|---|---|---|
| 2007-08 | 1030 | `502 H SanAntonio Open 12.5 Close 13 ML -1400` / `501 V Portland Open 184 Close 189.5 ML 900` | SAS is the −1400 favourite ⇒ its row is the **spread** (opened −12.5, closed −13); Portland the +900 dog ⇒ its row is the **total** (184 → 189.5) |
| 2013-14 | 115 | `701 V Chicago Open 3.5 Close 6 ML -240` / `702 H Orlando Open 181 Close 180.5 ML 200` | Chicago −240 ⇒ **spread** row (3.5 → 6); Orlando +200 ⇒ **total** row (181 → 180.5) |
| 2013-14 | 115 | `705 V Charlotte Open 1 Close 209 ML 110` / `706 H Philadelphia Open 205.5 Close 2 ML -130` | Philadelphia −130 favourite ⇒ its row is the **spread** (205.5 → 2 is the *dog-side* value; the spread lives on the H row here) — i.e. **decide favourite-side from the ML sign, never from V/H** |

Two consequences for the parser: (a) to get a game's opening line you must read *both* rows and pick by ML sign; (b) `ML` is a single column with no open/close distinction, and — cross-checked against ESPN above — it is the closing ML (−240 for a −6 closing favourite, not −165-ish for a −3.5 opener).

---

## 3. Kaggle — the task URL 404s; the domain is reCAPTCHA-walled

### 3.1 What the page actually is (question 3)

- **The URL in the task, `…/nba-betting-data-october-2007-to-june-2026`, does not exist: 404.** Fetched through `r.jina.ai`: `Warning: Target URL returned error 404: Not Found`.
- The **real** dataset is **`https://www.kaggle.com/datasets/cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024`** — the *slug* says 2024, the **title** says **"NBA Betting Data | October 2007 to June 2026"**. That mismatch is why the 2007-2026 slug looks plausible but 404s.
- **Every** Kaggle URL from this machine returns **HTTP 200 with a `~20.5 KB` reCAPTCHA challenge page** (`<title>Checking your browser - reCAPTCHA</title>`, `content-type: text/html`) — for the dataset page, for `api/v1/datasets/view/...`, and for `api/v1/datasets/download/...`, on **both** slugs. So the earlier phase-0 "kaggle.com dataset page PASS (HTTP 200, 20 506 bytes)" was a **false positive**: it was the bot wall, not the dataset. Direct scraping of Kaggle from this host is dead.
- Metadata **is** readable via `r.jina.ai` — this is what the dataset actually claims:

| field | value |
|---|---|
| title | `NBA Betting Data \| October 2007 to June 2026` |
| subtitle | `Scores, point spreads, totals, and other odds from all NBA games.` |
| licence | **`CC0: Public Domain`** |
| size | 2,493,308 bytes (2.49 MB) |
| usability rating | 0.7647 |
| date coverage (page histogram) | **2007-10-30 → 2026-06-13**, 20 buckets, ~1.1–1.3 k games each (2011-12 = 1,074 — the lockout) |
| flags | `regular` true 22,808 / false 1,632; `playoffs` true 1,592 / false 22,848 (**the two disagree on 40 rows**) |
| file list | **not visible**: the API's `files` array came back **empty** through the reader; the sibling doc names `nba_2008-2026.csv` — treat that as unconfirmed here |
| columns (author's own list) | `season, date, regular, playoffs, away, home, score_away/home, q1..q4+ot _away/_home, whos_favored, spread (always positive), total, moneyline_away, moneyline_home, h2_spread, h2_total, id_spread, id_total` |
| provenance (author) | SBR 2007-10-30 → **2023-01-16**; ESPN 2023-01-17 → 2025-06-22; "median odds" from sportsbookreview.com **since 2025-10-21**; *"I scraped SportsbookReviewsOnline.com and fixed a few errors."* |
| **moneyline after Jan 2023** | **NO.** Author's note verbatim: *"2H and Moneyline odds are absent from the ESPN data (since Jan 2023)."* Also: *"ESPN uses non-integer values exclusively so there are no pushes."* |
| open/close distinction | **none** — one spread, one total, one ML per side |

So the dataset is broadly **what it claims to be** (title/licence/date range match), *except* that the URL in the task is dead, there is **no opening line**, and **no moneyline after 2023-01-16**.

### 3.2 Can it be downloaded without an API key? (question 3, second half)

**Not from this machine.** `GET https://www.kaggle.com/api/v1/datasets/download/<owner>/<slug>` with no auth returns `text/html` = the reCAPTCHA page (not a zip; no `Content-Length`, no `Content-Disposition`). There are no local credentials either: `~/.kaggle/kaggle.json` **missing**, `KAGGLE_USERNAME` / `KAGGLE_KEY` / `KAGGLE_API_TOKEN` **unset**. Whether the endpoint would serve an anonymous zip from a clean (non-flagged) IP is **UNVERIFIED** here — it is blocked by the bot wall before that question is reachable.

---

## 4. Cheapest working alternatives for historical odds (question 4)

Ranked by (works-here, then price):

1. **ESPN core API — free, no key, WORKS TODAY.** Coverage **2012-13 → 2025-26** (regular + playoffs, ~1.1–1.24 k games/season). Multi-book 2013-14 → 2023-24 (6→14 books); single-book after (ESPN BET in 2024-25, DraftKings in 2025-26). Opening **and** closing **moneyline + spread + total** for 2023-24 → 2025-26 (per book, with gaps) and an opening ML for 2013-14 → 2016-17 via the `"Opening"` provider. The single best free source for this project.
2. **SBR via `r.jina.ai` — free, WORKS TODAY (one hop).** **2007-08 → 2022-23**, 16 seasons, ~1.2–2.6 k rows/season (playoffs included). Open + close **spread and total**, one (closing) **moneyline** per side, plus a 2H line. This is the only free source that reaches back before 2012-13. Caveat: it is a reader proxy, so respect the origin's terms and cache aggressively rather than hammering it.
3. **The Odds API — paid for historical, the only timestamped source.** NBA snapshots from **2020-06-27** (per its own "earliest historical timestamps" table, verified in the sibling doc); snapshots every 10 min from Jun 2020, 5 min from Sep 2022; 30 credits per (3-market × 1-region) snapshot ⇒ ~222 k credits for one closing snapshot per game over 6 seasons; the **$59/mo 100 k tier** covers it if you snapshot per slate instead of per game. Free tier (500 credits/mo) **excludes historical**.
4. **Kaggle `cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024` (CC0)** — free *if* you have an API key; gives **2007-10-30 → 2026-06-13** results + closing spread/total + ML (ML stops Jan 2023). Best used as a **cross-check** of the SBR-derived rows (it is literally SBR-scraped), not as the primary.
5. **`sportsdataverse` / `hoopR` (MIT R package, free)** — ESPN-derived NBA odds; usable as a second implementation, but inherits ESPN's era gaps. `parlay-api.com` claims a free tier with `sbr_close` back to 2007 (vendor claim, unverified).

GitHub is not a shortcut: `api.github.com/search/repositories?q=nba+odds+dataset` returns 5 repos total, top one `GogateVarun/NBA-Game-Predictor` (7★); code search needs auth. `wippa-studios/wippa-nba-data` ("NBA betting data 2016-2026", 1★) is **unvetted**.

**Not usable:** Basketball-Reference (ToS forbids ML use + competing datastore), OddsPortal (no-scrape clause), `stats.nba.com` (read-timeout from this host; works intermittently per the sibling probe).

---

## 5. Bonus finding — a **timestamped** line-movement series (free, ESPN core)

Undocumented but live: `GET /events/{eventId}/competitions/{eventId}/odds/{providerId}/history/{betTypeId}` and its sub-resource `…/history/{betTypeId}/movement`.

- `betTypeId`: **0 = moneyline, 1 = spread, 2 = total** (`…/nba/bet-types/{0,1,2}`).
- `history/{n}` returns `highlights`: `{awayTeam:{current, previous, high, open, low}, homeTeam:{…}}` (for totals: `{current, previous, high, open, low}` flat).
- `movement` returns the **full quote series with timestamps**:

```json
{"count":17,"pageIndex":1,"pageSize":25,"pageCount":1,"items":[
 {"awayOdds":-185.0,"homeOdds":166.0,"line":0.0,"lineDate":"2014-01-15T15:22Z"},
 {"awayOdds":-180.0,"homeOdds":162.0,"line":0.0,"lineDate":"2014-01-15T15:32Z"},
 …
 {"awayOdds":-234.0,"homeOdds":209.0,"line":0.0,"lineDate":"2014-01-15T23:52Z"}]}
```

Verified: 2013-14 event 400489439 → moneyline count **17** (2014-01-15T15:22Z → 23:52Z), spread count **21** (from 2014-01-09T00:00Z, line −4.5 → −6), total count **22** (182.5 → 180.5). 2022-23 event 401468803 → ML **31** quotes, spread **17** quotes (line −6.0 → −8.5, last `2023-01-16T01:50Z`).

**Critical limitation: it exists only for provider `1002` (`teamrankings`) — a prediction model, NOT a sportsbook.** Every real book (5Dimes 18, Pinnacle 2, Westgate 25, DraftKings 40, MGM 47, ESPN BET 58, …) returns `404 {"error":{"message":"no instance found"}}` for `/history/{n}`, in every season tested (2013-14 → 2025-26). It also disappears from the provider list after 2022-23 (2023-24 → 2025-26 all 404). So it cannot be your market baseline — but it *is* a free, timestamped, open→close series that is useful for (a) sanity-checking your own movement reconstruction and (b) a time-decayed "line direction" feature on the years it covers.

---

## 6. Repro (exact calls)

```python
import requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
B = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"

# 1) list a past season's regular-season events (2013-14 -> season year 2014)
requests.get(f"{B}/seasons/2014/types/2/events?limit=10&page=1", headers=H).json()   # count=1233
# 2) list one date's events
requests.get(f"{B}/events?dates=20140115&limit=10", headers=H).json()                # count=12
# 3) one game's odds (multi-book; open/close objects from 2023-24)
requests.get(f"{B}/events/401705127/competitions/401705127/odds", headers=H).json()
# 4) timestamped movement (teamrankings only)
requests.get(f"{B}/events/400489439/competitions/400489439/odds/1002/history/0/movement", headers=H).json()

# SBR (direct is proxy-blocked; reader proxy works):
import warnings; warnings.filterwarnings("ignore")
requests.get("https://r.jina.ai/https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm",
             headers={**H, "x-respond-with": "html"}, timeout=150).text
```

Raw artifacts written by this probe (all inside `docs/phase0/`):

| file | contents |
|---|---|
| `probe-odds-espn.json` | every endpoint tested with HTTP status + counts; the 14-season provider/open-close table; **full odds payloads** for events 400489439 and 401705127; movement-series samples |
| `probe-odds-sbr.json` | direct-fetch failures (with the exact 307 block URL), index links, index/first-500-byte strings, header row, first/last rows, the ESPN↔SBR closing cross-check |
| `probe-odds-kaggle.json` | all 6 direct request records (reCAPTCHA proof), reader results, full dataset metadata JSON + description, coverage buckets |

---

## 7. Corrections to `nba-prediction-research/01-data-and-odds-sources.md`

1. **"Moneyline + spread + total (closing) | SBR | … 2016-17 → 2019-20: SBR + ESPN multi-book"** — ESPN core is reachable and multi-book for **2013-14** onward, not 2016-17. The `"Opening"` pseudo-provider (2013-14 → 2016-17) was missed.
2. **"Opening moneyline: ✗ (SBR has one ML column only) … not available free anywhere."** **Wrong.** ESPN core gives an opening ML for 2013-14 → 2016-17 (`provider 23 "Opening"`) and per-book opening ML for **2023-24 → 2025-26** (`.open.moneyLine`). Only the **2017-18 → 2022-23** middle band lacks a free opening ML.
3. **"ESPN core odds = a single frozen snapshot with no open/close distinction"** — true only for **2017-18 → 2022-23**. From 2023-24 the payload has explicit `open`/`close` (item-level for totals/juice, team-level for spread + ML), and the top-level numbers equal `close` (verified against SBR).
4. **SBR failure mode:** it is not an SSL problem on this host, it is an **egress-proxy category block (cat=21 gambling)**, so `verify=False` alone does **not** fix it — the reader proxy is required.
5. **Kaggle "PASS, HTTP 200"** was the **reCAPTCHA challenge page**; the platform is bot-walled from here, and the task's 2026 slug is **404**. Also, the SBR season page read through the reader returns the **full season** (2,639 rows for 2013-14) where the earlier direct fetch stopped at Dec 31.

---

## 8. VERDICT TABLE

| Source | Seasons covered (verified here) | Open price? | Close price? | Moneyline? | Cost | Verdict from this machine |
|---|---|---|---|---|---|---|
| **ESPN core — odds** `/events/{id}/competitions/{id}/odds` | **2012-13 → 2025-26** (reg+post) | **2013-14 → 2016-17** (`Opening` provider: spread+total+ML) · **2023-24 → 2025-26** (per-book spread+ML, gaps) · ✗ 2017-18 → 2022-23 | **YES** all seasons with a book (<code>close</code> obj 2023-24+, frozen final snapshot before) | **YES** (both open and close where noted) | Free, no key | **PASS** — workhorse |
| **ESPN core — event listing** `/seasons/{y}/types/2|3/events` | 2012-13 → 2025-26 (1,113–1,239 reg events/season) | n/a | n/a | n/a | Free | **PASS** |
| **ESPN core — `history`/`movement`** (`/odds/1002/history/{0,1,2}/movement`) | 2012-13 → 2022-23 only | **YES** (first quote) | **YES** (last quote before tip) | **YES** with timestamps | Free | **PARTIAL PASS** — *model* provider (teamrankings), not a book; 404 for every real book |
| **SBR archive (direct)** | — | — | — | — | Free | **FAIL** — 307 → proxy `webcat?cat=21` block |
| **SBR archive (via `r.jina.ai`)** `…/scoresoddsarchives/nba-odds-YYYY-YY` | **2007-08 → 2022-23** (16 seasons; 2,639 rows for 2013-14) | **spread + total only — NO opening ML** | **YES** spread + total (+ one ML) | **closing ML only**, one per side | Free (1 reader hop) | **PASS** (indirect) |
| **Kaggle `…-october-2007-to-june-2024`** (task's `-2026` slug = **404**) | 2007-10-30 → 2026-06-13 | **✗** | ✔ (single value) | ✔ until **2023-01-16**, then **absent** | Free + API key | **FAIL here** — reCAPTCHA wall on page, `/view`, and `/download`; no local creds. Metadata only via reader |
| **The Odds API (historical)** | **2020-06-27 → present** | ✔ (walk `previous_timestamp`) | ✔ (last snapshot < `commence_time`) | ✔ | Paid (hist. from $30–59/mo; free tier excludes historical) | **PASS (paid)** — only *provable* as-of source |
| Basketball-Reference / OddsPortal | — | — | — | — | — | **DO NOT USE** (ToS) |
| `stats.nba.com` (nba_api) | — | n/a | n/a | ✗ | Free | Not an odds source |
