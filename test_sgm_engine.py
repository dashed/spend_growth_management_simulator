#!/usr/bin/env python3
"""
Comprehensive test suite for SGMEngine from sgm_simulator_v5.py
Tests the core SGM algorithm and spend limit calculations
"""

import pytest

from sgm_simulator import DayResult, ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMEngine:
    """Test suite for SGMEngine core functionality"""

    def test_bootstrap_initial_limit(self):
        """Test initial daily limit calculation during bootstrap"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Day 0 should give us min_growth / 7
        limit, _, _ = SGMEngine.calculate_daily_spend_limit([], rule)
        assert limit == pytest.approx(20.0 / 7, rel=1e-3)

    def test_bootstrap_growth(self):
        """Test that bootstrap allows growth based on spending"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Simulate first few days with increasing spending
        history = [2.86]  # Day 0 spent exactly the limit
        limit_day1, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # Day 1 should allow 20% growth
        expected = 2.86 * 1.2
        assert limit_day1 >= expected

    def test_bootstrap_weekly_minimum(self):
        """Test that bootstrap ensures weekly minimum is achievable"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # If we've spent less than needed, limit should increase
        history = [1.0, 1.0, 1.0]  # Only spent $3 in 3 days
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # Need to spend at least $17 in remaining 4 days = $4.25/day
        assert limit >= 4.25

    def test_prfaq_algorithm_start(self):
        """Test PRFAQ algorithm activation after 7 days"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # 7 days of history at $3/day
        history = [3.0] * 7
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # PRFAQ should calculate based on recent history
        # recent_7 = 21, recent_6 = 18
        # exponential: 21 * 1.20^(1/7) - 18 ≈ 3.6
        # linear: 21 + 20/7 - 18 ≈ 5.86
        assert limit > 3.0  # Should be higher than historical average

    def test_prfaq_growth_calculation(self):
        """Test PRFAQ growth calculations"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,  # 20% per week
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Steady spending at $5/day for 7 days
        history = [5.0] * 7
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # Should choose max of exponential and linear growth
        recent_7 = 35.0
        recent_6 = 30.0
        growth_factor = 1.20 ** (1.0 / 7)  # Daily growth for 20% weekly
        exponential = recent_7 * growth_factor - recent_6
        linear = recent_7 + 20.0 / 7 - recent_6

        expected = max(exponential, linear, 0)
        assert limit == pytest.approx(expected, rel=1e-3)

    def test_wallet_cap_enforcement(self):
        """Test that wallet is capped at 2x daily limit"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Simulate day with high wallet balance
        result, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=5.0,
            wallet_balance=100.0,  # Very high wallet
            accepted_history=[3.0] * 7,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Wallet should be capped at 2x daily limit
        assert result.wallet_balance_start <= result.daily_spend_limit * 2

    def test_reserved_volume_consumption(self):
        """Test that reserved volumes are consumed before SGM"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        result, _, _ = SGMEngine.simulate_day(
            day_index=0,
            billing_day=1,
            requested_spend=10.0,
            wallet_balance=0.0,
            accepted_history=[],
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should consume from reserved first
        assert result.reserved_spend == 10.0
        assert result.sgm_spend == 0.0
        assert result.accepted_spend == 10.0

    def test_reserved_to_sgm_transition(self):
        """Test smooth transition when reserved volume is exhausted"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Simulate with 95 already used, requesting 10
        result, _, _ = SGMEngine.simulate_day(
            day_index=20,
            billing_day=21,
            requested_spend=10.0,
            wallet_balance=5.0,
            accepted_history=[5.0] * 20,  # History shows $5/day
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=95.0,
            manual_allowance=0,
        )

        # Should use remaining 5 from reserved, rest from SGM
        assert result.reserved_spend == 5.0
        assert result.sgm_spend > 0
        assert result.accepted_spend == result.reserved_spend + result.sgm_spend

    def test_intervention_types(self):
        """Test intervention type detection"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test throttle intervention (partial rejection)
        result, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=10.0,
            wallet_balance=2.0,
            accepted_history=[2.0] * 7,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        if (
            result.rejected_spend > 0
            and result.rejected_spend < result.requested_spend * 0.9
        ):
            assert result.intervention_type == "throttle"

        # Test shutdown intervention (90%+ rejection)
        result2, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=100.0,  # Very high request
            wallet_balance=2.0,
            accepted_history=[2.0] * 7,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        if result2.sgm_spend < 10.0:  # If we accepted less than 10% of SGM request
            assert result2.intervention_type == "shutdown"

    def test_manual_allowance(self):
        """Test manual allowance addition to wallet"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test with manual allowance
        result, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=10.0,
            wallet_balance=0.0,
            accepted_history=[3.0] * 7,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=50.0,  # Add $50 manual allowance
        )

        # Should be able to spend the requested amount with manual allowance
        assert result.accepted_spend == 10.0

    def test_billing_cycle_reset(self):
        """Test that reserved volume resets on new billing cycle"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Simulate billing day 1 with previous usage
        result, _, _ = SGMEngine.simulate_day(
            day_index=30,  # Day 30 (second month)
            billing_day=1,  # Back to billing day 1
            requested_spend=50.0,
            wallet_balance=0.0,
            accepted_history=[5.0] * 30,
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=100.0,  # Fully used last month
            manual_allowance=0,
        )

        # Should have full reserved volume available
        assert result.reserved_spend == 50.0
        assert result.cumulative_reserved_used == 50.0

    def test_zero_spend_days(self):
        """Test handling of days with zero spending"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # History with some zero-spend days
        history = [5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0]
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # Should still calculate limits based on the pattern
        assert limit > 0

    def test_high_growth_percentage(self):
        """Test with high growth percentage (50% per week)"""
        rule = SGMRule(
            name="High Growth",
            growth_percentage=50.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        history = [10.0] * 7
        limit, _, _ = SGMEngine.calculate_daily_spend_limit(history, rule)

        # With 50% weekly growth, daily limit should be significantly higher
        growth_factor = 1.50 ** (1.0 / 7)
        expected_exponential = 70 * growth_factor - 60
        expected_linear = 70 + 20.0 / 7 - 60

        assert limit == pytest.approx(
            max(expected_exponential, expected_linear), rel=1e-3
        )

    def test_disabled_rule(self):
        """Test behavior when SGM rule is disabled"""
        rule = SGMRule(
            name="Disabled Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=False,
        )

        # Even with disabled rule, engine should calculate limits
        # (The simulator would handle the disabled state)
        limit, _, _ = SGMEngine.calculate_daily_spend_limit([5.0] * 7, rule)
        assert limit > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
