import pytest

import dynamic_price_index as dpi


def make_index(**kwargs):
    return dpi.DynamicPriceIndex(**kwargs)


def test_duplicate_item_registration_rejected():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    with pytest.raises(dpi.PriceError) as err:
        index.register_item("bread", 1200, 50)
    assert err.value.code == "PRICE_DUPLICATE_ITEM"


def test_invalid_registration_values_rejected():
    index = make_index()
    with pytest.raises(dpi.PriceError) as err:
        index.register_item("bread", 0, 10)
    assert err.value.code == "PRICE_INVALID_REQUEST"
    with pytest.raises(dpi.PriceError):
        index.register_item("bread", 1000, -1)
    with pytest.raises(dpi.PriceError):
        index.register_item("", 1000, 10)


def test_initial_quote_uses_base_price_and_minimum_spread():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    assert index.quote("bread") == {
        "bid": 999,
        "ask": 1001,
        "mid": 1000,
        "spread": 2,
        "stock": 1000,
    }


def test_quote_unknown_item_rejected():
    index = make_index()
    with pytest.raises(dpi.PriceError) as err:
        index.quote("ghost")
    assert err.value.code == "PRICE_UNKNOWN_ITEM"


def test_buy_trade_lifts_pressure_and_mid_price():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    result = index.record_trade("bread", "buy", 200)
    assert result["stock"] == 800
    assert result["pressure"] == 200
    assert result["mid"] == 1020
    quote = index.quote("bread")
    assert quote["mid"] == 1020
    assert quote["bid"] == 1019
    assert quote["ask"] == 1021


def test_sell_trade_adds_stock_and_lowers_mid_price():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    result = index.record_trade("bread", "sell", 500)
    assert result["stock"] == 1500
    assert result["pressure"] == -500
    assert result["mid"] == 950
    assert index.quote("bread")["mid"] == 950


def test_buy_beyond_stock_rejected_without_changes():
    index = make_index()
    index.register_item("bread", 1000, 100)
    with pytest.raises(dpi.PriceError) as err:
        index.record_trade("bread", "buy", 101)
    assert err.value.code == "PRICE_INSUFFICIENT_STOCK"
    assert index.stock_of("bread") == 100
    assert index.pressure_of("bread") == 0


def test_invalid_trade_side_rejected():
    index = make_index()
    index.register_item("bread", 1000, 100)
    with pytest.raises(dpi.PriceError) as err:
        index.record_trade("bread", "hold", 1)
    assert err.value.code == "PRICE_INVALID_REQUEST"


def test_elasticity_changes_mid_price():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    index.record_trade("bread", "buy", 200)
    assert index.quote("bread")["mid"] == 1020
    index.set_elasticity("bread", 25)
    assert index.quote("bread")["mid"] == 1050
    with pytest.raises(dpi.PriceError) as err:
        index.set_elasticity("bread", -1)
    assert err.value.code == "PRICE_INVALID_REQUEST"


def test_repeated_quotes_are_stable_until_a_trade():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    first = index.quote("bread")
    second = index.quote("bread")
    assert first == second
    index.record_trade("bread", "buy", 100)
    assert index.quote("bread") != first
    assert index.quote("bread")["mid"] == 1010


def test_daily_settle_is_idempotent_for_same_day():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    index.record_trade("bread", "buy", 200)
    first = index.daily_settle("2026-11-01")
    assert first["settled"] is True
    assert first["items"] == 1
    assert first["prices"] == {"bread": 1015}
    assert index.price_history("bread") == (1015,)
    second = index.daily_settle("2026-11-01")
    assert second["settled"] is False
    assert index.price_history("bread") == (1015,)


def test_next_day_settle_decays_pressure_and_appends_history():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    index.record_trade("bread", "buy", 200)
    index.daily_settle("2026-11-01")
    result = index.daily_settle("2026-11-02")
    assert result["settled"] is True
    assert index.pressure_of("bread") == 112
    assert index.price_history("bread") == (1015, 1011)


def test_set_base_price_moves_mid_and_validates():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    index.record_trade("bread", "buy", 200)
    index.set_base_price("bread", 1200)
    assert index.quote("bread")["mid"] == 1220
    with pytest.raises(dpi.PriceError) as err:
        index.set_base_price("bread", 0)
    assert err.value.code == "PRICE_INVALID_REQUEST"
    with pytest.raises(dpi.PriceError) as err:
        index.set_base_price("ghost", 100)
    assert err.value.code == "PRICE_UNKNOWN_ITEM"


def test_snapshot_restore_returns_to_captured_market_state():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    index.record_trade("bread", "buy", 200)
    snapshot = index.snapshot()
    index.record_trade("bread", "sell", 100)
    assert index.stock_of("bread") == 900
    index.restore(snapshot)
    assert index.stock_of("bread") == 800
    assert index.pressure_of("bread") == 200
    assert index.quote("bread")["mid"] == 1020
    assert index.price_history("bread") == ()


def test_invalid_snapshot_restore_leaves_market_untouched():
    index = make_index()
    index.register_item("bread", 1000, 1000)
    with pytest.raises(dpi.PriceError) as err:
        index.restore({"items": "broken"})
    assert err.value.code == "PRICE_SNAPSHOT_INVALID"
    assert index.quote("bread")["mid"] == 1000
    assert index.stock_of("bread") == 1000
