"""Exception types raised across mzstack."""

from __future__ import annotations


class MzStackError(Exception):
    """Base class for every error mzstack raises itself."""


class FormatError(MzStackError):
    """A dataset is not readable as mzStack.

    Raised for a missing manifest, a manifest declaring another format, a
    major version this implementation does not read, and structural problems
    such as a dataset mixing run kinds.
    """


class ArchiveError(MzStackError):
    """An mzPeak archive could not be read as the specification requires."""


class StudyError(MzStackError):
    """A repository study cannot be ingested.

    Raised for a study that does not exist, one whose assays reference no mzML,
    a file selection that matches nothing, and a download that did not complete.
    """


class NotTranslatable(MzStackError):
    """A MassQL query cannot be compiled to SQL and must use the engine.

    Not a failure: the caller falls back to massql's own evaluator, which
    handles every construct. Only the compiled fast path is declined.
    """
