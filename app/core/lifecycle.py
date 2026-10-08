"""Listing lifecycle constants shared by the crawlers, services and API."""

STATUS_ACTIVE = "active"
STATUS_REMOVED = "removed"

# Query-level value meaning "do not filter on status".
STATUS_ALL = "all"

# HTTP statuses that mean "this listing no longer exists on the source".
GONE_STATUSES = frozenset({404, 410})
