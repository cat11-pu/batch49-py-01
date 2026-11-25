import pytest

import sbom_format_bridge as br


def bridge(**kwargs):
    return br.SbomFormatBridge(**kwargs)


def spdx_payload():
    return {
        "spdxVersion": "SPDX-2.3",
        "name": "acme-image",
        "packages": [
            {
                "SPDXID": "SPDXRef-openssl",
                "name": "openssl",
                "versionInfo": "3.0.13",
                "supplier": "Org: Acme",
                "licenseConcluded": "Apache-2.0",
                "externalRefs": [
                    {
                        "referenceType": "purl",
                        "referenceLocator": "pkg:deb/openssl@3.0.13",
                    }
                ],
                "licenseComments": "已核对上游公告",
            },
            {
                "SPDXID": "SPDXRef-zlib",
                "name": "zlib",
                "versionInfo": "1.2.13",
                "supplier": "Org: Zlib",
                "licenseConcluded": "Zlib",
            },
            {
                "SPDXID": "SPDXRef-libxml2",
                "name": "libxml2",
                "versionInfo": "2.9.14",
                "licenseConcluded": "MIT",
            },
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-openssl",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": "SPDXRef-zlib",
            },
            {
                "spdxElementId": "SPDXRef-libxml2",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": "SPDXRef-zlib",
            },
        ],
        "properties": {"build.id": "42", "env": "prod"},
    }


def cyclonedx_payload():
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {"component": {"name": "acme-image", "type": "container"}},
        "components": [
            {
                "bom-ref": "openssl@3.0.13",
                "name": "openssl",
                "version": "3.0.13",
                "supplier": {"name": "Org: Acme"},
                "licenses": [{"license": {"id": "Apache-2.0"}}],
                "externalReferences": [
                    {"type": "purl", "url": "pkg:deb/openssl@3.0.13"}
                ],
            },
            {"bom-ref": "zlib@1.2.13", "name": "zlib", "version": "1.2.13"},
        ],
        "dependencies": [{"ref": "openssl@3.0.13", "dependsOn": ["zlib@1.2.13"]}],
        "properties": [{"name": "build.id", "value": "42"}],
    }


def test_parse_spdx_maps_packages_in_document_order():
    doc = bridge().parse_spdx(spdx_payload())
    assert doc.format == "spdx"
    assert doc.name == "acme-image"
    assert [component.name for component in doc.components] == [
        "openssl",
        "zlib",
        "libxml2",
    ]
    assert [component.version for component in doc.components] == [
        "3.0.13",
        "1.2.13",
        "2.9.14",
    ]
    assert doc.components[0].supplier == "Org: Acme"
    assert doc.components[0].license_expression == "Apache-2.0"
    assert doc.components[2].supplier is None


def test_parse_spdx_maps_external_refs_and_extra_fields():
    doc = bridge().parse_spdx(spdx_payload())
    assert doc.components[0].external_refs == {"purl": "pkg:deb/openssl@3.0.13"}
    assert doc.components[0].extra == {"licenseComments": "已核对上游公告"}
    assert doc.components[1].extra == {}


def test_parse_spdx_resolves_relationships_to_names():
    doc = bridge().parse_spdx(spdx_payload())
    assert [(rel.source, rel.target, rel.kind) for rel in doc.relationships] == [
        ("openssl", "zlib", "DEPENDS_ON"),
        ("libxml2", "zlib", "DEPENDS_ON"),
    ]


def test_parse_cyclonedx_maps_metadata_and_components():
    doc = bridge().parse_cyclonedx(cyclonedx_payload())
    assert doc.format == "cyclonedx"
    assert doc.name == "acme-image"
    assert [(component.name, component.version) for component in doc.components] == [
        ("openssl", "3.0.13"),
        ("zlib", "1.2.13"),
    ]
    assert doc.components[0].supplier == "Org: Acme"
    assert doc.components[0].license_expression == "Apache-2.0"
    assert doc.document_properties == {"build.id": "42"}


def test_parse_cyclonedx_reads_dependency_edges():
    doc = bridge().parse_cyclonedx(cyclonedx_payload())
    assert [(rel.source, rel.target, rel.kind) for rel in doc.relationships] == [
        ("openssl", "zlib", "DEPENDS_ON")
    ]


def test_validate_reports_sorted_field_paths():
    payload = spdx_payload()
    del payload["name"]
    del payload["packages"][1]["versionInfo"]
    payload["packages"][0].pop("SPDXID")
    assert bridge().validate(payload, "spdx") == [
        "name",
        "packages[0].SPDXID",
        "packages[1].versionInfo",
    ]


def test_validate_accepts_wellformed_documents():
    instance = bridge()
    assert instance.validate(spdx_payload(), "spdx") == []
    assert instance.validate(cyclonedx_payload(), "cyclonedx") == []


def test_parse_rejects_document_of_the_other_format():
    instance = bridge()
    with pytest.raises(br.SbomBridgeError) as exc:
        instance.parse_spdx(cyclonedx_payload())
    assert exc.value.code == "DOCUMENT_FORMAT_MISMATCH"
    assert exc.value.detail == "spdxVersion"

    with pytest.raises(br.SbomBridgeError) as exc:
        instance.parse_cyclonedx(spdx_payload())
    assert exc.value.code == "DOCUMENT_FORMAT_MISMATCH"
    assert exc.value.detail == "bomFormat"


def test_convert_preserves_component_identity():
    instance = bridge()
    doc = instance.parse_spdx(spdx_payload())
    payload, report = instance.convert(doc, "cyclonedx")
    assert payload["bomFormat"] == "CycloneDX"
    assert sorted((item["name"], item["version"]) for item in payload["components"]) == [
        ("libxml2", "2.9.14"),
        ("openssl", "3.0.13"),
        ("zlib", "1.2.13"),
    ]
    assert (report.source_format, report.target_format) == ("spdx", "cyclonedx")


def test_convert_is_deterministic():
    instance = bridge()
    doc = instance.parse_spdx(spdx_payload())
    first, _ = instance.convert(doc, "cyclonedx")
    second, _ = instance.convert(doc, "cyclonedx")
    assert first == second
    assert instance.to_json(first) == instance.to_json(second)


def test_convert_report_marks_fields_kept_in_properties():
    instance = bridge()
    doc = instance.parse_spdx(spdx_payload())
    payload, report = instance.convert(doc, "cyclonedx")
    openssl = next(
        item for item in payload["components"] if item["name"] == "openssl"
    )
    properties = {item["name"]: item["value"] for item in openssl["properties"]}
    assert properties["gsb:licenseComments"] == "已核对上游公告"
    assert "components[0].licenseComments" in report.preserved
    assert any(path.endswith("licenseComments") for path in report.preserved)


def test_roundtrip_keeps_component_names_and_versions():
    instance = bridge()
    back = instance.roundtrip(spdx_payload(), "spdx")
    assert back["spdxVersion"] == "SPDX-2.3"
    assert back["name"] == "acme-image"
    assert sorted(item["name"] for item in back["packages"]) == [
        "libxml2",
        "openssl",
        "zlib",
    ]
    assert {item["name"]: item["versionInfo"] for item in back["packages"]} == {
        "openssl": "3.0.13",
        "zlib": "1.2.13",
        "libxml2": "2.9.14",
    }


def test_roundtrip_keeps_single_license_expression():
    instance = bridge()
    back = instance.roundtrip(spdx_payload(), "spdx")
    by_name = {item["name"]: item for item in back["packages"]}
    assert by_name["openssl"]["licenseConcluded"] == "Apache-2.0"
    assert by_name["libxml2"]["licenseConcluded"] == "MIT"
    assert by_name["zlib"]["licenseConcluded"] == "Zlib"


def test_component_limit_rejects_oversized_document():
    instance = bridge(max_components=2)
    doc = instance.parse_spdx(spdx_payload())
    with pytest.raises(br.SbomBridgeError) as exc:
        instance.convert(doc, "cyclonedx")
    assert exc.value.code == "COMPONENT_LIMIT_EXCEEDED"


def test_document_properties_survive_conversion():
    instance = bridge()
    doc = instance.parse_spdx(spdx_payload())
    payload, _ = instance.convert(doc, "cyclonedx")
    properties = {item["name"]: item["value"] for item in payload["properties"]}
    assert properties["build.id"] == "42"
    assert properties["env"] == "prod"

    back = instance.roundtrip(spdx_payload(), "spdx")
    assert back["properties"]["build.id"] == "42"
    assert back["properties"]["env"] == "prod"
