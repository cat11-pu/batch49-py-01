"""Signed rollback bundles: per-batch verification, isolation and idempotent replay."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import threading

_SNAPSHOT_VERSION = 1
_STAT_KEYS = {"requests", "batches", "executed", "undone", "retries",
              "quarantined"}
_BATCH_STATES = {"trusted", "quarantined", "failed", "done"}
_REQUEST_STATES = {"completed", "partial", "failed"}
_EVENT_TYPES = {"submit", "undo", "done", "fail"}


class RollbackError(ValueError):
    """Raised when a rollback submission or execution is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _is_int(value) -> bool:
    return type(value) is int


def _signature_text(batch_id: str, assets: list) -> str:
    body = {"batch_id": batch_id, "assets": assets}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


class RollbackBundleGuard:
    """Execute signed rollback batches in order, isolating untrusted ones."""

    def __init__(self, undo, *, max_batches: int = 8,
                 max_assets_per_batch: int = 32,
                 max_attempts: int = 2) -> None:
        self._undo = undo
        self.max_batches = max_batches
        self.max_assets_per_batch = max_assets_per_batch
        self.max_attempts = max_attempts
        self._lock = threading.RLock()
        self._keys: dict[str, bytes] = {}
        self._requests: dict[str, dict] = {}
        self._settled: dict[str, list[int]] = {}
        self._history: list[dict] = []
        self._stats = {key: 0 for key in _STAT_KEYS}

    @staticmethod
    def _material_bytes(material) -> bytes:
        if isinstance(material, str):
            return material.encode("utf-8")
        if isinstance(material, bytes):
            return bytes(material)
        if isinstance(material, bytearray):
            return bytes(material)
        raise TypeError("key material must be str or bytes")

    def trust_key(self, key_id: str, material: str) -> None:
        if not isinstance(key_id, str) or not key_id:
            raise RollbackError("ROLLBACK_KEY_INVALID",
                                "key_id must be a string")
        try:
            encoded = self._material_bytes(material)
        except TypeError as exc:
            raise RollbackError("ROLLBACK_KEY_INVALID", str(exc)) from exc
        with self._lock:
            self._keys[key_id] = encoded

    def untrust_key(self, key_id: str) -> int:
        with self._lock:
            return 1 if self._keys.pop(key_id, None) is not None else 0

    def _batch_structure_error(self, batch) -> str | None:
        if not isinstance(batch, dict):
            return "batch must be a dictionary"
        for field in ("batch_id", "key_id", "assets", "signature"):
            if field not in batch:
                return f"batch is missing {field}"

        if not isinstance(batch["batch_id"], str) or not batch["batch_id"]:
            return "batch_id must be a non-empty string"
        if not isinstance(batch["key_id"], str) or not batch["key_id"]:
            return "key_id must be a non-empty string"
        if not isinstance(batch["signature"], str) or not batch["signature"]:
            return "signature must be a non-empty string"

        assets = batch["assets"]
        if not isinstance(assets, list):
            return "assets must be a list"
        if not assets:
            return "assets must not be empty"
        if len(assets) > self.max_assets_per_batch:
            return "too many assets in batch"

        seen_assets = set()
        for item in assets:
            if not isinstance(item, dict):
                return "asset must be a dictionary"
            for field in ("asset_id", "from_version", "to_version"):
                if field not in item:
                    return f"asset is missing {field}"
            asset_id = item["asset_id"]
            if not isinstance(asset_id, str) or not asset_id:
                return "asset_id must be a non-empty string"
            if asset_id in seen_assets:
                return f"duplicate asset_id {asset_id} in batch"
            seen_assets.add(asset_id)
            if not _is_int(item["from_version"]):
                return "from_version must be an integer"
            if not _is_int(item["to_version"]):
                return "to_version must be an integer"
            if item["from_version"] == item["to_version"]:
                return "from_version and to_version must differ"
        return None

    def _bundle_structure_error(self, bundle) -> str | None:
        if not isinstance(bundle, dict):
            return "bundle must be a dictionary"
        if "bundle_id" not in bundle or "batches" not in bundle:
            return "bundle must contain bundle_id and batches"
        if not isinstance(bundle["bundle_id"], str) or not bundle["bundle_id"]:
            return "bundle_id must be a non-empty string"
        batches = bundle["batches"]
        if not isinstance(batches, list):
            return "batches must be a list"
        if not batches:
            return "batches must not be empty"
        if len(batches) > self.max_batches:
            return "too many batches"

        seen_batches = set()
        for batch in batches:
            error = self._batch_structure_error(batch)
            if error is not None:
                return error
            batch_id = batch["batch_id"]
            if batch_id in seen_batches:
                return f"duplicate batch_id {batch_id}"
            seen_batches.add(batch_id)
        return None

    def _verify_batch_unlocked(self, batch) -> dict:
        error = self._batch_structure_error(batch)
        if error is not None:
            batch_id = batch.get("batch_id") if isinstance(batch, dict) else None
            return {"batch_id": batch_id, "status": "quarantined",
                    "code": "ROLLBACK_BATCH_INVALID", "detail": error}

        batch_id = batch["batch_id"]
        material = self._keys.get(batch["key_id"])
        if material is None:
            return {"batch_id": batch_id, "status": "quarantined",
                    "code": "ROLLBACK_KEY_UNKNOWN",
                    "detail": f"key_id {batch['key_id']} is not trusted"}

        try:
            text = _signature_text(batch_id, batch["assets"])
            expected = hmac.new(material, text.encode("utf-8"),
                                hashlib.sha256).hexdigest()
        except (TypeError, ValueError) as exc:
            return {"batch_id": batch_id, "status": "quarantined",
                    "code": "ROLLBACK_BATCH_INVALID", "detail": str(exc)}

        try:
            matches = hmac.compare_digest(expected, batch["signature"])
        except (TypeError, ValueError):
            matches = False
        if not matches:
            return {"batch_id": batch_id, "status": "quarantined",
                    "code": "ROLLBACK_SIGNATURE_INVALID",
                    "detail": "batch signature does not match"}
        return {"batch_id": batch_id, "status": "ok", "code": None,
                "detail": None}

    def verify_batch(self, batch) -> dict:
        with self._lock:
            return self._verify_batch_unlocked(batch)

    def _is_settled(self, asset: dict) -> bool:
        return asset["from_version"] in self._settled.get(asset["asset_id"], ())

    def _settle(self, asset: dict) -> None:
        versions = self._settled.setdefault(asset["asset_id"], [])
        if asset["from_version"] not in versions:
            versions.append(asset["from_version"])

    def _event(self, event_type: str, request_id: str, now: int,
               **fields) -> None:
        event = {"type": event_type, "request_id": request_id, "at": now}
        event.update(fields)
        self._history.append(event)

    def submit(self, bundle, *, request_id: str, now: int = 0) -> dict:
        with self._lock:
            error = self._bundle_structure_error(bundle)
            if error is not None:
                raise RollbackError("ROLLBACK_BUNDLE_INVALID", error)
            try:
                fingerprint = _canonical_json(bundle)
            except (TypeError, ValueError) as exc:
                raise RollbackError(
                    "ROLLBACK_BUNDLE_INVALID", str(exc)) from exc

            existing = self._requests.get(request_id)
            if existing is not None:
                if fingerprint != existing["fingerprint"]:
                    raise RollbackError(
                        "ROLLBACK_REQUEST_CONFLICT",
                        f"request {request_id} was submitted with another bundle")
                return copy.deepcopy(existing["submit_result"])

            batch_records = []
            quarantined = []
            seen_assets = set()
            accepted = 0

            for batch in bundle["batches"]:
                assets = copy.deepcopy(batch["assets"])
                record = {
                    "batch_id": batch["batch_id"],
                    "key_id": batch["key_id"],
                    "assets": assets,
                    "signature": batch["signature"],
                    "status": "trusted",
                    "code": None,
                    "detail": None,
                    "attempts": 0,
                    "done": [],
                    "failed_index": None,
                }

                verdict = self._verify_batch_unlocked(batch)
                if verdict["status"] != "ok":
                    record["status"] = "quarantined"
                    record["code"] = verdict["code"]
                    record["detail"] = verdict["detail"]
                elif any(asset["asset_id"] in seen_assets for asset in assets):
                    record["status"] = "quarantined"
                    record["code"] = "ROLLBACK_ASSET_DUPLICATE"
                    record["detail"] = "asset already appears in an earlier batch"
                else:
                    settled_asset = next(
                        (asset for asset in assets if self._is_settled(asset)),
                        None,
                    )
                    if settled_asset is not None:
                        record["status"] = "quarantined"
                        record["code"] = "ROLLBACK_ASSET_SETTLED"
                        record["detail"] = (
                            f"{settled_asset['asset_id']} was already rolled "
                            f"back from version {settled_asset['from_version']}")
                    else:
                        accepted += 1

                seen_assets.update(asset["asset_id"] for asset in assets)
                if record["status"] == "quarantined":
                    quarantined.append({
                        "batch_id": record["batch_id"],
                        "code": record["code"],
                    })
                batch_records.append(record)

            submit_result = {
                "bundle_id": bundle["bundle_id"],
                "accepted": accepted,
                "quarantined": tuple(quarantined),
                "total_batches": len(batch_records),
            }
            self._requests[request_id] = {
                "bundle_id": bundle["bundle_id"],
                "fingerprint": fingerprint,
                "batches": batch_records,
                "submit_result": copy.deepcopy(submit_result),
                "executed": False,
                "result": None,
            }
            self._stats["requests"] += 1
            self._stats["batches"] += len(batch_records)
            self._stats["quarantined"] += len(quarantined)
            self._event(
                "submit", request_id, now,
                bundle_id=bundle["bundle_id"],
                accepted=accepted,
                quarantined=tuple(copy.deepcopy(quarantined)),
            )
            return copy.deepcopy(submit_result)

    def _derive_request_status(self, record: dict) -> str:
        batches = record["batches"]
        if any(batch["status"] == "quarantined" for batch in batches):
            return "partial"
        if batches and all(batch["status"] == "done" for batch in batches):
            return "completed"
        if batches and all(batch["status"] == "failed" for batch in batches):
            return "failed"
        if any(batch["status"] in {"done", "failed"} for batch in batches):
            return "partial"
        return "pending"

    def _build_execute_result(self, record: dict) -> dict:
        done = []
        failed = []
        quarantined = []
        undone = []
        for batch in record["batches"]:
            undone.extend(batch["done"])
            if batch["status"] == "done":
                done.append(batch["batch_id"])
            elif batch["status"] == "failed":
                index = batch["failed_index"]
                asset_id = (batch["assets"][index]["asset_id"]
                            if index is not None else None)
                failed.append({
                    "batch_id": batch["batch_id"],
                    "code": batch["code"] or "ROLLBACK_UNDO_FAILED",
                    "asset_id": asset_id,
                })
            elif batch["status"] == "quarantined":
                quarantined.append({
                    "batch_id": batch["batch_id"],
                    "code": batch["code"],
                })
        return {
            "status": self._derive_request_status(record),
            "done": tuple(done),
            "failed": tuple(failed),
            "quarantined": tuple(quarantined),
            "undone": tuple(undone),
        }

    def _quarantine_batch(self, record: dict, code: str, detail: str,
                          request_id: str, now: int) -> None:
        record["status"] = "quarantined"
        record["code"] = code
        record["detail"] = detail
        self._stats["quarantined"] += 1
        self._event("fail", request_id, now, batch_id=record["batch_id"],
                    asset_id=None, code=code)

    def _run_batch(self, request_id: str, record: dict, now: int) -> bool:
        record["attempts"] += 1
        self._stats["executed"] += 1
        start = record["failed_index"] or 0
        assets = record["assets"]

        for index in range(start, len(assets)):
            asset = assets[index]
            succeeded = False
            error_detail = None
            try:
                succeeded = self._undo(
                    asset["asset_id"], asset["from_version"],
                    asset["to_version"]) is True
                if not succeeded:
                    error_detail = "undo did not return True"
            except Exception as exc:
                error_detail = str(exc)

            self._event(
                "undo", request_id, now,
                batch_id=record["batch_id"],
                asset_id=asset["asset_id"],
                from_version=asset["from_version"],
                to_version=asset["to_version"],
                success=succeeded,
            )
            if succeeded:
                record["done"].append(asset["asset_id"])
                self._settle(asset)
                self._stats["undone"] += 1
                continue

            record["status"] = "failed"
            record["code"] = "ROLLBACK_UNDO_FAILED"
            record["detail"] = error_detail
            record["failed_index"] = index
            self._event("fail", request_id, now,
                        batch_id=record["batch_id"],
                        asset_id=asset["asset_id"],
                        code="ROLLBACK_UNDO_FAILED")
            return False

        record["status"] = "done"
        record["code"] = None
        record["detail"] = None
        record["failed_index"] = None
        self._event("done", request_id, now, batch_id=record["batch_id"])
        return True

    def execute(self, request_id: str, *, now: int = 0) -> dict:
        with self._lock:
            record = self._requests.get(request_id)
            if record is None:
                raise RollbackError(
                    "ROLLBACK_REQUEST_UNKNOWN",
                    f"request {request_id} is not known")
            if record["result"] is not None:
                return copy.deepcopy(record["result"])

            for batch in record["batches"]:
                if batch["status"] != "trusted":
                    continue
                settled_asset = next(
                    (asset for asset in batch["assets"]
                     if self._is_settled(asset)),
                    None,
                )
                if settled_asset is not None:
                    self._quarantine_batch(
                        batch, "ROLLBACK_ASSET_SETTLED",
                        f"{settled_asset['asset_id']} was already rolled back "
                        f"from version {settled_asset['from_version']}",
                        request_id, now)
                    continue
                self._run_batch(request_id, batch, now)

            record["executed"] = True
            result = self._build_execute_result(record)
            record["result"] = copy.deepcopy(result)
            return copy.deepcopy(result)

    def retry_batch(self, request_id: str, batch_id: str, *,
                    now: int = 0) -> dict:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise RollbackError(
                    "ROLLBACK_REQUEST_UNKNOWN",
                    f"request {request_id} is not known")
            batch = next(
                (item for item in request["batches"]
                 if item["batch_id"] == batch_id),
                None,
            )
            if batch is None:
                raise RollbackError(
                    "ROLLBACK_BATCH_UNKNOWN",
                    f"batch {batch_id} is not known in request {request_id}")
            if batch["status"] != "failed":
                raise RollbackError(
                    "ROLLBACK_BATCH_NOT_FAILED",
                    f"batch {batch_id} is {batch['status']}, not failed")
            if batch["attempts"] >= self.max_attempts:
                raise RollbackError(
                    "ROLLBACK_ATTEMPTS_EXCEEDED",
                    f"batch {batch_id} has used {batch['attempts']} attempts")

            verdict = self._verify_batch_unlocked(batch)
            if verdict["status"] != "ok":
                self._quarantine_batch(
                    batch, verdict["code"], verdict["detail"],
                    request_id, now)
                request["result"] = None
                raise RollbackError(
                    "ROLLBACK_BATCH_QUARANTINED",
                    {"batch_id": batch_id, "code": verdict["code"]})

            start = batch["failed_index"] or 0
            settled_asset = next(
                (asset for asset in batch["assets"][start:]
                 if self._is_settled(asset)),
                None,
            )
            if settled_asset is not None:
                code = "ROLLBACK_ASSET_SETTLED"
                detail = (
                    f"{settled_asset['asset_id']} was already rolled back "
                    f"from version {settled_asset['from_version']}")
                self._quarantine_batch(batch, code, detail, request_id, now)
                request["result"] = None
                raise RollbackError(
                    "ROLLBACK_BATCH_QUARANTINED",
                    {"batch_id": batch_id, "code": code})

            before = len(batch["done"])
            self._stats["retries"] += 1
            self._run_batch(request_id, batch, now)
            request["result"] = None
            local_undone = tuple(batch["done"][before:])
            retry_result = {
                "batch_id": batch_id,
                "status": batch["status"],
                "code": batch["code"],
                "attempts": batch["attempts"],
                "undone": local_undone,
            }
            if batch["status"] == "failed":
                index = batch["failed_index"]
                retry_result["asset_id"] = (
                    batch["assets"][index]["asset_id"]
                    if index is not None else None)
            return retry_result

    def bundle_status(self, request_id: str) -> dict:
        with self._lock:
            record = self._requests.get(request_id)
            if record is None:
                raise RollbackError(
                    "ROLLBACK_REQUEST_UNKNOWN",
                    f"request {request_id} is not known")

            if record["executed"]:
                status = self._derive_request_status(record)
            else:
                status = "pending"
            undone = []
            batches = []
            for batch in record["batches"]:
                undone.extend(batch["done"])
                batches.append({
                    "batch_id": batch["batch_id"],
                    "status": batch["status"],
                    "code": batch["code"],
                    "attempts": batch["attempts"],
                    "done": tuple(batch["done"]),
                })
            return {
                "request_id": request_id,
                "bundle_id": record["bundle_id"],
                "status": status,
                "executed": record["executed"],
                "undone": tuple(undone),
                "batches": batches,
            }

    def history(self, request_id: str | None = None) -> tuple:
        with self._lock:
            if request_id is None:
                return copy.deepcopy(tuple(self._history))
            return copy.deepcopy(tuple(
                event for event in self._history
                if event.get("request_id") == request_id))

    def stats(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._stats)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy({
                "version": _SNAPSHOT_VERSION,
                "keys": self._keys,
                "requests": self._requests,
                "settled": self._settled,
                "history": self._history,
                "stats": self._stats,
            })

    @staticmethod
    def _snapshot_error(detail: str) -> RollbackError:
        return RollbackError("ROLLBACK_SNAPSHOT_INVALID", detail)

    def _normalise_result(self, result) -> dict:
        if not isinstance(result, dict):
            raise self._snapshot_error("request result must be a dictionary")
        if result.get("status") not in _REQUEST_STATES:
            raise self._snapshot_error("request result has an invalid status")
        for field in ("done", "failed", "quarantined", "undone"):
            if not isinstance(result.get(field), (list, tuple)):
                raise self._snapshot_error(
                    f"request result {field} must be a sequence")
        for row in result["failed"]:
            if (not isinstance(row, dict)
                    or not isinstance(row.get("batch_id"), str)
                    or not isinstance(row.get("code"), str)
                    or "asset_id" not in row):
                raise self._snapshot_error("invalid failed result row")
        for row in result["quarantined"]:
            if (not isinstance(row, dict)
                    or not isinstance(row.get("batch_id"), str)
                    or not isinstance(row.get("code"), str)):
                raise self._snapshot_error("invalid quarantined result row")
        if not all(isinstance(item, str) for item in result["done"]):
            raise self._snapshot_error("invalid done result")
        if not all(isinstance(item, str) for item in result["undone"]):
            raise self._snapshot_error("invalid undone result")
        return copy.deepcopy(result)

    def _normalise_submit_result(self, result) -> dict:
        if not isinstance(result, dict):
            raise self._snapshot_error("submit result must be a dictionary")
        if not isinstance(result.get("bundle_id"), str):
            raise self._snapshot_error("submit result bundle_id is invalid")
        if not _is_int(result.get("accepted")) or result["accepted"] < 0:
            raise self._snapshot_error("submit result accepted is invalid")
        if (not _is_int(result.get("total_batches"))
                or result["total_batches"] < 0):
            raise self._snapshot_error("submit result total_batches is invalid")
        if not isinstance(result.get("quarantined"), (list, tuple)):
            raise self._snapshot_error("submit result quarantined is invalid")
        for row in result["quarantined"]:
            if (not isinstance(row, dict)
                    or not isinstance(row.get("batch_id"), str)
                    or not isinstance(row.get("code"), str)):
                raise self._snapshot_error("invalid submit quarantine row")
        return copy.deepcopy(result)

    def _normalise_batch(self, batch) -> dict:
        error = self._batch_structure_error(batch)
        if error is not None:
            raise self._snapshot_error(error)
        if batch.get("status") not in _BATCH_STATES:
            raise self._snapshot_error("batch has an invalid status")
        if not _is_int(batch.get("attempts")) or batch["attempts"] < 0:
            raise self._snapshot_error("batch attempts is invalid")
        if batch.get("code") is not None and not isinstance(batch["code"], str):
            raise self._snapshot_error("batch code is invalid")
        if (batch.get("detail") is not None
                and not isinstance(batch["detail"], str)):
            raise self._snapshot_error("batch detail is invalid")
        done = batch.get("done")
        if not isinstance(done, list) or not all(
                isinstance(item, str) for item in done):
            raise self._snapshot_error("batch done is invalid")
        asset_ids = [asset["asset_id"] for asset in batch["assets"]]
        if done != asset_ids[:len(done)]:
            raise self._snapshot_error("batch done is not an asset prefix")

        failed_index = batch.get("failed_index")
        if failed_index is not None and (
                not _is_int(failed_index)
                or failed_index < 0
                or failed_index >= len(asset_ids)):
            raise self._snapshot_error("batch failed_index is invalid")

        status = batch["status"]
        if status == "trusted":
            if batch["attempts"] != 0 or done or failed_index is not None:
                raise self._snapshot_error("trusted batch has execution state")
        elif status == "done":
            if (batch["attempts"] < 1 or done != asset_ids
                    or failed_index is not None or batch["code"] is not None):
                raise self._snapshot_error("done batch is inconsistent")
        elif status == "failed":
            if (batch["attempts"] < 1 or failed_index is None
                    or not batch["code"] or len(done) != failed_index):
                raise self._snapshot_error("failed batch is inconsistent")
        elif status == "quarantined":
            if not batch["code"]:
                raise self._snapshot_error("quarantined batch has no code")
        return copy.deepcopy(batch)

    def _normalise_request(self, request_id, record) -> dict:
        if not isinstance(request_id, str) or not request_id:
            raise self._snapshot_error("request id is invalid")
        if not isinstance(record, dict):
            raise self._snapshot_error("request record must be a dictionary")
        required = {"bundle_id", "fingerprint", "batches", "submit_result",
                    "executed", "result"}
        if not required.issubset(record):
            raise self._snapshot_error("request record is incomplete")
        if not isinstance(record["bundle_id"], str):
            raise self._snapshot_error("request bundle_id is invalid")
        if not isinstance(record["fingerprint"], str):
            raise self._snapshot_error("request fingerprint is invalid")
        if not isinstance(record["batches"], list) or not record["batches"]:
            raise self._snapshot_error("request batches are invalid")
        if type(record["executed"]) is not bool:
            raise self._snapshot_error("request executed flag is invalid")

        batches = [self._normalise_batch(batch)
                   for batch in record["batches"]]
        batch_ids = [batch["batch_id"] for batch in batches]
        if len(set(batch_ids)) != len(batch_ids):
            raise self._snapshot_error("request has duplicate batch ids")
        submit_result = self._normalise_submit_result(record["submit_result"])
        if submit_result["bundle_id"] != record["bundle_id"]:
            raise self._snapshot_error("submit result bundle_id differs")

        result = record["result"]
        if result is not None:
            result = self._normalise_result(result)
        if result is not None and not record["executed"]:
            raise self._snapshot_error("cached result exists before execution")
        return {
            "bundle_id": record["bundle_id"],
            "fingerprint": record["fingerprint"],
            "batches": batches,
            "submit_result": submit_result,
            "executed": record["executed"],
            "result": result,
        }

    def _normalise_snapshot(self, state) -> dict:
        if not isinstance(state, dict):
            raise self._snapshot_error("snapshot must be a dictionary")
        required = {"version", "keys", "requests", "settled", "history",
                    "stats"}
        if not required.issubset(state):
            raise self._snapshot_error("snapshot is incomplete")
        if (not _is_int(state["version"])
                or state["version"] != _SNAPSHOT_VERSION):
            raise self._snapshot_error("unsupported snapshot version")

        if not isinstance(state["keys"], dict):
            raise self._snapshot_error("snapshot keys are invalid")
        keys = {}
        for key_id, material in state["keys"].items():
            if not isinstance(key_id, str) or not key_id:
                raise self._snapshot_error("snapshot key id is invalid")
            try:
                keys[key_id] = self._material_bytes(material)
            except TypeError as exc:
                raise self._snapshot_error(str(exc)) from exc

        if not isinstance(state["requests"], dict):
            raise self._snapshot_error("snapshot requests are invalid")
        requests = {
            request_id: self._normalise_request(request_id, record)
            for request_id, record in state["requests"].items()
        }

        if not isinstance(state["settled"], dict):
            raise self._snapshot_error("snapshot settled is invalid")
        settled = {}
        for asset_id, versions in state["settled"].items():
            if not isinstance(asset_id, str) or not asset_id:
                raise self._snapshot_error("snapshot settled asset is invalid")
            if not isinstance(versions, (list, tuple, set)):
                raise self._snapshot_error(
                    "snapshot settled versions are invalid")
            if not all(_is_int(version) for version in versions):
                raise self._snapshot_error(
                    "snapshot settled version is invalid")
            settled[asset_id] = list(dict.fromkeys(versions))

        if not isinstance(state["history"], list):
            raise self._snapshot_error("snapshot history is invalid")
        for event in state["history"]:
            if (not isinstance(event, dict)
                    or event.get("type") not in _EVENT_TYPES
                    or not isinstance(event.get("request_id"), str)
                    or not _is_int(event.get("at"))):
                raise self._snapshot_error("snapshot history event is invalid")

        if not isinstance(state["stats"], dict):
            raise self._snapshot_error("snapshot stats are invalid")
        if not _STAT_KEYS.issubset(state["stats"]):
            raise self._snapshot_error("snapshot stats are incomplete")
        for key, value in state["stats"].items():
            if not _is_int(value) or value < 0:
                raise self._snapshot_error("snapshot stats value is invalid")

        return {
            "version": _SNAPSHOT_VERSION,
            "keys": keys,
            "requests": requests,
            "settled": settled,
            "history": copy.deepcopy(state["history"]),
            "stats": copy.deepcopy(state["stats"]),
        }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                candidate = copy.deepcopy(state)
                normalised = self._normalise_snapshot(candidate)
            except RollbackError:
                raise
            except Exception as exc:
                raise RollbackError(
                    "ROLLBACK_SNAPSHOT_INVALID", str(exc)) from exc

            self._keys = normalised["keys"]
            self._requests = normalised["requests"]
            self._settled = normalised["settled"]
            self._history = normalised["history"]
            self._stats = normalised["stats"]
