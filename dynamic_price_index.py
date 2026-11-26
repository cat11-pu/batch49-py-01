"""Dynamic shop pricing with pressure, elasticity and daily settlement."""

import threading


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
        self._items = {}
        self._last_settle_day = None
        self._lock = threading.RLock()

    def _is_int(self, value):
        return isinstance(value, int) and not isinstance(value, bool)

    def _get_item(self, item_id):
        item = self._items.get(item_id)
        if item is None:
            raise PriceError(
                "PRICE_UNKNOWN_ITEM",
                "item %r is not registered" % (item_id,),
            )
        return item

    def _delta(self, pressure, elasticity):
        if pressure >= 0:
            return pressure * elasticity // 100
        return -(abs(pressure) * elasticity // 100)

    def _mid(self, item):
        delta = self._delta(item["pressure"], item["elasticity"])
        return max(1, item["base_price"] + delta)

    def register_item(self, item_id, base_price, stock):
        if (not isinstance(item_id, str) or not item_id
                or not self._is_int(base_price) or base_price < 1
                or not self._is_int(stock) or stock < 0):
            raise PriceError(
                "PRICE_INVALID_REQUEST",
                "invalid item registration for %r" % (item_id,),
            )
        with self._lock:
            if item_id in self._items:
                raise PriceError(
                    "PRICE_DUPLICATE_ITEM",
                    "item %r is already registered" % (item_id,),
                )
            self._items[item_id] = {
                "base_price": base_price,
                "stock": stock,
                "pressure": 0,
                "elasticity": 10,
                "history": [],
            }

    def set_base_price(self, item_id, base_price):
        with self._lock:
            item = self._get_item(item_id)
            if not self._is_int(base_price) or base_price < 1:
                raise PriceError(
                    "PRICE_INVALID_REQUEST",
                    "base_price must be a positive integer",
                )
            item["base_price"] = base_price

    def set_elasticity(self, item_id, elasticity):
        with self._lock:
            item = self._get_item(item_id)
            if not self._is_int(elasticity) or elasticity < 0:
                raise PriceError(
                    "PRICE_INVALID_REQUEST",
                    "elasticity must be a non-negative integer",
                )
            item["elasticity"] = elasticity

    def quote(self, item_id):
        with self._lock:
            item = self._get_item(item_id)
            mid = self._mid(item)
            spread = max(self.min_spread, mid * self.spread_bp // 10000)
            bid = max(1, mid - spread // 2)
            ask = bid + spread
            return {
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread": spread,
                "stock": item["stock"],
            }

    def record_trade(self, item_id, side, qty):
        with self._lock:
            item = self._get_item(item_id)
            if side not in ("buy", "sell") or not self._is_int(qty) or qty < 1:
                raise PriceError(
                    "PRICE_INVALID_REQUEST",
                    "side must be 'buy'/'sell' and qty a positive integer",
                )
            if side == "buy":
                if qty > item["stock"]:
                    raise PriceError(
                        "PRICE_INSUFFICIENT_STOCK",
                        "cannot buy %r units of %r" % (qty, item_id),
                    )
                item["stock"] -= qty
                impact = min(qty, self.max_impact_units)
                item["pressure"] += impact
            else:
                item["stock"] += qty
                impact = min(qty, self.max_impact_units)
                item["pressure"] -= impact
            return {
                "item": item_id,
                "side": side,
                "qty": qty,
                "stock": item["stock"],
                "pressure": item["pressure"],
                "mid": self._mid(item),
            }

    def daily_settle(self, day):
        with self._lock:
            if self._last_settle_day is not None and day <= self._last_settle_day:
                return {
                    "settled": False,
                    "items": 0,
                    "prices": {},
                }
            prices = {}
            for item_id in sorted(self._items):
                item = self._items[item_id]
                pressure = item["pressure"]
                retained_bp = 10000 - self.decay_bp
                if pressure >= 0:
                    pressure = pressure * retained_bp // 10000
                else:
                    pressure = -(abs(pressure) * retained_bp // 10000)
                item["pressure"] = pressure
                mid = self._clamp_mid(item)
                item["history"].append(mid)
                prices[item_id] = mid
            self._last_settle_day = day
            return {
                "settled": True,
                "items": len(prices),
                "prices": prices,
            }

    def _clamp_mid(self, item):
        """Clamp the settled mid to base_price +/- 30%.

        Clamping applies only to the recorded settlement price; the decayed
        pressure is left untouched so quotes keep following the pressure model.
        """
        base = item["base_price"]
        lower = max(1, base - base * 30 // 100)
        upper = base + base * 30 // 100
        mid = self._mid(item)
        if mid < lower:
            mid = lower
        elif mid > upper:
            mid = upper
        return max(1, mid)

    def price_history(self, item_id):
        with self._lock:
            item = self._get_item(item_id)
            return tuple(item["history"])

    def stock_of(self, item_id):
        with self._lock:
            item = self._get_item(item_id)
            return item["stock"]

    def pressure_of(self, item_id):
        with self._lock:
            item = self._get_item(item_id)
            return item["pressure"]

    def snapshot(self):
        with self._lock:
            return {
                "items": {
                    item_id: {
                        "base_price": item["base_price"],
                        "stock": item["stock"],
                        "pressure": item["pressure"],
                        "elasticity": item["elasticity"],
                        "history": list(item["history"]),
                    }
                    for item_id, item in self._items.items()
                },
                "last_settle_day": self._last_settle_day,
            }

    def restore(self, snapshot):
        with self._lock:
            items, last_settle_day = self._validate_snapshot(snapshot)
            self._items = items
            self._last_settle_day = last_settle_day

    def _validate_snapshot(self, snapshot):
        if not isinstance(snapshot, dict):
            raise PriceError("PRICE_SNAPSHOT_INVALID", "snapshot must be a dict")
        raw_items = snapshot.get("items")
        if not isinstance(raw_items, dict):
            raise PriceError(
                "PRICE_SNAPSHOT_INVALID",
                "snapshot 'items' must be a dict",
            )
        restored = {}
        for item_id, raw_item in raw_items.items():
            if not isinstance(item_id, str) or not item_id:
                raise PriceError(
                    "PRICE_SNAPSHOT_INVALID",
                    "snapshot contains an invalid item id",
                )
            if not isinstance(raw_item, dict):
                raise PriceError(
                    "PRICE_SNAPSHOT_INVALID",
                    "snapshot entry for %r must be a dict" % (item_id,),
                )
            base_price = raw_item.get("base_price")
            stock = raw_item.get("stock")
            pressure = raw_item.get("pressure")
            elasticity = raw_item.get("elasticity")
            history = raw_item.get("history")
            if (not self._is_int(base_price) or base_price < 1
                    or not self._is_int(stock) or stock < 0
                    or not self._is_int(pressure)
                    or not self._is_int(elasticity) or elasticity < 0
                    or not isinstance(history, list)
                    or not all(self._is_int(value) for value in history)):
                raise PriceError(
                    "PRICE_SNAPSHOT_INVALID",
                    "snapshot entry for %r has invalid fields" % (item_id,),
                )
            restored[item_id] = {
                "base_price": base_price,
                "stock": stock,
                "pressure": pressure,
                "elasticity": elasticity,
                "history": list(history),
            }
        last_settle_day = snapshot.get("last_settle_day")
        if last_settle_day is not None and not isinstance(last_settle_day, str):
            raise PriceError(
                "PRICE_SNAPSHOT_INVALID",
                "snapshot 'last_settle_day' must be a string or None",
            )
        return restored, last_settle_day
