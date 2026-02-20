"""Custom exception classes for the application."""
from typing import Any


class AppException(Exception):
    """Base exception for the application."""

    def __init__(self, message: str, detail: Any = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class NotFoundError(AppException):
    """Resource not found."""
    pass


class DuplicateError(AppException):
    """Duplicate resource detected."""
    pass


class ScrapingError(AppException):
    """Error during scraping operation."""
    pass


class RobotsBlockedError(ScrapingError):
    """URL blocked by robots.txt or robots.txt unavailable (fail-closed)."""
    pass


class RateLimitError(ScrapingError):
    """Rate limit exceeded (HTTP 429)."""
    pass


class ParsingError(AppException):
    """Error parsing HTML content."""
    pass


class EnrichmentError(AppException):
    """Error during description enrichment."""
    pass


class ExportError(AppException):
    """Error during data export."""
    pass


class ValidationError(AppException):
    """Data validation error."""
    pass


class JobAlreadyRunningError(AppException):
    """A scrape job is already running (MVP limitation: 1 at a time)."""
    pass


class JobCancelledError(AppException):
    """Job was cancelled by user."""
    pass
