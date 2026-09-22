"""Tennis sport groups for The Odds API (map step 7)."""

SPORT_GROUPS = {
    # Mens singles only — no WTA, no doubles/mixed. US Open first.
    "tennis": ["tennis_atp_singles"],
    "usopen": ["tennis_atp_us_open"],
}
DEFAULT_GROUP = "usopen"
