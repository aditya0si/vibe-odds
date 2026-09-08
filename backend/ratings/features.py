"""H2H records, rolling form, and serve-stat features from chronological matches."""

from __future__ import annotations

from collections import defaultdict, deque

from backend.features.score import parse_score


def _pct(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return num / den


class HeadToHead:
    def __init__(self):
        self.overall: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        self.surface: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])

    @staticmethod
    def _key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    def lookup(self, a: str, b: str, surface: str | None = None) -> dict:
        k = self._key(a, b)
        o = self.overall.get(k, [0, 0])
        wa, la = (o[0], o[1]) if a == k[0] else (o[1], o[0])
        out = {"w_a": wa, "w_b": la, "diff": wa - la, "total": wa + la}
        if surface:
            s = self.surface.get((k[0], k[1], surface), [0, 0])
            sa, sb = (s[0], s[1]) if a == k[0] else (s[1], s[0])
            out.update({"surf_w_a": sa, "surf_w_b": sb, "surf_diff": sa - sb})
        return out

    def record(self, winner: str, loser: str, surface: str, walkover: bool = False) -> None:
        if walkover:
            return
        k = self._key(winner, loser)
        if winner == k[0]:
            self.overall[k][0] += 1
            self.surface[(k[0], k[1], surface)][0] += 1
        else:
            self.overall[k][1] += 1
            self.surface[(k[0], k[1], surface)][1] += 1

    def snapshot(self) -> dict:
        return {"overall": {k: list(v) for k, v in self.overall.items()},
                "surface": {k: list(v) for k, v in self.surface.items()}}

    def restore(self, snap: dict) -> None:
        from collections import defaultdict as _dd
        self.overall = _dd(lambda: [0, 0], {tuple(k): list(v) for k, v in snap.get("overall", {}).items()})
        self.surface = _dd(lambda: [0, 0], {tuple(k): list(v) for k, v in snap.get("surface", {}).items()})


class FormTracker:
    """Last-N win rates, rest days, and rolling serve percentages per player."""

    def __init__(self, window: int = 10):
        self.window = window
        self.results: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self.last_date: dict[str, int] = {}
        self.serve: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        self.rank: dict[str, float] = {}
        self.age: dict[str, float] = {}
        self.played: dict[str, int] = defaultdict(int)
        self.load: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))  # (ord, minutes, best_of, sets)
        self.tb: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))  # 1/0 per tiebreak

    def features(self, name: str, date: int) -> dict:
        res = list(self.results.get(name, []))
        out = {
            "form_n": len(res),
            "win_rate_10": (sum(res) / len(res)) if res else 0.5,
            "rest_days": self._rest(name, date),
            "rank": self.rank.get(name),
            "age": self.age.get(name),
            "career_n": self.played.get(name, 0),
        }
        out.update(self._load_features(name, date))
        tbs = list(self.tb.get(name, []))
        out["tb_rate"] = (sum(tbs) / len(tbs)) if tbs else 0.5
        out["tb_n"] = len(tbs)
        sv = list(self.serve.get(name, []))
        if sv:
            n = len(sv)
            out.update({
                "ace_rate": sum(s["ace"] for s in sv) / max(1, sum(s["svpt"] for s in sv)),
                "df_rate": sum(s["df"] for s in sv) / max(1, sum(s["svpt"] for s in sv)),
                "first_in": sum(s["in1"] for s in sv) / max(1, sum(s["svpt"] for s in sv)),
                "first_won": sum(s["w1"] for s in sv) / max(1, sum(s["in1"] for s in sv)),
                "second_won": sum(s["w2"] for s in sv) / max(1, sum(s["svpt"] - s["in1"] for s in sv)),
                "bp_saved": sum(s["bps"] for s in sv) / max(1, sum(s["bpf"] for s in sv)),
                "serve_n": n,
            })
        else:
            out.update({"ace_rate": None, "df_rate": None, "first_in": None,
                        "first_won": None, "second_won": None, "bp_saved": None, "serve_n": 0})
        return out

    @staticmethod
    def _to_ord(yyyymmdd: int) -> int | None:
        try:
            from datetime import datetime
            s = f"{int(yyyymmdd):08d}"
            return datetime(int(s[:4]), int(s[4:6]), int(s[6:8])).toordinal()
        except (ValueError, TypeError):
            return None

    def _load_features(self, name: str, date: int) -> dict:
        """Minutes played in last 14d + gruelling-match count (5 setters/deciders)."""
        now = self._to_ord(date)
        mins, hard = 0, 0
        for d_ord, mins_m, sets in self.load.get(name, []):
            if now is not None and now - d_ord > 14:
                continue
            mins += mins_m or 0
            if sets >= 5:
                hard += 1
        return {"minutes_14d": mins, "grind_10": hard}

    def _rest(self, name: str, date: int) -> int | None:
        last = self.last_date.get(name)
        if last is None:
            return None
        a, b = self._to_ord(date), self._to_ord(last)
        if a is None or b is None:
            return None
        return max(0, a - b)

    def record(self, match: dict) -> None:
        if match.get("walkover"):
            return
        w, l, d = match["winner"], match["loser"], match["date"]
        self.results[w].append(1)
        self.results[l].append(0)
        self.last_date[w] = d
        self.last_date[l] = d
        self.played[w] += 1
        self.played[l] += 1
        if match.get("w_rank"):
            self.rank[w] = match["w_rank"]
        if match.get("l_rank"):
            self.rank[l] = match["l_rank"]
        if match.get("w_age"):
            self.age[w] = match["w_age"]
        if match.get("l_age"):
            self.age[l] = match["l_age"]
        d_ord = self._to_ord(d)
        struct = parse_score(match.get("score", "")) or {}
        if d_ord is not None:
            sets = struct.get("sets_w", 0) + struct.get("sets_l", 0)
            mins = match.get("minutes") or 0
            self.load[w].append((d_ord, mins, sets))
            self.load[l].append((d_ord, mins, sets))
        for _ in range(struct.get("tb_w", 0)):
            self.tb[w].append(1)
            self.tb[l].append(0)
        for _ in range(struct.get("tb_l", 0)):
            self.tb[w].append(0)
            self.tb[l].append(1)
        for side, stats in (("w_stats", match["w_stats"]), ("l_stats", match["l_stats"])):
            name = w if side == "w_stats" else l
            svpt = stats.get("svpt")
            if svpt:
                self.serve[name].append({
                    "ace": stats.get("ace") or 0, "df": stats.get("df") or 0,
                    "svpt": svpt, "in1": stats.get("1stIn") or 0,
                    "w1": stats.get("1stWon") or 0, "w2": stats.get("2ndWon") or 0,
                    "bps": stats.get("bpSaved") or 0, "bpf": stats.get("bpFaced") or 0,
                })

    def snapshot(self) -> dict:
        return {"window": self.window,
                "results": {k: list(v) for k, v in self.results.items()},
                "last_date": dict(self.last_date),
                "serve": {k: list(v) for k, v in self.serve.items()},
                "rank": dict(self.rank), "age": dict(self.age),
                "played": dict(self.played),
                "load": {k: list(v) for k, v in self.load.items()},
                "tb": {k: list(v) for k, v in self.tb.items()}}

    def restore(self, snap: dict) -> None:
        from collections import defaultdict as _dd, deque as _dq
        self.window = snap.get("window", self.window)
        self.results = _dd(lambda: _dq(maxlen=self.window),
                           {k: _dq(v, maxlen=self.window) for k, v in snap.get("results", {}).items()})
        self.last_date = dict(snap.get("last_date", {}))
        self.serve = _dd(lambda: _dq(maxlen=20),
                         {k: _dq(v, maxlen=20) for k, v in snap.get("serve", {}).items()})
        self.rank = dict(snap.get("rank", {}))
        self.age = dict(snap.get("age", {}))
        self.played = _dd(int, snap.get("played", {}))
        self.load = _dd(lambda: _dq(maxlen=10),
                        {k: _dq(v, maxlen=10) for k, v in snap.get("load", {}).items()})
        self.tb = _dd(lambda: _dq(maxlen=20),
                      {k: _dq(v, maxlen=20) for k, v in snap.get("tb", {}).items()})
