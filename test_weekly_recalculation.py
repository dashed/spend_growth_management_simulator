#!/usr/bin/env python3
"""
Comprehensive test coverage for weekly recalculation functionality
Tests the fixed weekly recalculation feature
"""

import math

import pytest

from sgm_simulator import DayResult, ManualAllowance, SGMEngine, SGMRule, WalletConfig


class TestWeeklyRecalculation:
    """Test weekly recalculation functionality"""

    def test_weekly_recalc_disabled_behavior(self):
        """Test that when weekly recalc is disabled, normal PRFAQ algorithm is used"""
        rule = SGMRule(
            name="No recalc test",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
            weekly_recalc_enabled=False,  # DISABLED
        )

        # Create history with high initial period, then low period
        accepted_history = [15.0] * 7  # Initial baseline

        # Test day 7 (first PRFAQ day)
        daily_limit, last_recalc_day, baseline = SGMEngine.calculate_daily_spend_limit(
            accepted_history, rule, 7, 0
        )

        # Should use normal PRFAQ algorithm
        assert baseline is None, "Baseline should be None when recalc disabled"
        assert last_recalc_day == 0, "last_recalc_day should not change"

        # Calculate expected PRFAQ result
        recent_7 = sum(accepted_history[-7:])  # 105.0
        recent_6 = sum(accepted_history[-6:])  # 90.0
        growth_factor = (1 + 20.0 / 100) ** (1.0 / 7)
        expected_exponential = recent_7 * growth_factor - recent_6
        expected_linear = recent_7 + 20.0 / 7 - recent_6
        expected_limit = max(expected_exponential, expected_linear, 0)

        assert (
            abs(daily_limit - expected_limit) < 0.01
        ), "Should use normal PRFAQ when disabled"

    def test_weekly_recalc_timing(self):
        """Test that weekly recalculation occurs on the correct day"""
        rule = SGMRule(
            name="Recalc timing test",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=2,  # Wednesday (0=Monday, 1=Tuesday, 2=Wednesday...)
        )

        # Build up 7 days of history
        accepted_history = [10.0] * 7

        # Test various days to see when recalculation occurs
        test_cases = [
            # (day_index, expected_recalc, expected_last_recalc_day)
            (7, False, 0),  # Day 7 (Monday) - no recalc yet
            (8, False, 0),  # Day 8 (Tuesday) - not Wednesday
            (9, True, 9),  # Day 9 (Wednesday) - should recalc!
            (10, False, 9),  # Day 10 (Thursday) - already recalced this week
            (11, False, 9),  # Day 11 (Friday) - still same week
            (15, False, 9),  # Day 15 (Tuesday) - not Wednesday yet
            (16, True, 16),  # Day 16 (Wednesday) - next week recalc
        ]

        last_recalc_day = 0
        baseline = None

        for day_index, should_recalc, expected_last_recalc in test_cases:
            # Add more history for later days
            while len(accepted_history) <= day_index:
                accepted_history.append(12.0)

            daily_limit, new_last_recalc, new_baseline = (
                SGMEngine.calculate_daily_spend_limit(
                    accepted_history[: day_index + 1],
                    rule,
                    day_index,
                    last_recalc_day,
                    baseline,
                )
            )

            if should_recalc:
                assert (
                    new_last_recalc == expected_last_recalc
                ), f"Day {day_index}: Should update last_recalc_day to {expected_last_recalc}"
                assert (
                    new_baseline is not None
                ), f"Day {day_index}: Should calculate new baseline"
                expected_baseline = (
                    sum(accepted_history[day_index - 6 : day_index + 1]) / 7.0
                )
                assert (
                    abs(new_baseline - expected_baseline) < 0.01
                ), f"Day {day_index}: Baseline should be 7-day average"
            else:
                assert (
                    new_last_recalc == last_recalc_day
                ), f"Day {day_index}: Should not update last_recalc_day"
                assert (
                    new_baseline == baseline
                ), f"Day {day_index}: Baseline should not change"

            last_recalc_day = new_last_recalc
            baseline = new_baseline

    def test_weekly_recalc_calculation_prd_style(self):
        """Test that weekly recalc uses PRD-style calculation when baseline is available"""
        rule = SGMRule(
            name="PRD style test",
            growth_percentage=25.0,  # 25% growth
            min_growth_dollars=30.0,  # $30 minimum
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=0,  # Monday
        )

        # Create history leading to recalculation
        accepted_history = [20.0] * 14  # 14 days, last 7 average = 20.0

        # Day 14 (Monday) should trigger recalculation
        daily_limit, last_recalc_day, baseline = SGMEngine.calculate_daily_spend_limit(
            accepted_history, rule, 14, 0
        )

        # Verify baseline was calculated
        assert baseline == 20.0, "Baseline should be 7-day average (20.0)"
        assert last_recalc_day == 14, "Should update last_recalc_day"

        # Verify PRD-style calculation is used
        weekly_baseline = baseline * 7.0  # 140.0
        weekly_growth_limit = max(
            rule.min_growth_dollars,  # 30.0
            weekly_baseline
            * (1 + rule.growth_percentage / 100),  # 140.0 * 1.25 = 175.0
        )
        expected_daily_limit = weekly_growth_limit / 7.0  # 175.0 / 7 = 25.0

        assert (
            abs(daily_limit - expected_daily_limit) < 0.01
        ), f"Should use PRD calculation: expected {expected_daily_limit}, got {daily_limit}"

    def test_weekly_recalc_vs_prfaq_algorithm(self):
        """Test difference between weekly recalc and normal PRFAQ algorithm"""
        rule_no_recalc = SGMRule(
            name="No recalc",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
            weekly_recalc_enabled=False,
        )

        rule_with_recalc = SGMRule(
            name="With recalc",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=0,  # Monday
        )

        # Create history with changing pattern
        # Days 0-6: low usage, Days 7-13: high usage
        accepted_history = [5.0] * 7 + [25.0] * 7

        # Day 14 (Monday) - test both approaches

        # Without recalc (normal PRFAQ rolling window)
        limit_no_recalc, _, _ = SGMEngine.calculate_daily_spend_limit(
            accepted_history, rule_no_recalc, 14, 0
        )

        # With recalc (should establish baseline from recent period)
        limit_with_recalc, _, baseline = SGMEngine.calculate_daily_spend_limit(
            accepted_history, rule_with_recalc, 14, 0
        )

        # Verify baseline reflects recent period
        assert baseline == 25.0, "Baseline should be recent 7-day average"

        # The limits should be different (even if slightly)
        assert (
            abs(limit_with_recalc - limit_no_recalc) > 0.1
        ), "Weekly recalc should produce different limits than PRFAQ"

        # Weekly recalc should give more stable growth based on baseline
        expected_weekly_recalc = max(20.0, 25.0 * 7 * 1.2) / 7.0  # PRD formula
        assert (
            abs(limit_with_recalc - expected_weekly_recalc) < 0.01
        ), "Weekly recalc should use PRD-style calculation"

    def test_weekly_recalc_with_simulation(self):
        """Test weekly recalculation in full simulation context"""
        rule = SGMRule(
            name="Simulation recalc test",
            growth_percentage=30.0,
            min_growth_dollars=25.0,
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=1,  # Tuesday
        )

        wallet_config = WalletConfig(model="daily_limit_2x")

        # Simulate 21 days (3 weeks)
        accepted_history = []
        wallet_balance = 0.0
        last_recalc_day = 0
        baseline_spend = None
        results = []

        # Varying daily pattern: low -> medium -> high
        daily_pattern = [8.0] * 7 + [15.0] * 7 + [25.0] * 7

        for day_index, request in enumerate(daily_pattern):
            result, last_recalc_day, baseline_spend = SGMEngine.simulate_day(
                day_index=day_index,
                billing_day=day_index + 1,
                requested_spend=request,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                wallet_config=wallet_config,
                last_recalc_day=last_recalc_day,
                baseline_spend=baseline_spend,
            )

            results.append(result)
            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end

        # Check recalculation occurred on Tuesdays
        tuesday_days = [8, 15]  # Day 8 and 15 are Tuesdays (1st and 3rd weeks)

        # Day 8 (first Tuesday with 7+ days history) should trigger recalc
        assert last_recalc_day >= 8, "Should have recalculated by day 8"

        # Verify baseline adaptation over time
        assert baseline_spend is not None, "Should have baseline after recalculation"

        # Check that limits adapt to changing usage patterns
        week1_limits = [r.daily_spend_limit for r in results[0:7]]
        week3_limits = [r.daily_spend_limit for r in results[14:21]]

        avg_week1 = sum(week1_limits) / len(week1_limits)
        avg_week3 = sum(week3_limits) / len(week3_limits)

        assert (
            avg_week3 > avg_week1 * 1.5
        ), "Weekly recalc should adapt limits to higher usage pattern"

    def test_recalc_with_minimum_growth_constraint(self):
        """Test that weekly recalc respects minimum growth constraint"""
        rule = SGMRule(
            name="Min growth test",
            growth_percentage=10.0,  # Low percentage
            min_growth_dollars=50.0,  # High minimum
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=0,
        )

        # Low baseline that would give less than minimum
        accepted_history = [5.0] * 14  # Baseline = 5.0, 10% growth = 0.5

        daily_limit, _, baseline = SGMEngine.calculate_daily_spend_limit(
            accepted_history, rule, 14, 0
        )

        # Should use minimum growth instead of percentage
        weekly_baseline = baseline * 7.0  # 35.0
        weekly_with_percentage = weekly_baseline * 1.1  # 38.5
        weekly_minimum = rule.min_growth_dollars  # 50.0

        expected_weekly = max(weekly_minimum, weekly_with_percentage)  # 50.0
        expected_daily = expected_weekly / 7.0  # ~7.14

        assert (
            abs(daily_limit - expected_daily) < 0.01
        ), "Should use minimum growth when percentage growth is too low"

    def test_recalc_edge_case_first_week(self):
        """Test weekly recalc behavior during bootstrap period"""
        rule = SGMRule(
            name="Bootstrap recalc test",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=0,  # Monday
        )

        # Test days 0-6 (bootstrap period)
        for day in range(7):
            accepted_history = [10.0] * day if day > 0 else []

            daily_limit, last_recalc_day, baseline = (
                SGMEngine.calculate_daily_spend_limit(accepted_history, rule, day, 0)
            )

            # During bootstrap, no recalculation should occur
            assert last_recalc_day == 0, f"Day {day}: No recalc during bootstrap"
            assert baseline is None, f"Day {day}: No baseline during bootstrap"
            assert daily_limit > 0, f"Day {day}: Should have positive limit"

    def test_recalc_frequency_constraint(self):
        """Test that recalculation only happens once per week minimum"""
        rule = SGMRule(
            name="Frequency test",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=0,  # Monday
        )

        accepted_history = [10.0] * 14

        # First recalc on day 7 (Monday)
        _, last_recalc_day1, baseline1 = SGMEngine.calculate_daily_spend_limit(
            accepted_history[:8], rule, 7, 0
        )

        # Try to recalc again on day 8 (Tuesday) - should not happen
        _, last_recalc_day2, baseline2 = SGMEngine.calculate_daily_spend_limit(
            accepted_history[:9], rule, 8, last_recalc_day1, baseline1
        )

        # Try to recalc on day 14 (Monday again) - should happen
        _, last_recalc_day3, baseline3 = SGMEngine.calculate_daily_spend_limit(
            accepted_history, rule, 14, last_recalc_day2, baseline2
        )

        assert last_recalc_day1 == 7, "First recalc should occur on day 7"
        assert last_recalc_day2 == 7, "No recalc on day 8 (too soon)"
        assert last_recalc_day3 == 14, "Second recalc should occur on day 14"

        assert baseline1 is not None, "Should get baseline on first recalc"
        assert baseline2 == baseline1, "Baseline should not change on day 8"
        assert baseline3 is not None, "Should get new baseline on second recalc"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
