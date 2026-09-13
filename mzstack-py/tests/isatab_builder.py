"""Builders for synthetic MetaboLights studies.

Writes real ISA-Tab -- an investigation file, a sample sheet and assay files --
into a directory laid out the way the repository lays out a study, so
``metabolights-utils`` parses it exactly as it parses a downloaded one and the
ingest path can be tested end to end without a network.

Kept out of ``conftest.py`` so test modules can import the builders directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

__all__ = ["AssaySpec", "write_study", "STUDY_ID"]

STUDY_ID = "MTBLSTEST"

#: Column layout of the assay files written here: a cut-down but structurally
#: faithful version of a real LC-MS assay.
_ASSAY_COLUMNS = (
    "Sample Name",
    "Protocol REF",
    "Parameter Value[Scan polarity]",
    "Parameter Value[Instrument]",
    "Term Source REF",
    "Term Accession Number",
    "MS Assay Name",
    "Raw Spectral Data File",
    "Derived Spectral Data File",
    "Metabolite Assignment File",
)

_SAMPLE_COLUMNS = (
    "Source Name",
    "Characteristics[Organism]",
    "Term Source REF",
    "Term Accession Number",
    "Protocol REF",
    "Sample Name",
    "Factor Value[Treatment]",
)


class AssaySpec:
    """One assay file: which samples it holds, and where its data files go.

    Args:
        name: the assay file name, which must start with ``a_``.
        samples: one sample name per row.
        files: one data file name per row, without a directory.
        column: which ISA column references them -- ``derived`` is where mzML
            usually sits, ``raw`` where a vendor format does.
        polarity: written into every row's ``Parameter Value[Scan polarity]``.
    """

    def __init__(
        self,
        name: str,
        samples: Sequence[str],
        files: Sequence[str],
        column: str = "derived",
        polarity: str = "positive",
    ) -> None:
        if len(samples) != len(files):
            raise ValueError("samples and files must correspond row for row")
        self.name = name
        self.samples = list(samples)
        self.files = list(files)
        self.column = column
        self.polarity = polarity

    def rows(self) -> list[list[str]]:
        rows = []
        for sample, file in zip(self.samples, self.files, strict=True):
            raw = f"FILES/{file}" if self.column == "raw" else ""
            derived = f"FILES/{file}" if self.column == "derived" else ""
            rows.append(
                [
                    sample,
                    "Mass spectrometry",
                    self.polarity,
                    "Bruker micrOTOF-Q I",
                    "MS",
                    "http://purl.obolibrary.org/obo/MS_1000703",
                    f"{sample}_{self.polarity[:3]}",
                    raw,
                    derived,
                    "m_metabolite_profiling_v2_maf.tsv",
                ]
            )
        return rows


def _write_table(path: Path, columns: Sequence[str], rows: Sequence[Sequence[str]]):
    lines = ["\t".join(f'"{c}"' for c in columns)]
    lines += ["\t".join(f'"{v}"' for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _investigation(study_id: str, assays: Sequence[AssaySpec]) -> str:
    """The minimum investigation file the ISA-Tab parser accepts.

    Sections and their field order matter to the parser, so this follows the
    specification's layout rather than listing only the fields under test.
    """
    n = len(assays)

    def field(name: str, values: Sequence[str]) -> str:
        return "\t".join([name, *values])

    lines = [
        "ONTOLOGY SOURCE REFERENCE",
        'Term Source Name\t"MS"\t"NCBITAXON"',
        'Term Source File\t""\t""',
        'Term Source Version\t""\t""',
        'Term Source Description\t"Mass Spectrometry Ontology"\t"NCBI Taxonomy"',
        "INVESTIGATION",
        f'Investigation Identifier\t"{study_id}"',
        'Investigation Title\t"Test investigation"',
        'Investigation Description\t""',
        'Investigation Submission Date\t""',
        'Investigation Public Release Date\t""',
        "INVESTIGATION PUBLICATIONS",
        'Investigation PubMed ID\t""',
        'Investigation Publication DOI\t""',
        'Investigation Publication Author List\t""',
        'Investigation Publication Title\t""',
        'Investigation Publication Status\t""',
        'Investigation Publication Status Term Accession Number\t""',
        'Investigation Publication Status Term Source REF\t""',
        "INVESTIGATION CONTACTS",
        'Investigation Person Last Name\t""',
        'Investigation Person First Name\t""',
        'Investigation Person Mid Initials\t""',
        'Investigation Person Email\t""',
        'Investigation Person Phone\t""',
        'Investigation Person Fax\t""',
        'Investigation Person Address\t""',
        'Investigation Person Affiliation\t""',
        'Investigation Person Roles\t""',
        'Investigation Person Roles Term Accession Number\t""',
        'Investigation Person Roles Term Source REF\t""',
        "STUDY",
        f'Study Identifier\t"{study_id}"',
        'Study Title\t"Test study"',
        'Study Description\t"A study built by the test suite."',
        'Study Submission Date\t"2024-01-01"',
        'Study Public Release Date\t"2024-01-01"',
        f'Study File Name\t"s_{study_id}.txt"',
        "STUDY DESIGN DESCRIPTORS",
        'Study Design Type\t"untargeted metabolites"',
        'Study Design Type Term Accession Number\t""',
        'Study Design Type Term Source REF\t""',
        "STUDY PUBLICATIONS",
        'Study PubMed ID\t""',
        'Study Publication DOI\t""',
        'Study Publication Author List\t""',
        'Study Publication Title\t""',
        'Study Publication Status\t""',
        'Study Publication Status Term Accession Number\t""',
        'Study Publication Status Term Source REF\t""',
        "STUDY FACTORS",
        'Study Factor Name\t"Treatment"',
        'Study Factor Type\t"treatment"',
        'Study Factor Type Term Accession Number\t""',
        'Study Factor Type Term Source REF\t""',
        "STUDY ASSAYS",
        field(
            "Study Assay Measurement Type",
            ['"metabolite profiling"'] * n,
        ),
        field("Study Assay Measurement Type Term Accession Number", ['""'] * n),
        field("Study Assay Measurement Type Term Source REF", ['""'] * n),
        field("Study Assay Technology Type", ['"mass spectrometry"'] * n),
        field("Study Assay Technology Type Term Accession Number", ['""'] * n),
        field("Study Assay Technology Type Term Source REF", ['""'] * n),
        field("Study Assay Technology Platform", ['"Liquid Chromatography MS"'] * n),
        field("Study Assay File Name", [f'"{a.name}"' for a in assays]),
        "STUDY PROTOCOLS",
        'Study Protocol Name\t"Mass spectrometry"',
        'Study Protocol Type\t"Mass spectrometry"',
        'Study Protocol Type Term Accession Number\t""',
        'Study Protocol Type Term Source REF\t""',
        'Study Protocol Description\t""',
        'Study Protocol URI\t""',
        'Study Protocol Version\t""',
        'Study Protocol Parameters Name\t"Scan polarity;Instrument"',
        'Study Protocol Parameters Name Term Accession Number\t";"',
        'Study Protocol Parameters Name Term Source REF\t";"',
        'Study Protocol Components Name\t""',
        'Study Protocol Components Type\t""',
        'Study Protocol Components Type Term Accession Number\t""',
        'Study Protocol Components Type Term Source REF\t""',
        "STUDY CONTACTS",
        'Study Person Last Name\t"Tester"',
        'Study Person First Name\t"A"',
        'Study Person Mid Initials\t""',
        'Study Person Email\t"tester@example.org"',
        'Study Person Phone\t""',
        'Study Person Fax\t""',
        'Study Person Address\t""',
        'Study Person Affiliation\t""',
        'Study Person Roles\t"Principal Investigator"',
        'Study Person Roles Term Accession Number\t""',
        'Study Person Roles Term Source REF\t""',
    ]
    return "\n".join(lines) + "\n"


def write_study(
    root: Path,
    assays: Sequence[AssaySpec],
    study_id: str = STUDY_ID,
    treatments: dict[str, str] | None = None,
) -> Path:
    """Write a study under ``root/<study_id>``; return that directory.

    ``root`` is the cache root, i.e. what ``cache_dir`` is set to; the reader
    resolves a study as ``<root>/<study_id>``.

    Args:
        assays: the assay files to write.
        treatments: ``Factor Value[Treatment]`` per sample name, defaulting to
            ``control``.
    """
    study = root / study_id
    (study / "FILES").mkdir(parents=True, exist_ok=True)

    (study / "i_Investigation.txt").write_text(
        _investigation(study_id, assays), encoding="utf-8"
    )

    treatments = treatments or {}
    seen: list[str] = []
    for assay in assays:
        for sample in assay.samples:
            if sample not in seen:
                seen.append(sample)
    _write_table(
        study / f"s_{study_id}.txt",
        _SAMPLE_COLUMNS,
        [
            [
                sample,
                "Arabidopsis thaliana",
                "NCBITAXON",
                "http://purl.bioontology.org/ontology/NCBITAXON/3702",
                "Sample collection",
                sample,
                treatments.get(sample, "control"),
            ]
            for sample in seen
        ],
    )

    for assay in assays:
        _write_table(study / assay.name, _ASSAY_COLUMNS, assay.rows())

    return study
