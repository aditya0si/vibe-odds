-- NBA database schema (as-of by construction).
--
-- Rule that everything obeys: a feature for game G may read
--   * G's own PRE-GAME state (that game's inactive list, rosters, its own tipoff time), and
--   * other rows only when they are strictly BEFORE G's date (cross-game facts), or
--     before G's tipoff_ts when a real timestamp is known.
-- Never: G's final score (that is the label), in-game events, or anything dated on/after
-- G's date from another game.
--
-- Every table carries asof_ts = the moment the row became knowable (UTC ISO-8601).
-- tipoff_ts is NULL where the league did not publish a start time (older seasons).
--
-- Rebuild: python -m sports.nba.db.build --from-empty

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seasons (
    season            TEXT PRIMARY KEY,        -- '2005-06'
    start_year        INTEGER NOT NULL,
    end_year          INTEGER NOT NULL,
    n_games_known     INTEGER,                 -- 1230, 1229, 990, 1059, 1080, ...
    regime_flags      TEXT,                    -- JSON: covid, empty_arena, rule_break_14s, partial
    asof_ts           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    team_id     INTEGER PRIMARY KEY,
    abbr        TEXT,
    full_name   TEXT,
    city        TEXT,
    nickname    TEXT,
    conference  TEXT,
    division    TEXT,
    asof_ts     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    player_id   INTEGER PRIMARY KEY,
    full_name   TEXT,
    first_name  TEXT,
    last_name   TEXT,
    from_year   INTEGER,
    to_year     INTEGER,
    is_active   INTEGER,
    asof_ts     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    game_id           TEXT PRIMARY KEY,
    season            TEXT NOT NULL REFERENCES seasons(season),
    season_type       TEXT NOT NULL DEFAULT 'regular',   -- regular | playoffs | playin | preseason
    game_date         TEXT NOT NULL,                     -- ISO date, arena-local
    tipoff_ts         TEXT,                              -- ISO UTC when published, else NULL
    home_team_id      INTEGER REFERENCES teams(team_id),
    away_team_id      INTEGER REFERENCES teams(team_id),
    home_score        INTEGER,
    away_score        INTEGER,
    ot_count          INTEGER,
    arena             TEXT,
    attendance        INTEGER,
    is_neutral        INTEGER DEFAULT 0,                 -- Paris/Mexico City/NBA Cup neutral sites
    source            TEXT,                              -- which ingest produced the row
    source_updated_at TEXT,
    asof_ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_season ON games(season, season_type);
CREATE INDEX IF NOT EXISTS idx_games_date   ON games(game_date);

CREATE TABLE IF NOT EXISTS game_traditional (
    game_id     TEXT NOT NULL REFERENCES games(game_id),
    team_id     INTEGER NOT NULL,
    player_id   INTEGER NOT NULL,
    started     INTEGER,
    minutes     REAL,
    pts         INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER, pf INTEGER,
    fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
    plus_minus  REAL,
    asof_ts     TEXT NOT NULL,
    PRIMARY KEY (game_id, player_id)
);

CREATE TABLE IF NOT EXISTS game_advanced (
    game_id     TEXT NOT NULL REFERENCES games(game_id),
    team_id     INTEGER NOT NULL,
    player_id   INTEGER NOT NULL,
    off_rating  REAL, def_rating REAL, net_rating REAL, ts_pct REAL, usg_pct REAL,
    pace        REAL, pie REAL,
    asof_ts     TEXT NOT NULL,
    PRIMARY KEY (game_id, player_id)
);

-- PRE-GAME availability: known before tip-off, and the biggest usable signal.
CREATE TABLE IF NOT EXISTS game_inactives (
    game_id     TEXT NOT NULL REFERENCES games(game_id),
    team_id     INTEGER NOT NULL,
    player_id   INTEGER NOT NULL,
    reason      TEXT,
    asof_ts     TEXT NOT NULL,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_inactives_player ON game_inactives(player_id);

CREATE TABLE IF NOT EXISTS game_officials (
    game_id      TEXT NOT NULL REFERENCES games(game_id),
    official_id  INTEGER NOT NULL,
    first_name   TEXT,
    last_name    TEXT,
    jersey_num   TEXT,
    asof_ts      TEXT NOT NULL,
    PRIMARY KEY (game_id, official_id)
);

CREATE TABLE IF NOT EXISTS rosters (
    player_id   INTEGER NOT NULL,
    team_id     INTEGER NOT NULL,
    from_date   TEXT NOT NULL,
    to_date     TEXT,
    asof_ts     TEXT NOT NULL,
    PRIMARY KEY (player_id, team_id, from_date)
);

CREATE TABLE IF NOT EXISTS transactions (
    txn_id      TEXT PRIMARY KEY,
    txn_date    TEXT NOT NULL,
    player_id   INTEGER,
    from_team_id INTEGER,
    to_team_id  INTEGER,
    txn_type    TEXT,
    detail      TEXT,
    asof_ts     TEXT NOT NULL
);

-- open / mid / close snapshots from every source, raw strings kept for audit.
CREATE TABLE IF NOT EXISTS odds_snapshots (
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    source          TEXT NOT NULL,               -- sbr | espn | the_odds_api
    book            TEXT NOT NULL,
    market          TEXT NOT NULL,               -- moneyline | spread | total
    side            TEXT NOT NULL,               -- home | away | over | under
    price_decimal   REAL,
    price_raw       TEXT,
    line            REAL,                        -- spread/total point value
    captured_at     TEXT,                        -- snapshot time when the source gives one
    snapshot_kind   TEXT NOT NULL,               -- open | mid | close
    asof_ts         TEXT NOT NULL,
    PRIMARY KEY (game_id, source, book, market, side, snapshot_kind)
);
CREATE INDEX IF NOT EXISTS idx_odds_kind ON odds_snapshots(snapshot_kind, source);

-- Team-level line per game, parsed from the cached LeagueGameFinder payloads
-- (FGA/FTA/OREB/TOV are enough to derive possessions, pace, ORtg/DRtg without
-- needing the per-game box-score ingest - which is the slow, rate-limited path).
CREATE TABLE IF NOT EXISTS game_team_stats (
    game_id     TEXT NOT NULL REFERENCES games(game_id),
    team_id     INTEGER NOT NULL,
    is_home     INTEGER NOT NULL,
    minutes     REAL,
    pts INTEGER, fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
    oreb INTEGER, dreb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER,
    pf INTEGER, plus_minus REAL,
    asof_ts     TEXT NOT NULL,
    PRIMARY KEY (game_id, team_id)
);

CREATE TABLE IF NOT EXISTS ratings_daily (
    team_id     INTEGER NOT NULL,
    as_of_date  TEXT NOT NULL,
    rating_kind TEXT NOT NULL,
    value       REAL NOT NULL,
    games_played INTEGER,
    asof_ts     TEXT NOT NULL,
    PRIMARY KEY (team_id, as_of_date, rating_kind)
);

CREATE TABLE IF NOT EXISTS features (
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    feature_version TEXT NOT NULL,
    built_at        TEXT NOT NULL,
    asof_ts         TEXT NOT NULL,
    payload         TEXT NOT NULL,               -- JSON row (parquet mirror for bulk analysis)
    PRIMARY KEY (game_id, feature_version)
);

CREATE TABLE IF NOT EXISTS predictions (
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    arm             TEXT NOT NULL,
    p_home          REAL NOT NULL,
    p_away          REAL NOT NULL,
    logged_at       TEXT NOT NULL,               -- MUST be < games.tipoff_ts (asserted)
    model_version   TEXT NOT NULL,
    feature_version TEXT,
    notes           TEXT,
    PRIMARY KEY (game_id, arm, model_version)
);

CREATE TABLE IF NOT EXISTS results (
    game_id     TEXT PRIMARY KEY REFERENCES games(game_id),
    home_win    INTEGER NOT NULL,
    final_margin INTEGER,
    settled_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_obs (
    entity      TEXT NOT NULL,                   -- team | player
    entity_key  TEXT NOT NULL,
    obs_date    TEXT NOT NULL,
    source      TEXT NOT NULL,                   -- reddit | gdelt | pageviews | trends
    value       REAL,
    extra       TEXT,
    asof_ts     TEXT NOT NULL,
    PRIMARY KEY (entity, entity_key, obs_date, source)
);

INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');
