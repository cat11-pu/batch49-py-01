"""Dynamic shop pricing with pressure, elasticity and daily settlement."""


class PriceError(Exception):
    """Error raised by the price index."""

    def __init__(self, code, detail=""):
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    def __str__(self):
        if self.detail:
            return "%s: %s" % (self.code, self.detail)
        return self.code


class DynamicPriceIndex:
    """Quote engine for a shop catalogue."""

    def __init__(self, *, decay_bp=2500, spread_bp=20, min_spread=1,
                 max_impact_units=1000):
        self.decay_bp = decay_bp
        self.spread_bp = spread_bp
        self.min_spread = min_spread
        self.max_impact_units = max_impact_units

    def register_item(self, item_id, base_price, stock):
        raise NotImplementedError("register_item")

    def set_base_price(self, item_id, base_price):
        raise NotImplementedError("set_base_price")

    def set_elasticity(self, item_id, elasticity):
        raise NotImplementedError("set_elasticity")

    def quote(self, item_id):
        raise NotImplementedError("quote")

    def record_trade(self, item_id, side, qty):
        raise NotImplementedError("record_trade")

    def daily_settle(self, day):
        raise NotImplementedError("daily_settle")

    def price_history(self, item_id):
        raise NotImplementedError("price_history")

    def stock_of(self, item_id):
        raise NotImplementedError("stock_of")

    def pressure_of(self, item_id):
        raise NotImplementedError("pressure_of")

    def snapshot(self):
        raise NotImplementedError("snapshot")

    def restore(self, snapshot):
        raise NotImplementedError("restore")
