from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
import rfc8785

from adapters.parsers.ragflow_documents import (
    compile_document_bytes,
)
from applications.document_compiler_runner import (
    ArtifactSource,
    BytesArtifactSource,
    compile_in_local_document_runner,
)
from engine.supply import (
    DOCX_CONFIG_V1,
    MAX_FORMAT_DOCUMENT_UNITS,
    PDF_TEXT_OUTLINE_V1,
    CompilationProfileRef,
    DocumentCompilationFailure,
    DocumentCompilationFailureCode,
    DocumentStructuralKind,
    DocxXmlLocator,
    ParsedDocument,
    PdfRegionLocator,
    StructuralUnit,
    canonicalize_parsed_document,
    deserialize_parsed_document,
)
from eval._compiler_acceptance import acceptance_context

REPOSITORY_ROOT = Path(__file__).parents[2]


@dataclass
class _CountingArtifact(ArtifactSource):
    payload: bytes
    reads: int = 0

    def read(self) -> bytes:
        self.reads += 1
        return self.payload


def _docx_fixture(*, with_image: bool = False) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("Architecture", level=1)
    document.add_paragraph("First paragraph.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Key"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "parser"
    table.cell(1, 1).text = "registered"
    document.add_paragraph("Last paragraph.")
    if with_image:
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls

        run = document.add_paragraph().add_run()
        run._r.append(parse_xml(f"<pic:pic {nsdecls('pic')}></pic:pic>"))
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _docx_fixture_with_blank_source_block() -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("Architecture", level=1)
    document.add_paragraph("")
    document.add_paragraph("After blank source block.")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_outline_fixture() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    root = writer.add_outline_item("Overview", 0)
    writer.add_outline_item("Details", 1, parent=root)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_unknown_profile_refuses_before_artifact_bytes_are_opened() -> None:
    artifact = _CountingArtifact(b"must not be read")

    outcome = compile_in_local_document_runner(
        artifact,
        "pdf-layout-ocr-v1",
        acceptance_context=acceptance_context(),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.UNKNOWN_PROFILE
    assert artifact.reads == 0


def test_child_unknown_profile_refuses_before_stdin_is_read() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "applications.document_compiler_runner",
            "--profile",
            "pdf-layout-ocr-v1",
        ],
        input=b"must not be parsed",
        capture_output=True,
        check=True,
        timeout=30,
    )

    assert json.loads(completed.stdout) == {
        "outcome": "failure",
        "failure": {"code": "unknown_profile"},
    }


def test_child_enforces_artifact_bound_with_a_closed_refusal() -> None:
    from applications.document_compiler_runner import MAX_DOCUMENT_ARTIFACT_BYTES

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "applications.document_compiler_runner",
            "--profile",
            DOCX_CONFIG_V1,
        ],
        input=b"x" * (MAX_DOCUMENT_ARTIFACT_BYTES + 1),
        capture_output=True,
        check=True,
        timeout=30,
    )

    assert json.loads(completed.stdout) == {
        "outcome": "failure",
        "failure": {"code": "artifact_bound_exceeded"},
    }


def test_owned_document_runner_has_no_network_database_or_model_imports() -> None:
    paths = (
        REPOSITORY_ROOT / "adapters/parsers/ragflow_documents.py",
        REPOSITORY_ROOT / "applications/document_compiler_runner.py",
        REPOSITORY_ROOT / "third_party/ragflow/deepdoc/parser/docx_parser.py",
        REPOSITORY_ROOT / "third_party/ragflow/deepdoc/parser/utils.py",
    )
    forbidden = {
        "common",
        "huggingface_hub",
        "httpx",
        "os",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "urllib",
    }
    for path in paths:
        imports: set[str] = set()
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module is not None
                imports.add(node.module.partition(".")[0])
        assert imports.isdisjoint(forbidden), path


def test_docx_profile_preserves_ooxml_block_order_and_typed_locators() -> None:
    source = _docx_fixture()

    outcome = compile_document_bytes(
        source,
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is ParsedDocument
    assert outcome.units is not None
    assert [unit.kind for unit in outcome.units] == [
        DocumentStructuralKind.HEADING,
        DocumentStructuralKind.PARAGRAPH,
        DocumentStructuralKind.TABLE,
        DocumentStructuralKind.PARAGRAPH,
    ]
    assert [unit.text for unit in outcome.units] == [
        "Architecture",
        "First paragraph.",
        "Key\tValue\nparser\tregistered",
        "Last paragraph.",
    ]
    assert all(
        type(locator) is DocxXmlLocator
        for unit in outcome.units
        for locator in unit.locators
    )
    docx_locators = tuple(unit.locators[0] for unit in outcome.units)
    assert all(type(locator) is DocxXmlLocator for locator in docx_locators)
    assert tuple(
        locator.block_ordinal
        for locator in docx_locators
        if type(locator) is DocxXmlLocator
    ) == (0, 1, 2, 3)
    assert outcome.provenance.config_version == DOCX_CONFIG_V1


def test_docx_image_is_an_honest_typed_refusal() -> None:
    outcome = compile_document_bytes(
        _docx_fixture(with_image=True),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


def test_docx_locator_ordinal_is_the_ooxml_source_ordinal_not_output_index() -> None:
    outcome = compile_document_bytes(
        _docx_fixture_with_blank_source_block(),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is ParsedDocument
    assert outcome.units is not None
    locators = tuple(unit.locators[0] for unit in outcome.units)
    assert tuple(
        locator.block_ordinal
        for locator in locators
        if type(locator) is DocxXmlLocator
    ) == (0, 2)


def test_pdf_outline_profile_emits_source_order_and_pdf_region_locators() -> None:
    outcome = compile_document_bytes(
        _pdf_outline_fixture(),
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is ParsedDocument
    assert outcome.units is not None
    assert [unit.text for unit in outcome.units] == ["Overview", "Details"]
    assert [unit.heading_level for unit in outcome.units] == [1, 2]
    assert all(unit.kind is DocumentStructuralKind.HEADING for unit in outcome.units)
    assert all(
        type(unit.locators[0]) is PdfRegionLocator for unit in outcome.units
    )
    pdf_locators = tuple(unit.locators[0] for unit in outcome.units)
    assert [
        locator.page_number
        for locator in pdf_locators
        if type(locator) is PdfRegionLocator
    ] == [1, 2]


def test_page_render_digest_is_identical_for_same_pixels_in_distinct_pdfs() -> None:
    first = _pdf_outline_fixture()
    second = first + b"\n% byte-distinct container\n"
    outcomes = tuple(
        compile_document_bytes(
            source,
            CompilationProfileRef(
                "context-engine-pdf-outline-v1",
                PDF_TEXT_OUTLINE_V1,
            ),
        )
        for source in (first, second)
    )

    assert all(type(outcome) is ParsedDocument for outcome in outcomes)
    documents = tuple(
        outcome for outcome in outcomes if type(outcome) is ParsedDocument
    )
    assert documents[0].artifact_digest != documents[1].artifact_digest
    assert documents[0].units is not None and documents[1].units is not None
    first_locator = documents[0].units[0].locators[0]
    second_locator = documents[1].units[0].locators[0]
    assert type(first_locator) is PdfRegionLocator
    assert type(second_locator) is PdfRegionLocator
    assert first_locator.page_render_digest == second_locator.page_render_digest
    assert documents[0].content_hash == documents[1].content_hash
    assert documents[0].compilation_digest != documents[1].compilation_digest


@pytest.mark.parametrize(
    ("profile_ref", "fixture"),
    (
        (DOCX_CONFIG_V1, _docx_fixture),
        (PDF_TEXT_OUTLINE_V1, _pdf_outline_fixture),
    ),
)
def test_profile_digest_is_identical_across_two_fresh_processes(
    profile_ref: str,
    fixture: object,
) -> None:
    assert callable(fixture)
    source = fixture()

    first = compile_in_local_document_runner(
        BytesArtifactSource(source),
        profile_ref,
        acceptance_context=acceptance_context(),
    )
    second = compile_in_local_document_runner(
        BytesArtifactSource(source),
        profile_ref,
        acceptance_context=acceptance_context(),
    )

    assert type(first) is ParsedDocument
    assert type(second) is ParsedDocument
    assert first.compilation_digest == second.compilation_digest
    assert canonicalize_parsed_document(first) == (
        canonicalize_parsed_document(second)
    )


@pytest.mark.parametrize("profile_ref", (DOCX_CONFIG_V1, PDF_TEXT_OUTLINE_V1))
def test_malformed_artifact_is_a_closed_typed_refusal(profile_ref: str) -> None:
    outcome = compile_in_local_document_runner(
        BytesArtifactSource(b"not the declared format"),
        profile_ref,
        acceptance_context=acceptance_context(),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_format_neutral_constructor_rejects_more_than_the_hard_unit_bound() -> None:
    digest = "0" * 64
    unit = StructuralUnit(
        ordinal=0,
        kind=DocumentStructuralKind.PARAGRAPH,
        text="bounded",
        locators=(
            DocxXmlLocator(
                artifact_digest=digest,
                part_uri="/word/document.xml",
                block_ordinal=0,
                xml_digest=digest,
            ),
        ),
    )
    units = tuple(
        StructuralUnit(
            ordinal=ordinal,
            kind=unit.kind,
            text=unit.text,
            locators=(
                DocxXmlLocator(
                    artifact_digest=digest,
                    part_uri="/word/document.xml",
                    block_ordinal=ordinal,
                    xml_digest=digest,
                ),
            ),
        )
        for ordinal in range(MAX_FORMAT_DOCUMENT_UNITS + 1)
    )

    with pytest.raises(ValueError, match="structural-unit hard bound"):
        ParsedDocument.format_neutral(
            artifact_digest=digest,
            profile=CompilationProfileRef(
                "context-engine-docx-v1",
                DOCX_CONFIG_V1,
            ),
            units=units,
        )


def test_format_constructor_rejects_wrong_compiler_and_cross_artifact_locator() -> (
    None
):
    artifact_digest = "1" * 64
    unit = StructuralUnit(
        ordinal=0,
        kind=DocumentStructuralKind.PARAGRAPH,
        text="bounded",
        locators=(
            DocxXmlLocator(
                artifact_digest="2" * 64,
                part_uri="/word/document.xml",
                block_ordinal=0,
                xml_digest="3" * 64,
            ),
        ),
    )
    with pytest.raises(ValueError, match="compiler/profile identity"):
        ParsedDocument.format_neutral(
            artifact_digest=artifact_digest,
            profile=CompilationProfileRef("forged-compiler", DOCX_CONFIG_V1),
            units=(unit,),
        )
    with pytest.raises(ValueError, match="bind the document artifact"):
        ParsedDocument.format_neutral(
            artifact_digest=artifact_digest,
            profile=CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
            units=(unit,),
        )


def test_deserializer_rejects_forged_compiler_and_locator_artifact() -> None:
    compiled = compile_document_bytes(
        _docx_fixture(),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )
    assert type(compiled) is ParsedDocument
    canonical = json.loads(canonicalize_parsed_document(compiled))
    canonical["profile"]["compilerRef"] = "forged-compiler"
    with pytest.raises(ValueError, match="compiler/profile identity"):
        deserialize_parsed_document(rfc8785.dumps(canonical))
    canonical["profile"]["compilerRef"] = "context-engine-docx-v1"
    canonical["units"][0]["locators"][0]["artifactDigest"] = "f" * 64
    with pytest.raises(ValueError, match="bind the document artifact"):
        deserialize_parsed_document(rfc8785.dumps(canonical))


def test_unleased_document_runner_requires_private_acceptance_capability() -> None:
    unchecked_runner = cast(Any, compile_in_local_document_runner)
    outcome = unchecked_runner(
        BytesArtifactSource(_docx_fixture()),
        DOCX_CONFIG_V1,
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.RUNNER_UNAVAILABLE


def test_raw_compiler_converts_constructor_rejection_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_constructor(*args: object, **kwargs: object) -> ParsedDocument:
        raise ValueError("domain constructor rejected parser output")

    monkeypatch.setattr(ParsedDocument, "format_neutral", reject_constructor)
    outcome = compile_document_bytes(
        _docx_fixture(),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT
