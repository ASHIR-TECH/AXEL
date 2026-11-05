"""Read-only reporting projections. No dashboard component can submit an order."""

from axel.dashboard.review import (
    DateRange,
    PerformanceRecord,
    PerformanceSummary,
    ReviewReport,
    build_review_report,
    review_date_range,
)

__all__ = [
    "DateRange",
    "PerformanceRecord",
    "PerformanceSummary",
    "ReviewReport",
    "build_review_report",
    "review_date_range",
]
