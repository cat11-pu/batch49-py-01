"""IKEv2 SA generation ledger with a message-id window and duplicate suppression."""

from __future__ import annotations


class IkeLedgerError(ValueError):
    """Raised when a rekey, message or deletion is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class IkeRekeyLedger:
    """Track SA generations, control-message windows and rekey overlap windows."""

    def __init__(self, *, window_size: int = 64, max_sas: int = 32,
                 overlap_seconds: int = 120, max_initiators: int = 16,
                 sa_lifetime: int = 3600) -> None:
        self.window_size = window_size
        self.max_sas = max_sas
        self.overlap_seconds = overlap_seconds
        self.max_initiators = max_initiators
        self.sa_lifetime = sa_lifetime

    def create_sa(self, initiator: str, *, now: int = 0,
                  spi: str | None = None) -> dict:
        raise NotImplementedError

    def receive(self, initiator: str, message_id: int, kind: str,
                payload: dict | None = None, *, now: int = 0) -> dict:
        raise NotImplementedError

    def rekey(self, initiator: str, *, now: int = 0,
              nonce: str | None = None) -> dict:
        raise NotImplementedError

    def delete_sa(self, initiator: str, generation: int, *, now: int = 0) -> bool:
        raise NotImplementedError

    def generations(self, initiator: str) -> list:
        raise NotImplementedError

    def window(self, initiator: str) -> dict:
        raise NotImplementedError

    def state(self, initiator: str) -> dict:
        raise NotImplementedError

    def prune(self, *, now: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
