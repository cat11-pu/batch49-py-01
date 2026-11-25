"""Bridge between SPDX style and CycloneDX style SBOM documents."""

from __future__ import annotations

from dataclasses import dataclass, field


class SbomBridgeError(ValueError):
    """Raised when a document cannot be parsed or converted."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    supplier: str | None = None
    license_expression: str | None = None
    external_refs: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Relationship:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class SbomDoc:
    format: str
    name: str
    components: tuple = ()
    relationships: tuple = ()
    document_properties: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ConversionReport:
    source_format: str
    target_format: str
    preserved: tuple = ()
    warnings: tuple = ()


class SbomFormatBridge:
    """Convert component, relationship and license data between SBOM shapes."""

    def __init__(self, *, max_components: int = 500) -> None:
        self.max_components = max_components

    def parse_spdx(self, payload) -> SbomDoc:
        raise NotImplementedError

    def parse_cyclonedx(self, payload) -> SbomDoc:
        raise NotImplementedError

    def to_spdx(self, doc) -> dict:
        raise NotImplementedError

    def to_cyclonedx(self, doc) -> dict:
        raise NotImplementedError

    def convert(self, doc, target) -> tuple:
        raise NotImplementedError

    def roundtrip(self, payload, source_format) -> dict:
        raise NotImplementedError

    def validate(self, payload, format) -> list:
        raise NotImplementedError

    def target_formats(self) -> list:
        raise NotImplementedError

    def component_index(self, doc) -> dict:
        raise NotImplementedError

    def summary(self, doc) -> dict:
        raise NotImplementedError

    def supported_relationship_kinds(self) -> list:
        raise NotImplementedError

    def to_json(self, payload) -> str:
        raise NotImplementedError
