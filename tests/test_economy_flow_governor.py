import pytest

import economy_flow_governor as efg

DAY1 = "2026-11-01"
DAY2 = "2026-11-02"


def make_gov(**kwargs):
    return efg.EconomyFlowGovernor(**kwargs)


def test_unknown_source_category_rejected():
    gov = make_gov()
    with pytest.raises(efg.FlowError) as err:
        gov.record_source("acc", "raid", 10, DAY1)
    assert err.value.code == "FLOW_UNKNOWN_CATEGORY"


def test_source_within_quota_credits_full_amount():
    gov = make_gov()
    result = gov.record_source("acc", "quest", 400, DAY1)
    assert result["accepted"] == 400
    assert result["pending"] == 0
    assert result["balance"] == 400
    assert result["quota_left"] == 600


def test_quota_for_tracks_daily_usage_per_category():
    gov = make_gov()
    gov.record_source("acc", "quest", 400, DAY1)
    gov.record_source("acc", "drop", 500, DAY1)
    assert gov.quota_for("acc", "quest", DAY1) == 600
    assert gov.quota_for("acc", "drop", DAY1) == 0
    assert gov.quota_for("acc", "quest", DAY2) == 1000


def test_non_positive_amount_rejected():
    gov = make_gov()
    with pytest.raises(efg.FlowError) as err:
        gov.record_source("acc", "quest", 0, DAY1)
    assert err.value.code == "FLOW_INVALID_REQUEST"
    with pytest.raises(efg.FlowError):
        gov.record_source("", "quest", 10, DAY1)


def test_sink_deducts_balance_and_reports_acceptance():
    gov = make_gov()
    gov.record_source("acc", "quest", 400, DAY1)
    result = gov.record_sink("acc", "repair", 100, DAY1)
    assert result["accepted"] == 100
    assert result["rejected"] == 0
    assert result["balance"] == 300


def test_sink_cannot_drive_balance_negative():
    gov = make_gov()
    gov.record_source("acc", "quest", 100, DAY1)
    result = gov.record_sink("acc", "shop", 500, DAY1)
    assert result["accepted"] == 100
    assert result["rejected"] == 400
    assert result["balance"] == 0


def test_unknown_sink_category_rejected():
    gov = make_gov()
    with pytest.raises(efg.FlowError) as err:
        gov.record_sink("acc", "gamble", 10, DAY1)
    assert err.value.code == "FLOW_UNKNOWN_CATEGORY"


def test_recording_on_closed_day_rejected():
    gov = make_gov()
    assert gov.settle_day(DAY1)["settled"] is True
    with pytest.raises(efg.FlowError) as err:
        gov.record_source("acc", "quest", 10, DAY1)
    assert err.value.code == "FLOW_DAY_CLOSED"


def test_settle_same_day_is_idempotent():
    gov = make_gov()
    first = gov.settle_day(DAY1)
    assert first["settled"] is True
    second = gov.settle_day(DAY1)
    assert second["settled"] is False
    assert second["released"] == 0


def test_settle_advances_current_day():
    gov = make_gov()
    assert gov.settle_day(DAY1)["day"] == DAY1
    assert gov.settle_day(DAY2)["settled"] is True
    assert gov.current_day() == DAY2


def test_audit_reports_minted_sunk_and_pending():
    gov = make_gov()
    gov.record_source("acc", "quest", 400, DAY1)
    gov.record_source("acc", "drop", 200, DAY1)
    gov.record_sink("acc", "repair", 100, DAY1)
    assert gov.audit("acc") == {
        "minted": 600,
        "sunk": 100,
        "pending": 0,
    }
    assert gov.balance("acc") == 500


def test_balance_of_unknown_account_is_zero():
    gov = make_gov()
    assert gov.balance("nobody") == 0


def test_configure_category_changes_future_quota_and_validates():
    gov = make_gov()
    result = gov.configure_category("quest", kind="source", daily_quota=1500)
    assert result["category"] == "quest"
    assert result["daily_quota"] == 1500
    assert gov.quota_for("acc", "quest", DAY2) == 1500
    with pytest.raises(efg.FlowError) as err:
        gov.configure_category("quest", kind="both", daily_quota=10)
    assert err.value.code == "FLOW_INVALID_REQUEST"


def test_snapshot_restore_returns_to_captured_ledger():
    gov = make_gov()
    gov.record_source("acc", "quest", 400, DAY1)
    snapshot = gov.snapshot()
    gov.record_source("acc", "drop", 200, DAY1)
    gov.record_sink("acc", "repair", 100, DAY1)
    assert gov.balance("acc") == 500
    gov.restore(snapshot)
    assert gov.balance("acc") == 400
    assert gov.quota_for("acc", "quest", DAY1) == 600
    assert gov.audit("acc") == {"minted": 400, "sunk": 0, "pending": 0}


def test_invalid_snapshot_restore_leaves_ledger_untouched():
    gov = make_gov()
    gov.record_source("acc", "quest", 400, DAY1)
    with pytest.raises(efg.FlowError) as err:
        gov.restore({"accounts": "broken"})
    assert err.value.code == "FLOW_SNAPSHOT_INVALID"
    assert gov.balance("acc") == 400
    assert gov.quota_for("acc", "quest", DAY1) == 600
