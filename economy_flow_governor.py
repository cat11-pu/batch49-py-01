"""Per-account mint and sink quotas with daily settlement."""


class FlowError(Exception):
    """Error raised by the flow governor."""

    def __init__(self, code, detail=""):
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    def __str__(self):
        if self.detail:
            return "%s: %s" % (self.code, self.detail)
        return self.code


class EconomyFlowGovernor:
    """Tracks coin sources and sinks against daily quotas."""

    def __init__(self, *, source_quotas=None, sink_quotas=None,
                 carry_cap=500):
        self.source_quotas = dict(source_quotas or {})
        self.sink_quotas = dict(sink_quotas or {})
        self.carry_cap = carry_cap

    def record_source(self, account, category, amount, day):
        raise NotImplementedError("record_source")

    def record_sink(self, account, category, amount, day):
        raise NotImplementedError("record_sink")

    def quota_for(self, account, category, day):
        raise NotImplementedError("quota_for")

    def settle_day(self, day):
        raise NotImplementedError("settle_day")

    def balance(self, account):
        raise NotImplementedError("balance")

    def audit(self, account):
        raise NotImplementedError("audit")

    def configure_category(self, category, *, kind, daily_quota,
                           carry_cap=None):
        raise NotImplementedError("configure_category")

    def pending_for(self, account, category):
        raise NotImplementedError("pending_for")

    def snapshot(self):
        raise NotImplementedError("snapshot")

    def restore(self, snapshot):
        raise NotImplementedError("restore")

    def current_day(self):
        raise NotImplementedError("current_day")
