class AnalysisError(Exception):
    """Base analysis error."""


class FileValidationError(AnalysisError):
    """Raised when the upload violates request constraints."""
