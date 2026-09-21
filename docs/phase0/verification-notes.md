# Phase 0 — independent verification notes

Written by the coordinating agent (not the probe subagents): every claim below was re-checked with its own command
from this machine on 2026-09-21/22. Where a claim failed to reproduce, it says so.

| # | Claim under test | My check | Result |
|---|---|---|---|
| 1 | "stats.nba.com needs nba_api's full header set; a partial set stalls ~20 s" | 3 raw `requests.get` variants against `stats.nba.com/stats/scoreboardv3?GameDate=2024-12-15` | **Confirmed, after two failures of my own.** UA+Referer+Accept → ConnectionError @19.4 s. Adding only `Sec-Ch-Ua`/`Sec-Ch-Ua-Mobile`/`Sec-Fetch-Dest` → ConnectionError @19.4 s. **nba_api's full 12-header `STATS_HEADERS` → HTTP 200, 20,170 B in 0.90 s** (`boxscoresummaryv3` also 200, 17,312 B, 0.85 s). So it is a header-*set* problem, not an outage — and my intermediate "not reproduced" claim was wrong. Operationally: use `nba_api`, or copy its complete header dict. |
| 2 | "Pre-game inactives exist for the whole history" | 3 direct `boxscoresummaryv2` calls (my own) | **Confirmed.** 2005-06 `0020501226` → `InactivePlayers=6`, `Officials=3`, 9 sets; 2012-13 `0021201218` → 5/3; 2018-19 `0021801227` → 8/3. (Probe child separately found `v3` covers 2025-26 where `v2` returns empty sets, and 79/80 sampled games have ≥1 inactive row.) |
| 3 | "Free opening moneylines exist" | ESPN core odds for one event per era | **Confirmed, with era-specific mechanics.** 2013-14 (`400488874`, 9 providers) includes the `'Opening'` pseudo-provider **id 23** carrying opening ML/spread/total, while item-level `open`/`close` are empty. 2021-22 (14 providers) has no `open`/`close` and no `Opening` provider. 2024-25 (`401704627`, 2 providers: ESPN BET + live) carries full item-level `open` and `close` objects including `moneyLine`. |
| 4 | "A timestamped quote series exists for the middle seasons" | `/odds/1002/history/0/movement` for 4 seasons | **Confirmed.** 2013-14 → 12 points; 2021-22 → 25; 2019-20 → 25 (first `2019-10-20T05:46Z`, last `2019-10-23T02:15Z`, with `awayOdds`/`homeOdds` = moneyline); 2024-25 → 0 points. Provider 1002 (teamrankings) only. |
| 5 | "SBR is reachable through the reader and carries no opening moneyline" | r.jina.ai proxy, index + 2012-13 season page | **Confirmed.** Index lists 16 season files (`nba-odds-2007-08` … `nba-odds-2022-23`). 2012-13 page = 486,921 B, header `Date\|Rot\|VH\|Team\|1st\|2nd\|3rd\|4th\|Final\|Open\|Close\|ML\|2H`, 2,629 rows; the `ML` column tracks the close, and the `Open`/`Close` columns on the underdog row carry the **total**. |
| 6 | "Kaggle is a captcha wall / the dataset slug was wrong" | not re-checked | **Accepted from the probe child** (it verified via the reader). Treated as unusable without a Kaggle API key. |
| 7 | "538's archive recomputes to Brier 0.2147" | not re-checked | **Accepted from the research doc** (`03-platform-methodologies.md`); recomputation is an A10 benchmark task, not a Phase 0 gate. |

**Not proven by any probe (stated so nobody assumes otherwise):** sustained-rate behaviour beyond ~30 back-to-back
calls; the completeness of `leaguegamefinder` row counts beyond the 8 sampled seasons; whether ESPN's snapshot is
the true close for *every* game (one cross-check against SBR matched, plus post-game winner flags exist in the
payload — good evidence, not proof); ESPN coverage in 2024-25/2025-26 is thin (1-2 books).

**Consequences folded into the design** (`docs/phase0/db-design.md` §4, §8):
single v3 code path for all seasons; season-by-season `leaguegamefinder` (30k row cap, newest-first); session reuse
mandatory; per-call retry with backoff; odds sourced SBR-via-reader (2007-08→2012-13) + ESPN core (2013-14→2025-26);
project-venv created because the repo was resolving dependencies to a shared interpreter.
