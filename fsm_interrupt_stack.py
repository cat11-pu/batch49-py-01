"""Interrupt stack layered on top of an AI base state."""


class InterruptError(Exception):
    """Error raised by the interrupt stack."""

    def __init__(self, code, detail=""):
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    def __str__(self):
        if self.detail:
            return "%s: %s" % (self.code, self.detail)
        return self.code


class FsmInterruptStack:
    """Stack of interrupt frames above one long-running base state."""

    def __init__(self, *, max_depth=4, default_cooldown=0):
        self.max_depth = max_depth
        self.default_cooldown = default_cooldown

    def define_state(self, name, *, parent=None, precondition=None):
        raise NotImplementedError("define_state")

    def set_base(self, name):
        raise NotImplementedError("set_base")

    def push_interrupt(self, name, *, source, duration, priority=0,
                       critical=False):
        raise NotImplementedError("push_interrupt")

    def pop_interrupt(self, *, source=None):
        raise NotImplementedError("pop_interrupt")

    def tick(self, dt):
        raise NotImplementedError("tick")

    def current(self):
        raise NotImplementedError("current")

    def stack_depth(self):
        raise NotImplementedError("stack_depth")

    def progress(self):
        raise NotImplementedError("progress")

    def set_cooldown(self, source, seconds):
        raise NotImplementedError("set_cooldown")

    def cooldown_for(self, source):
        raise NotImplementedError("cooldown_for")

    def history(self):
        raise NotImplementedError("history")

    def snapshot(self):
        raise NotImplementedError("snapshot")

    def restore(self, snapshot):
        raise NotImplementedError("restore")
