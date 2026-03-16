"""Tests for preview coverage reporting."""

from app.services.preview_service import _build_field_results


class TestPreviewCoverage:
    def test_build_field_results_reports_coverage_and_missing_critical_fields(self):
        raw_data = {
            "title": "Moradia em Banda T3",
            "price": "517 500 €",
            "district": "Porto",
            "gross_area": "268 m²",
        }

        results, warnings, coverage = _build_field_results(raw_data)

        assert any(result.field == "title" and result.status == "ok" for result in results)
        assert any(result.field == "property_type" and result.status == "empty" for result in results)
        assert "property_type" in coverage.critical_missing
        assert coverage.total_fields > 0
        assert coverage.ok_fields >= 3
        assert coverage.empty_fields > 0
        assert coverage.coverage_percent < 100
        assert warnings