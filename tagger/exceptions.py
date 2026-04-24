from __future__ import annotations


class TaggerError(Exception):
    """Base exception for all tagger errors."""


class EnrichmentError(TaggerError):
    """Base exception for enrichment-related errors."""


class DiscogsError(EnrichmentError):
    """Errors related to Discogs API."""


class RateLimitError(DiscogsError):
    """Raised when an API rate limit is exceeded."""

    def __init__(self, service: str, retry_after: int | None = None) -> None:
        self.service = service
        self.retry_after = retry_after
        message = f"Rate limit exceeded for {service}"
        if retry_after:
            message += f". Retry after {retry_after} seconds."
        super().__init__(message)


class TransientAPIError(DiscogsError):
    """Raised on transient server-side errors (5xx) that are safe to retry."""

    def __init__(self, service: str, status_code: int) -> None:
        self.service = service
        self.status_code = status_code
        super().__init__(f"Transient {status_code} error from {service}")


class MusicBrainzError(EnrichmentError):
    """Errors related to the MusicBrainz API."""


class LLMError(EnrichmentError):
    """Errors related to LLM processing."""


class DatabaseError(TaggerError):
    """Errors related to database operations."""


class FileProcessError(TaggerError):
    """Errors related to file reading/writing."""
