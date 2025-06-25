#!/usr/bin/env python3
"""
Test suite for SGM rule validation
Tests that validation works correctly when enabled
"""

import pytest

from sgm_simulator import SGMRule


class TestSGMRuleValidation:
    """Test suite for SGM rule validation"""

    def test_valid_parameters_pass(self):
        """Test that valid parameters pass validation"""
        # Valid parameters should work
        rule = SGMRule(
            name="Valid Rule",
            growth_percentage=20.0,  # Within 5-50%
            min_growth_dollars=25.0,  # Above $20
            enabled=True,
        )
        assert rule.growth_percentage == 20.0
        assert rule.min_growth_dollars == 25.0

    def test_growth_percentage_validation(self):
        """Test growth percentage validation bounds"""
        # Too low (below 5%)
        with pytest.raises(
            ValueError, match="Growth percentage must be between 5% and 50%"
        ):
            SGMRule(
                name="Too Low",
                growth_percentage=2.0,
                min_growth_dollars=20.0,
                enabled=True,
            )

        # Too high (above 50%)
        with pytest.raises(
            ValueError, match="Growth percentage must be between 5% and 50%"
        ):
            SGMRule(
                name="Too High",
                growth_percentage=75.0,
                min_growth_dollars=20.0,
                enabled=True,
            )

        # Boundary values should work
        rule_min = SGMRule("Min", 5.0, 20.0, True)
        rule_max = SGMRule("Max", 50.0, 20.0, True)
        assert rule_min.growth_percentage == 5.0
        assert rule_max.growth_percentage == 50.0

    def test_min_growth_dollars_validation(self):
        """Test minimum growth dollars validation"""
        # Too low (below $20)
        with pytest.raises(
            ValueError, match="Minimum growth dollars must be at least \\$20"
        ):
            SGMRule(
                name="Too Low",
                growth_percentage=20.0,
                min_growth_dollars=15.0,
                enabled=True,
            )

        # Boundary value should work
        rule = SGMRule("Boundary", 20.0, 20.0, True)
        assert rule.min_growth_dollars == 20.0

    def test_weekly_recalc_day_validation(self):
        """Test weekly recalculation day validation"""
        # Invalid day (negative)
        with pytest.raises(ValueError, match="Weekly recalculation day must be 0-6"):
            SGMRule(
                name="Invalid Day",
                growth_percentage=20.0,
                min_growth_dollars=20.0,
                enabled=True,
                weekly_recalc_day=-1,
            )

        # Invalid day (too high)
        with pytest.raises(ValueError, match="Weekly recalculation day must be 0-6"):
            SGMRule(
                name="Invalid Day",
                growth_percentage=20.0,
                min_growth_dollars=20.0,
                enabled=True,
                weekly_recalc_day=7,
            )

        # Valid days should work
        for day in range(7):
            rule = SGMRule("Valid", 20.0, 20.0, True, weekly_recalc_day=day)
            assert rule.weekly_recalc_day == day

    def test_validation_disabled_allows_invalid_values(self):
        """Test that validation can be disabled for testing"""
        # Should allow invalid values when validation is disabled
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=0.0,  # Invalid when validation enabled
            min_growth_dollars=5.0,  # Invalid when validation enabled
            enabled=True,
            validate_bounds=False,  # Disable validation
        )
        assert rule.growth_percentage == 0.0
        assert rule.min_growth_dollars == 5.0

    def test_ui_slider_bounds_compliance(self):
        """Test that UI slider bounds match validation"""
        # This test documents that the UI slider (5.0, 50.0) matches validation bounds
        ui_min, ui_max = 5.0, 50.0

        # Test that UI bounds are valid
        rule_min = SGMRule("UI Min", ui_min, 20.0, True)
        rule_max = SGMRule("UI Max", ui_max, 20.0, True)

        assert rule_min.growth_percentage == ui_min
        assert rule_max.growth_percentage == ui_max

        # Test that values just outside UI bounds are invalid
        with pytest.raises(ValueError):
            SGMRule("Below UI", ui_min - 0.1, 20.0, True)

        with pytest.raises(ValueError):
            SGMRule("Above UI", ui_max + 0.1, 20.0, True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
