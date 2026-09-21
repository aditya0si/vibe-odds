# Environment constraints — NBA workstream (phase 0)

Run date: **2026-09-21**
Repo: `C:\Users\oliad\Desktop\vibe-odds` (worked from commit `db631ce`; a sibling agent committed
`e653f65` mid-run — see §6)
Interpreter probe: `python -V` → `Python 3.11.16`
Disk probe: `df -h /c` → `C: 953G total, 881G used, 73G avail, 93% used` (single drive)

Every number below is from a command actually run during this probe; the command is shown next to it.
Raw probe scripts live in `%LOCALAPPDATA%\Temp\probe_*.py` (outside the repo, scratch only).

---

## 0. Headline constraints

1. **Every general-purpose host is reachable** — GitHub raw/HTML/API, PyPI + files.pythonhosted, HuggingFace, Kaggle, Wikipedia. GitHub-hosted CSV mirrors are a viable fallback strategy. (§1)
2. **`stats.nba.com` is reachable, but only with three specific `Sec-*` headers.** Without them every call stalls ~20 s and fails; with them the same call returns `200` in 0.2 s. This is reproducible and is the single most dangerous trap in the workstream. (§1.3)
3. **`www.sportsbookreviewsonline.com` is hard-blocked** — a Sophos TLS-inspecting firewall intercepts the connection, the cert chain can't be verified, and policy then redirects to a block page that is itself unreachable. `verify=False` does **not** help. (§2)
4. **Storage is a non-issue; wall-clock is a non-issue *if* (2) holds.** Full 3-layer detail for 27,500 games ≈ **8.5 GB** against **73 GB** free; ingest ≈ **13–27 h**. But a hand-rolled HTTP layer missing the `Sec-*` headers turns that into **≈458 h**. (§3, §4)
5. The repo has **no venv**; all project deps resolve to Hermes's own interpreter — see §2.4.

---

## 1. Host reachability with plain `requests.get`

Command:

```
python probe_env_hosts.py        # %LOCALAPPDATA%\Temp\probe_env_hosts.py
```

Headers sent: `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) vibe-odds-probe/0.1`, `timeout=20`,
`allow_redirects=True`, **default TLS verification** (no `verify=False`).

| Host / URL | Status | Bytes | Elapsed | Note |
|---|---|---|---|---|
| `raw.githubusercontent.com/pandas-dev/pandas/main/README.md` | **200** | 11,348 | 20.478 s | real file; slow first-touch |
| `github.com/` | **200** | 575,789 | 2.468 s | |
| `api.github.com/` | **200** | 2,396 | 0.197 s | fastest host measured |
| `pypi.org/simple/` | **200** | 46,229,633 | 2.372 s | full simple index |
| `huggingface.co/` | **200** | 176,948 | 0.403 s | |
| `files.pythonhosted.org/` | **404** | 10 | 0.314 s | root is 404 by design — see §1.2 |
| `www.kaggle.com/api/v1/datasets/list` | **200** | 88,776 | 0.524 s | JSON, but unfiltered — see §1.1 |
| `en.wikipedia.org/api/rest_v1/page/summary/National_Basketball_Association` | **200** | 2,668 | 0.398 s | REST API works |

**Verdict:** all eight are reachable. Plain `requests.get` with default verification works for all of them.
None of the general-purpose hosts is filtered.

### 1.1 Kaggle returns HTTP 200 with a captcha wall — do not trust status codes

Command:

```
python probe_nba_hosts.py   # probe list includes the ?search= variant
```

| URL | Status | Bytes | Content |
|---|---|---|---|
| `.../api/v1/datasets/list` | 200 | 88,776 | real JSON dataset listing |
| `.../api/v1/datasets/list?search=nba` | **200** | 20,457 | **HTML: `<base href="https://www.google.com/recaptcha/challengepage/">`** |

The filtered (i.e. actually useful) endpoint serves a **reCAPTCHA challenge page with HTTP 200**. Any
probe that only records status codes will record Kaggle as "usable". Targeted Kaggle dataset downloads
require credentials; treat Kaggle as **not a usable mirror** for this workstream.

### 1.2 `files.pythonhosted.org` root 404 is expected

Command:

```
python -c "import requests,json; j=requests.get('https://pypi.org/pypi/certifi/json',timeout=25).json(); \
u=[x for x in j['urls'] if x['packagetype']=='bdist_wheel'][0]['url']; r=requests.get(u,timeout=60,stream=True); \
print(u); print(r.status_code, r.headers.get('Content-Type'), len(next(r.iter_content(65536))))"
```

→ real wheel URL `https://files.pythonhosted.org/packages/0b/a7/71ac2cff56fec219ed242bb11b8efb69fcc4bec75db06fb7bfe35de520e6/certifi-2026.7.22-py3-none-any.whl`
→ **200**, `binary/octet-stream`, first chunk 65,536 bytes.

`files.pythonhosted.org` is fully usable; the 404 in §1 is a bare-root artefact.

### 1.3 GitHub raw costs ~21 s per *new TCP connection*, ~0.05 s when reused

Command:

```
python -c "import requests,time,warnings; warnings.filterwarnings('ignore'); \
U='https://raw.githubusercontent.com/pandas-dev/pandas/main/README.md'; \
[print(lab, [( (lambda t0: (requests.get(U,timeout=60) if s is None else s.get(U,timeout=60)) and round(time.time()-t0,2))(__import__('time').time())) for i in range(3)]) for lab,s in [('fresh',None),('session',requests.Session())]]"
```

Measured (cleaner form of the same loop):

* fresh connection per request: `[21.54, 21.27, 21.97]` s
* reused `requests.Session()`: `[21.24, 0.05, 0.04]` s

So the ~21 s is **TLS/inspection setup per connection**, not per request. **Any downloader touching
GitHub must reuse a `requests.Session`**; a per-request `requests.get` pays 21 s × N.

### 1.4 Bonus hosts relevant to fallback sourcing

Command:

```
python probe_nba_hosts.py
```

| Host | Status | Bytes | Elapsed | Verdict |
|---|---|---|---|---|
| `sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events` | **200** | 257 | 5.866 s | **works** — usable ESPN path |
| `site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard` | **403** | 449 | 0.808 s | blocked |
| `cdn.nba.com/static/json/liveData/boxscore/boxscore_0022400001.json` | **403** | 443 | 0.239 s | blocked |
| `www.basketball-reference.com/boxscores/` | **200** | 147,615 | 0.584 s | **works** — real HTML |
| `api.balldontlie.io/v1/games?per_page=1` | **401** | 12 | 1.102 s | reachable, needs API key |
| `stats.nba.com/stats/boxscoretraditionalv2?...` | **ReadTimeout** | — | 20.409 s | see §1.5 |
| `stats.nba.com/stats/leaguedashplayerstats?...` | **ReadTimeout** | — | 20.181 s | see §1.5 |

Note `sports.core.api.espn.com` works while `site.api.espn.com` 403s — they are separate allow/deny
decisions, so probe them independently.

### 1.5 `stats.nba.com` needs three `Sec-*` headers — isolated and reproducible

Command:

```
python probe_header_isolation.py   # %LOCALAPPDATA%\Temp\probe_header_isolation.py
python probe_final.py              # repeat runs for reproducibility
```

All probes: `GET https://stats.nba.com/stats/boxscoretraditionalv3` with
`GameID=0022400001&EndPeriod=0&EndRange=0&RangeType=0&StartPeriod=0&StartRange=0`, `timeout=20`, `timeout=20`.

Base header set = the body of `STATS_HEADERS` in
`nba_api/stats/library/http.py:10-23` (read with `read_file` on the installed package), minus the `Sec-*` keys:

```
Host, User-Agent (Mac Chrome/145.0.0.0), Accept, Accept-Language, Accept-Encoding,
Connection, Referer, Pragma, Cache-Control
```

| Variant | Result |
|---|---|
| base set only (no `Sec-*`), no session | **ReadTimeout 20.127 s** |
| base set only (no `Sec-*`), repeat | **ConnectionError 19.491 s** |
| `+ Sec-Fetch-Dest: empty` only | **ConnectionError 19.344 s** |
| `+ Sec-Ch-Ua` only | **ConnectionError 19.484 s** |
| **base + all three `Sec-*`** | **200, 17,353 bytes, 0.566 s** |
| **base + all three `Sec-*`, `requests.Session()`** | **200, 17,353 bytes, 0.191 s** |
| base + all three `Sec-*`, **Windows** UA instead of Mac | **200** (0.515 s) — `User-Agent` string is irrelevant |
| base + all three `Sec-*`, `Host` header removed | **200** (0.206 s) — `Host` is irrelevant |

The three headers that matter (verbatim):

```
Sec-Ch-Ua: "Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"
Sec-Ch-Ua-Mobile: ?0
Sec-Fetch-Dest: empty
```

**This is the whole ballgame.** The connection is *tarpitted* (accepted, then no bytes, then closed at
~19.5–20.4 s) when they are missing — it presents as "site is down / timing out", not as a 403. It requires
the **full set**; no single `Sec-*` header is sufficient. 4 failing variants and 3 succeeding variants were
each reproduced.

Corollary: `nba_api` works **because** it ships that exact header dict, not because it does anything
special at the transport layer. A hand-rolled `requests` ingest layer copying that dict works equally well.

---

## 2. TLS

### 2.1 `sportsbookreviewsonline.com` is hard-blocked — it is not a stale-cert problem

Command:

```
python probe_env_tls.py    # %LOCALAPPDATA%\Temp\probe_env_tls.py
```

| Attempt | Result |
|---|---|
| `requests.get('https://www.sportsbookreviewsonline.com/', verify=True)` | `SSLError ... SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate')` in **0.228 s** |
| same with `verify=False`, `allow_redirects=False` | **307** → `Location: https://45.112.151.2:8090/ips/block/webcat?cat=21&pl=1&lu=0&url=aHR0cHM6Ly93d3cuc3BvcnRzYm9va3Jldmlld3NvbmxpbmUuY29t`, `Content-Length: 0`, **no `Server` header** |
| following that redirect (`verify=False`, `timeout=25`) | **ConnectTimeout** to `45.112.151.2:8090` after 21.274 s |
| `openssl s_client -connect www.sportsbookreviewsonline.com:443 -servername ... -showcerts` | `verify error:num=20:unable to get local issuer certificate`; **chain length = 1 certificate** |

Chain inspection of the leaf the server presents:

```
openssl s_client -connect www.sportsbookreviewsonline.com:443 -servername www.sportsbookreviewsonline.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -serial -dates
```

```
subject=CN=sportsbookreviewsonline.com
issuer=C=GB, ST=Oxfordshire, O=Sophos, OU=NSG, CN=Sophos SSL CA_9qTYklI48xOlW0E, emailAddress=support@sophos.com
serial=7E1304BED6923A
notBefore=Aug  6 21:33:15 2026 GMT
notAfter=Nov  4 22:32:53 2026 GMT
```

The leaf is signed by a **Sophos NSG (Next-Gen Security Gateway) interception CA**, presented for the
requested SNI, with a 90-day cert minted 2026-08-06. This machine sits behind a **TLS-inspecting firewall
that also does URL-category blocking** (`/ips/block/webcat`, `cat=21`). DNS for the real site resolves
normally to Cloudflare (`nslookup` → `172.67.220.131`, `104.21.17.36`) — the traffic never gets there.

**Therefore, for SBR:**

* the TLS failure is interception, **not** a site misconfiguration and **not** a stale trust store;
* `verify=False` gets you a 307 to a block page, so it does **not** bypass anything;
* the block page itself is unreachable (21 s timeout), so there is no content to scrape even with TLS disabled;
* there is **no `requests`-level workaround**. Any SBR/odds data must come from another host, or from a browser session on a network path that is not filtered.

Control comparison (proves interception is selective, not global) — same command against a working host:

```
issuer=C=GB, O=Sectigo Limited, CN=Sectigo Public Server Authentication CA DV E36
```

A real public CA. So per-host behaviour differs and must be probed per host.

### 2.2 The global certifi bundle is **NOT** stale

Command:

```
python probe_certifi.py    # %LOCALAPPDATA%\Temp\probe_certifi.py  (uses `cryptography` from the venv)
```

| Fact | Value |
|---|---|
| `certifi.where()` | `C:\Users\oliad\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\certifi\cacert.pem` |
| `certifi.__version__` | `2026.05.20` |
| bundle size | 236,095 bytes |
| PEM blocks | **119**, all 119 parsed successfully |
| already expired | **0** |
| newest roots by `notBefore` | `2024-05-15` (`TrustAsia TLS ECC/RSA Root CA`), `2023-07-17`, `2023-05-31` |
| oldest root by `notBefore` | `2007-12-13` (`Izenpe.com`) |

A 4-month-old certifi with 119 roots, zero expired roots and roots minted as recently as 2024-05-15 is
healthy. **Do not "fix" this host by upgrading certifi** — upgrading will not make SBR verifiable, because
the signer is a private Sophos CA that will never be in any public bundle. It is also worth noting
`SSL_CERT_FILE` is already set:

```
env | grep -i ssl_cert
→ SSL_CERT_FILE=C:\Users\oliad\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\certifi\cacert.pem
```

### 2.3 Every other host verifies cleanly

Command: `python probe_env_tls.py` (the `tls_verify` section performs a **verified** handshake per host)

| Host | Verified handshake |
|---|---|
| `raw.githubusercontent.com` | OK |
| `github.com` | OK |
| `api.github.com` | OK |
| `pypi.org` | OK |
| `huggingface.co` | OK |
| `files.pythonhosted.org` | OK |
| `www.kaggle.com` | OK |
| `en.wikipedia.org` | OK |
| `stats.nba.com` | OK (TLS fine — the §1.5 failure is at the HTTP layer) |
| `site.api.espn.com` | OK (TLS fine — the 403 is policy) |
| `www.sportsbookreviewsonline.com` | **FAIL: unable to get local issuer certificate** |

All negotiate **TLS 1.3**. So the TLS trust problem is confined to the one filtered host.

### 2.4 The interpreter is Hermes's venv, not a project interpreter

Command:

```
which python && python -c "import pandas, nba_api, certifi; print(pandas.__file__)"
```

`which python` → `/c/Users/oliad/AppData/Local/hermes/hermes-agent/venv/Scripts/python`, and every project
dependency (`pandas 3.0.5`, `numpy 2.4.3`, `scikit-learn 1.9.0`, `lightgbm 4.7.0`, `pyarrow 25.0.1`,
`fastapi 0.133.1`, `uvicorn 0.41.0`, `pytest 9.1.1`, `nba_api 1.11.4`, `requests 2.33.0`) resolves under
`...\hermes\hermes-agent\venv\Lib\site-packages\`. There is **no venv in `vibe-odds`**.

Consequences: the workstream silently depends on Hermes's environment; anything that reinstalls/upgrades
that env (including a `certifi` bump) can change the workstream's behaviour; and a future pinned,
reproducible env should be built before the ingest runs, not after.

---

## 3. Disk / storage budget

Measured anchors (all from this machine):

| Quantity | Command | Measured |
|---|---|---|
| Box score V3, 1 game, raw JSON | `python probe_nbaapi_meas.py` | **20,377 B mean** (10 games; range 18,980–21,730; era range 18,285–20,977) |
| Box score V3 → parquet+`zstd`, 5 games, 162 rows × 36 cols | `python probe_compress.py` | 99,565 B raw → 32,109 B = **ratio 0.3225** |
| Play-by-play V3, 1 game, raw JSON | `python probe_nbaapi_meas.py` | **232,989 B** (476 rows × 25 cols) |
| PBP V3 other eras | `python probe_ua.py` | 253,328 B (2005-06, 527 rows), 223,394 B (2010-11, 467), 300,514 B (2025-26, 611) → mean **252,556 B** |
| PBP V3 → parquet+`zstd` | `python probe_compress.py` | 232,989 B → 28,791 B = **ratio 0.1236** |
| Box score **summary** V3 (inactives + officials + arena + line score), 1 game | `python probe_nbaapi_meas.py` | **15,704 B** |
| Inactives table alone (7.7 rows/game avg) | `python probe_arith.py` | **619.2 B/game** parquet |
| Game logs, 1 season (`LeagueGameFinder 2024-25`) | `python probe_arith.py` | 524,683 B raw, 2,802 rows, 1,401 games → 86,969 B parquet = **ratio 0.1658** |
| Free disk | `df -h /c` | **73 GB avail**, single `C:`, 93% used |

Game-count basis: `LeagueGameFinder` per season gives 2,460 regular-season rows = **1,230 games**, plus
~85–89 playoff games and ~6 play-in games → **~1,322 games/season** (excluding preseason), so
**21 seasons ≈ 27,400–27,500 games**. Sibling measurements in `probe-nba-api-raw.json` (`season_log`)
confirm 1,230 regular games in 2005-06/2008-09/2012-13/2016-17 and the COVID-shortened 1,059 (2019-20) and
1,080 (2020-21). The figures below use **G = 27,500**, **S = 21**.

Command:

```
python -c "MB=1024**2; GB=1024**3; G=27500; S=21; ..."      # see probe_arith.py for the full expression
```

| Level | Raw JSONL on disk | Derived (parquet + zstd) |
|---|---|---|
| **(a)** game logs + box scores + inactives | **544.9 MB** | **190.3 MB** |
| **(b)** (a) **+ play-by-play** | **7,168.5 MB (7.0 GB)** | **1,009.0 MB (0.99 GB)** |
| **(c)** (b) + raw JSONL caches for summaries (full 3-layer raw retention) | **7,580.3 MB (7.4 GB)** | **1,141.8 MB (1.1 GB)** |
| peak with raw **and** derived both present | — | **8.52 GB = 11.7 % of free space** |

Reading: play-by-play is **92 %** of the storage (6.4 GB raw / 0.86 GB parquet of the totals) while
contributing the highest compression (12.4 % vs 32 % for box scores). Box scores + logs + inactives are
under 550 MB raw — noise.

### Recommended pruning policy

Given 73 GB free and an 8.5 GB worst case, storage is not the binding constraint, so the policy should
optimise for **recoverability**, not for space:

1. **Never keep 21 seasons of raw JSONL long-term.** Raw is 7.4 GB vs 1.1 GB derived and is re-fetchable.
2. **Prune per season, not per run.** Keep raw JSONL for the season currently being ingested; once that
   season's parquet is written and row-count-validated, delete the raw directory. Peak raw cache then
   drops from 7.4 GB to ~**360 MB** (measured: `(G/S) × (PBP_RAW + BS_RAW + SUM_RAW)`).
3. **Keep the derived parquet layer permanent — but only (a) and (b).** Do not persist summary JSONL
   after extracting `InactivePlayers` + `Officials`; that alone saves 432 MB raw for ~13 MB of tables.
4. **Write the game-log index first.** It is 21 calls (one per season, §4) and is the manifest every other
   layer joins against — it makes partial re-fetch cheap and makes "what's missing" answerable without a
   full rescan.
5. **Budget for temp, not just output.** SQLite/parquet writers can transiently double the derived layer;
   1.1 GB derived → allow ~2.5 GB headroom. Still trivial against 73 GB, but the drive is 93 % full and
   shared, so do not let a cache leak run unattended.
6. Do **not** keep the raw layer "just in case" for all 21 seasons: 7.4 GB of re-fetchable bytes is the
   only thing here that could plausibly collide with other agents on a 93 %-full drive.

---

## 4. Rate / throttle sanity

### 4.1 Ten sequential `nba_api` calls, wall clock

Command:

```
python probe_nbaapi_meas.py    # 10 × BoxScoreTraditionalV3, game ids 0022400001..0022400010, timeout=30
```

```
total_wall_s = 10.403        n_ok = 10/10
per_call_s   = [2.562, 0.696, 0.526, 0.488, 0.446, 0.415, 0.473, 0.519, 0.452, 0.448]
mean_all_s   = 0.703         (includes the cold first call)
mean_steady  = 0.496         (calls 2–10)
mean_bytes   = 20,377
```

Single-call timings for the other two layers (same script and `python probe_ua.py`):

| Endpoint | Seconds (measured) |
|---|---|
| `BoxScoreTraditionalV3` | 0.415 – 2.562 (steady 0.496) |
| `PlayByPlayV3` | 0.601, 0.897, 0.961, 1.811 → use **0.75** |
| `BoxScoreSummaryV3` | 0.582 (mine); 0.33–0.48, mean **0.43** (sibling `probe-nba-api-raw.json` → `throttle.A-mixed-summary`) |
| `LeagueGameFinder` per season | 0.49 – 4.34 → use **1.6** |

**No throttling observed:** 10/10 calls succeeded, no `429`, no slowdown across the sequence. The sibling
probe's two 10-call throttle runs also recorded 0 failures / 0 HTTP 429 across 20 calls. So ~30 calls in
total were clean — that is a small sample and does not prove sustained multi-hour behaviour is safe.

### 4.2 Projected ingest for 27,500 games

Model: 1 call/game (a), 2 (b), 3 (c). Per-call constants used in the table: **0.496 s** box score,
**0.75 s** play-by-play, **0.43 s** summary; `0.6 s` sleep applied per call, `G = 27,500`.

| Scope | Calls | No sleep | With 0.6 s sleep/call |
|---|---|---|---|
| **(a)** box scores only | 27,500 | **3.79 h** | **8.37 h** |
| **(b)** box scores + PBP | 55,000 | **9.52 h** | **18.68 h** |
| **(c)** box scores + PBP + summaries (inactives) | 82,500 | **12.80 h** | **26.55 h** |

(Game logs are excluded above because they are nearly free — see §4.3.)

**The same arithmetic if the `Sec-*` headers are missing** (every call burns the ~20 s stall before failing):

| Scope | Counterfactual |
|---|---|
| (a) 27,500 calls × 20 s | **152.8 h** |
| (b) 55,000 calls × 20 s | **305.6 h** |
| (c) 82,500 calls × 20 s | **458.3 h** |

A 15–20 day schedule absorbs 26.55 h easily. It does **not** absorb 458 h. The difference between those two
outcomes is three HTTP headers.

### 4.3 The game-log layer is nearly free — and silently truncates

Sibling probe (`docs/phase0/probe-nba-api-raw.json`, `cap_check`): one `LeagueGameFinder` call returned
**30,000 rows / 14,998 distinct games** spanning `2015-10-22 → 2026-06-13` in **5.12 s**, with
`delivered_first = 2026-06-13` and `delivered_last = 2015-10-22`.

Two consequences:

1. **Cost:** the whole game-log layer is **21 calls ≈ 0.6 min**, not 27,500 calls. Correct the ingest model
   accordingly — the 27,500-call figure applies only to per-game box score / PBP / summary.
2. **Trap:** the payload is **row-capped at 30,000 rows** and delivered newest-first, so a single
   "all seasons" call **silently drops the oldest games** while returning HTTP 200. Game logs must be
   fetched **one season at a time** (21 calls) and validated per season.

---

## 5. What would make a 15–20 day schedule unrealistic

Ordered by expected damage:

1. **A hand-rolled HTTP layer without the three `Sec-*` headers (§1.5).** This is the schedule-killer:
   26.55 h → 458.3 h for scope (c), and it *presents as a network outage*, so an agent will spend days
   diagnosing, retrying, adding sleeps, and possibly concluding the data source is unavailable — then burn
   more days on a fallback pipeline that was never needed. Mitigation: use `nba_api` for ingest, or copy
   `STATS_HEADERS` verbatim; and **assert on a single known-good call before starting any batch.**
2. **Opening a fresh TCP connection per request on this network (~21 s, §1.3).** Measured on
   `raw.githubusercontent.com`: 21.5 / 21.3 / 22.0 s fresh vs 0.05 s reused. If the fallback path fetches
   many GitHub-hosted CSVs without a `requests.Session`, 27,500 requests ≈ 160 h of pure handshake. Same
   failure signature as (1).
3. **`sportsbookreviewsonline.com` has no network path (§2.1).** TLS interception + URL-category block +
   an unreachable block page. `verify=False` does not help. If any part of the workstream needs SBR odds,
   that part cannot be delivered from this machine via `requests`, and the browser-automation tool has
   separately been reported (prior workstream context, not re-measured here) to time out at 420 s. Treat
   SBR as **out of scope** and re-source, rather than spending days attempting a bypass.
4. **Sustained-rate behaviour is unverified.** Only ~30 calls were run back-to-back. All succeeded with no
   429 and no latency growth, but a multi-hour 82,500-call run through a TLS-inspecting firewall is exactly
   the pattern that trips an IPS or an Akamai rate limiter. Mitigation: checkpoint per season, make the
   ingest resumable, and start with one full season (≈1,322 games) as a canary before committing to 21.
5. **Endpoint generation boundary (§1.5, and sibling `v2_v3_boundary`).** `BoxScoreTraditionalV2` is
   deprecated — "Data is no longer being published for BoxScoreTraditionalV2 as of the 2025-26 NBA season"
   — and `BoxScoreSummaryV2` carries a known-availability warning for games on/after 2025-04-10. A pipeline
   built on V2 works for early seasons and silently returns empty for recent ones. Use V3 throughout; V3 was
   verified working for 2005-06, 2008-09, 2012-13, 2015-16, 2019-20, 2023-24, 2025-26, plus a 2005-06
   playoff game and a 2023-24 play-in game (`python probe_headers_hist.py`).
6. **`LeagueGameFinder` cap-and-truncate (§4.3).** HTTP 200 with the oldest seasons missing is a silent
   correctness failure that would surface late, as a data-completeness bug, after the model is trained.
7. **Kaggle is a captcha wall behind HTTP 200 (§1.1).** Any fallback plan that counts Kaggle as a mirror is
   counting a page that will not return data.
8. **Environment coupling (§2.4).** No repo venv; all deps live in Hermes's venv. A `certifi` or `pandas`
   bump mid-project changes behaviour of a verified-good pipeline, and the ingest is not reproducible for a
   reviewer. Low probability, high blast radius — pin the env before the long ingest, not after.
9. **Single 93 %-full drive (§3).** 8.5 GB peak is only 11.7 % of free, so this is a tail risk, not a
   planning blocker — but the drive is shared and other agents are writing to the same repo. Enforce the
   per-season raw pruning in §3 so a leaked cache cannot become a multi-GB surprise.

**Not a risk:** raw network throughput for the core ingest. Scope (b) — logs + box scores + inactives +
play-by-play, 21 seasons — is **≈9.5 h with no sleep and ≈18.7 h at 0.6 s/call**, well inside a 15–20 day
schedule even after allowing for retries and validation.

---

## 6. Repo-state note

`git log --oneline -1` at probe start returned `db631ce`; by probe end it returned
`e653f65 chore(deps): pin nba_api==1.11.4 for the NBA adapter`, and `docs/phase0/` had gained sibling
artefacts (`db-design.md`, `probe_nba_api.py`, `probe-nba-api-raw.json`, `probe-odds-espn.json`,
`probe-odds-sbr.json`, `_tmp_espn_sweep.json`). **Other agents are working in this repo concurrently** —
they are the source cited for the `LeagueGameFinder` 30,000-row cap, the era inactive-row samples and the
corpus of throttle timings in §3–§4. This file only adds to `docs/phase0/`; nothing else was modified, and
no commit was made by this probe.

## Appendix — the recipe, verbatim

```python
import requests

STATS_HEADERS = {                      # copied from nba_api/stats/library/http.py:10-23
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Fetch-Dest": "empty",
}

_S = requests.Session()                # reuse: ~21 s per new connection on this network

def nba_get(endpoint: str, params: dict, timeout: int = 30):
    return _S.get(f"https://stats.nba.com/stats/{endpoint}",
                  params=sorted(params.items(), key=lambda kv: kv[0]),
                  headers=STATS_HEADERS, timeout=timeout)

# smoke-test this BEFORE any batch: expect 200 in well under 1 s.
r = nba_get("boxscoretraditionalv3",
            {"GameID": "0022400001", "EndPeriod": 0, "EndRange": 0,
             "RangeType": 0, "StartPeriod": 0, "StartRange": 0})
assert r.status_code == 200, r.status_code            # a ~20 s stall here means Sec-* headers are missing
```
