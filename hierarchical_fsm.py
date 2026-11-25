"""Hierarchical state machine with a per-frame transition queue."""


class FsmError(Exception):
    """Error raised by the state machine."""

    def __init__(self, code, detail=""):
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    def __str__(self):
        if self.detail:
            return "%s: %s" % (self.code, self.detail)
        return self.code


class HierarchicalStateMachine:
    """Parent/child state tree driven by one transition per tick."""

    def __init__(self, *, root="root", max_pending=32):
        self.root = root
        self.max_pending = max_pending

    def define_state(self, name, *, parent=None, priority=0, guard=None):
        raise NotImplementedError("define_state")

    def set_initial(self, name):
        raise NotImplementedError("set_initial")

    def request_transition(self, target, *, priority=0, reason=""):
        raise NotImplementedError("request_transition")

    def tick(self):
        raise NotImplementedError("tick")

    def current_path(self):
        raise NotImplementedError("current_path")

    def current_state(self):
        raise NotImplementedError("current_state")

    def register_hooks(self, state, *, on_enter=None, on_exit=None):
        raise NotImplementedError("register_hooks")

    def pending_count(self):
        raise NotImplementedError("pending_count")

    def history(self):
        raise NotImplementedError("history")

    def stats(self):
        raise NotImplementedError("stats")

    def snapshot(self):
        raise NotImplementedError("snapshot")

    def restore(self, snapshot):
        raise NotImplementedError("restore")
