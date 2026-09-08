from backend.gbm.predict import score_row
from backend.policy.bandit import Policy, bucket, reward_of


def _row():
    return {"date": 20250101, "surface": "hard", "best_of": 5, "round": "R128",
            "level_mult": 1.1, "elo_surf_diff": 150.0, "elo_overall_diff": 120.0,
            "elo_fast_diff": 150.0, "h2h_diff": 1, "h2h_total": 3, "surf_h2h_diff": 1, "rank_diff": 5.0,
            "rank_missing": 0, "age_diff": -2.0, "exp_diff": 0.5, "form_n_min": 8, "serve_n_min": 10,
            "serve_missing": 0, "win_rate_10_diff": 0.1, "first_won_diff": 0.03, "second_won_diff": 0.02,
            "s1_shrunk_diff": 0.03, "s2_shrunk_diff": 0.02, "ace_shrunk_diff": 0.01, "df_shrunk_diff": -0.005,
            "bp_saved_diff": 0.05, "ace_rate_diff": 0.01, "df_rate_diff": -0.005,
            "tb_rate_diff": 0.1, "minutes_14d_diff": -30.0, "grind_10_diff": 0,
            "rest_days_diff": 1.0, "markov_p": 0.62, "serve_edge": 0.02, "pt_sample": 3000,
            "markov_p_fast": 0.62, "serve_edge_fast": 0.02, "return_state_diff": 0.01, "state_n_min": 3000}


def test_gbm_scores_and_drivers():
    out = score_row(_row())
    assert 0.0 <= out["p"] <= 1.0
    assert out["p"] > 0.5  # all edges point to a
    assert len(out["drivers"]) == 4
    assert all("feature" in d and "push" in d for d in out["drivers"])


def test_policy_prior_and_learning():
    p = Policy()
    d = p.decide("hard", 0.05, 0.6)
    assert d["action"] == "post"  # prior trusts +EV
    assert p.decide("hard", -0.05, 0.6)["action"] == "pass"
    b = bucket("hard", 0.05, 0.6)
    for _ in range(6):
        p.update(b, "post", -1.0)  # posting loses repeatedly
    assert p.decide("hard", 0.05, 0.6)["action"] == "pass"
    p2 = Policy()
    for _ in range(6):
        p2.update(b, "post", 1.5)
    assert p2.decide("hard", 0.05, 0.6)["action"] == "post"
    assert reward_of("win", 2.5) == 1.5
    assert reward_of("loss", 2.5) == -1.0
    assert reward_of("push", 2.5) == 0.0
