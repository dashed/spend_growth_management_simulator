#!/usr/bin/env python3
"""
Edge case tests for SGM simulator
Tests boundary conditions and error handling
"""

import pytest

from sgm_simulator import DayResult, ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMEdgeCases:
    """Test suite for SGM edge cases and boundary conditions"""

    def test_zero_growth_percentage(self):
        """Test with 0% growth (minimum growth only)"""
        rule = SGMRule(
            name="Zero Growth",
            growth_percentage=0.0,
            min_growth_dollars=20.0,
            enabled=True,
            validate_bounds=False,  # Disable validation for edge case testing
        )

        history = [5.0] * 7
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # Should use linear growth only (no exponential)
        recent_7 = 35.0
        recent_6 = 30.0
        expected = recent_7 + 20.0 / 7 - recent_6  # Linear only

        assert limit == pytest.approx(expected, rel=1e-3)

    def test_very_high_spending_request(self):
        """Test with extremely high spending request"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=1_000_000.0,  # $1M request
            wallet_balance=10.0,
            accepted_history=[5.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should reject most of it
        assert result.rejected_spend > 999_000
        assert result.intervention_type == "shutdown"

    def test_negative_wallet_prevention(self):
        """Ensure wallet never goes negative"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Even with zero wallet and no history
        result, _, _ = SGMEngine.simulate_day(
            day_index=0,
            billing_day=1,
            requested_spend=100.0,
            wallet_balance=0.0,
            accepted_history=[],
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Wallet should never be negative
        assert result.wallet_balance_end >= 0
        assert result.wallet_balance_start >= 0

    def test_very_long_history(self):
        """Test with very long spending history"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # 365 days of history
        long_history = [10.0] * 365
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(long_history, rule)

        # Should only use recent 7 days
        assert limit > 0
        assert limit < 50  # Reasonable limit

    def test_all_zero_history(self):
        """Test with history of all zeros after bootstrap"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # 7 days of zero spending
        history = [0.0] * 7
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # Should return minimum growth / 7
        assert limit == pytest.approx(20.0 / 7, rel=1e-3)

    def test_reserved_volume_boundary(self):
        """Test exact reserved volume boundary"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Test with exactly 100 used
        result, _, _ = SGMEngine.simulate_day(
            day_index=20,
            billing_day=21,
            requested_spend=10.0,
            wallet_balance=10.0,
            accepted_history=[5.0] * 20,
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=100.0,  # Exactly at limit
            manual_allowance=0,
        )

        # Should use 0 from reserved, all from SGM
        assert result.reserved_spend == 0.0
        assert result.sgm_spend > 0

    def test_fractional_spending(self):
        """Test with fractional cent amounts"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test with fractional amounts
        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=0.001,  # $0.001
            wallet_balance=0.01,
            accepted_history=[0.001] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should handle fractional amounts correctly
        assert result.accepted_spend <= result.requested_spend
        assert result.wallet_balance_end >= 0

    def test_billing_day_edge_cases(self):
        """Test billing day transitions"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0,
            billing_day_start=30,  # Start at end of month
            days_in_cycle=30,
        )

        # Test transition from day 30 to day 1
        result1, _, _ = SGMEngine.simulate_day(
            day_index=0,
            billing_day=30,
            requested_spend=50.0,
            wallet_balance=0.0,
            accepted_history=[],
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        result2, _, _ = SGMEngine.simulate_day(
            day_index=1,
            billing_day=1,  # Should wrap to 1
            requested_spend=60.0,
            wallet_balance=result1.wallet_balance_end,
            accepted_history=[result1.accepted_spend],
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=result1.cumulative_reserved_used,
            manual_allowance=0,
        )

        # First day should use reserved volume
        assert result1.reserved_spend == 50.0
        # Second day (billing day 1) should reset reserved volume, so full 60 available
        assert result2.reserved_spend == 60.0  # Reserved volume reset on billing day 1

    def test_growth_percentage_bounds(self):
        """Test with growth percentages at boundaries (5% and 50%)"""
        # Test minimum growth percentage
        rule_min = SGMRule(
            name="Min Growth",
            growth_percentage=5.0,  # Minimum allowed
            min_growth_dollars=20.0,
            enabled=True,
        )

        history = [10.0] * 7
        limit_min, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule_min)

        # Test maximum growth percentage
        rule_max = SGMRule(
            name="Max Growth",
            growth_percentage=50.0,  # Maximum allowed
            min_growth_dollars=20.0,
            enabled=True,
        )

        limit_max, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule_max)

        # Max growth should give higher limit
        assert limit_max > limit_min
        assert limit_min > 0
        assert limit_max > 0

    def test_concurrent_reserved_and_manual(self):
        """Test interaction of reserved volume and manual allowance"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Test with both reserved and manual allowance
        result, _, _ = SGMEngine.simulate_day(
            day_index=0,
            billing_day=1,
            requested_spend=200.0,
            wallet_balance=0.0,
            accepted_history=[],
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=0,
            manual_allowance=100.0,
        )

        # Should use reserved first, then SGM+manual
        assert result.reserved_spend <= 100.0  # Max reserved available
        assert result.accepted_spend > 100.0  # Should accept more with manual

    def test_intervention_boundary_conditions(self):
        """Test intervention detection at exact boundaries"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test exactly 90% rejection (boundary for shutdown)
        wallet_available = 10.0
        sgm_request = 100.0

        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=sgm_request,
            wallet_balance=wallet_available - 2.86,  # Adjust for daily limit
            accepted_history=[5.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Check intervention type based on actual rejection rate
        if result.sgm_spend > 0:
            sgm_rejection_rate = (sgm_request - result.sgm_spend) / sgm_request
            if sgm_rejection_rate >= 0.9:
                assert result.intervention_type == "shutdown"
            elif sgm_rejection_rate > 0:
                assert result.intervention_type == "throttle"

    def test_insufficient_history_handling(self):
        """Test that bootstrap period handles insufficient history gracefully"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test with 6 days of history (still in bootstrap)
        history = [1.0] * 6
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # Should return a valid limit, not throw an exception
        assert limit > 0
        assert isinstance(limit, float)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
