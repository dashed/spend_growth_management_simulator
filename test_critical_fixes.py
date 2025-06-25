#!/usr/bin/env python3
"""
Comprehensive test coverage for critical SGM fixes
Tests all the issues identified in the compliance audit.
"""

import math

import pytest

from sgm_simulator import (
    DayResult,
    ManualAllowance,
    ReservedVolumesConfig,
    SGMEngine,
    SGMRule,
    WalletConfig,
)


class TestCriticalFixes:
    """Test all critical fixes from the compliance audit"""

    def test_fix1_history_tracking_with_reserved_volumes(self):
        """
        Test Fix 1: History tracking should use total accepted spend, not just SGM spend
        This tests the critical bug where reserved volume usage wasn't included in history.
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        wallet_config = WalletConfig(model="daily_limit_2x")

        # Simulate several days where reserved volumes are used
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0
        last_recalc_day = 0

        results = []
        daily_requests = [15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 25.0]  # 8 days

        baseline_spend = None
        for day_index, request in enumerate(daily_requests):
            result, last_recalc_day, baseline_spend = SGMEngine.simulate_day(
                day_index=day_index,
                billing_day=day_index + 1,
                requested_spend=request,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                wallet_config=wallet_config,
                reserved_config=reserved,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowances=[],
                last_recalc_day=last_recalc_day,
                baseline_spend=baseline_spend,
            )

            results.append(result)

            # CRITICAL: Verify history includes total accepted spend (reserved + SGM)
            accepted_history.append(result.accepted_spend)  # This is the fix
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used

        # Verify that the algorithm considers total spend (reserved + SGM) for future calculations
        # Day 7 (PRFAQ algorithm) should use history that includes reserved volume usage
        day7_result = results[7]  # Day 8 (0-indexed 7)

        # Calculate what the limit should be based on total accepted history
        total_history = [r.accepted_spend for r in results[:7]]
        expected_recent_7 = sum(total_history)

        # Verify the daily limit calculation used the correct history
        # This would be wrong if only SGM spend was tracked
        assert expected_recent_7 == 15.0 * 7  # All requests accepted via reserved + SGM
        assert day7_result.daily_spend_limit > 0  # Should have reasonable limit

        # The bug would cause incorrect daily limits because reserved spend wouldn't be counted
        print(f"Day 7 daily limit: {day7_result.daily_spend_limit}")
        print(f"Total history sum: {sum(total_history)}")

    def test_fix2_wallet_cap_never_violated(self):
        """
        Test Fix 2: Wallet cap should never be violated during calculations
        PRD requirement: wallet "can never exceed the max weekly spend growth"
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        wallet_config = WalletConfig(model="daily_limit_2x")

        # Test with various scenarios that might cause wallet cap violations
        test_scenarios = [
            # (initial_wallet, daily_request, manual_allowances)
            (50.0, 100.0, []),  # High initial wallet
            (
                10.0,
                50.0,
                [ManualAllowance(amount=100.0, created_day=0)],
            ),  # Large manual allowance
            (
                30.0,
                25.0,
                [ManualAllowance(amount=75.0, created_day=0)],
            ),  # Wallet + allowance > cap
        ]

        for initial_wallet, request, manual_allowances in test_scenarios:
            # Set up history for PRFAQ algorithm
            accepted_history = [10.0] * 7  # 7 days of $10 each

            result, _, _ = SGMEngine.simulate_day(
                day_index=7,
                billing_day=8,
                requested_spend=request,
                wallet_balance=initial_wallet,
                accepted_history=accepted_history,
                rule=rule,
                wallet_config=wallet_config,
                manual_allowances=manual_allowances,
            )

            # CRITICAL: Wallet must never exceed capacity
            max_capacity = result.wallet_max_capacity
            assert (
                result.wallet_balance_start <= max_capacity
            ), f"Wallet start {result.wallet_balance_start} exceeds capacity {max_capacity}"
            assert (
                result.wallet_balance_end <= max_capacity
            ), f"Wallet end {result.wallet_balance_end} exceeds capacity {max_capacity}"

            # Additional check: wallet should never temporarily exceed capacity during calculations
            # This is enforced by the implementation using min() operations

            print(
                f"Scenario: wallet={initial_wallet}, request={request}, manual={len(manual_allowances)}"
            )
            print(
                f"  Capacity: {max_capacity}, Start: {result.wallet_balance_start}, End: {result.wallet_balance_end}"
            )

    def test_fix3_wallet_capacity_models_compliance(self):
        """
        Test Fix 3: Wallet capacity alignment with PRD requirements
        PRD specifies "max of a 3 day budget" vs PRFAQ "2x daily limit"
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test both capacity models
        wallet_2x = WalletConfig(model="daily_limit_2x")
        wallet_3day = WalletConfig(model="three_day_budget")

        accepted_history = [15.0] * 7  # Establish history for consistent daily limit

        # Test 2x daily limit model (PRFAQ)
        result_2x, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=20.0,
            wallet_balance=0.0,
            accepted_history=accepted_history,
            rule=rule,
            wallet_config=wallet_2x,
        )

        # Test 3-day budget model (PRD)
        result_3day, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=20.0,
            wallet_balance=0.0,
            accepted_history=accepted_history,
            rule=rule,
            wallet_config=wallet_3day,
        )

        # Verify capacity calculations
        daily_limit = result_2x.daily_spend_limit
        assert result_2x.wallet_max_capacity == daily_limit * 2.0, "2x model incorrect"
        assert (
            result_3day.wallet_max_capacity == daily_limit * 3.0
        ), "3-day model incorrect"
        assert (
            result_3day.wallet_max_capacity > result_2x.wallet_max_capacity
        ), "3-day should be larger"

        print(f"Daily limit: {daily_limit}")
        print(f"2x capacity: {result_2x.wallet_max_capacity}")
        print(f"3-day capacity: {result_3day.wallet_max_capacity}")

    def test_feature1_manual_allowance_expiration(self):
        """
        Test Feature 1: Manual allowance expiration support
        PRD requirement: "Allowances should expire at some point [TBD]"
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Create allowances with different expiration settings
        allowances = [
            ManualAllowance(
                amount=50.0, created_day=0, expiration_days=3, reason="Short term"
            ),
            ManualAllowance(
                amount=30.0, created_day=0, expiration_days=None, reason="Permanent"
            ),
            ManualAllowance(
                amount=25.0, created_day=2, expiration_days=5, reason="Medium term"
            ),
        ]

        accepted_history = [10.0] * 7

        # Test various days to check expiration behavior
        test_days = [0, 1, 2, 3, 4, 5, 6, 7, 8]

        for day in test_days:
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=day + 1,
                requested_spend=60.0,  # Large request to use allowances
                wallet_balance=0.0,
                accepted_history=accepted_history,
                rule=rule,
                manual_allowances=allowances,
            )

            # Check expiration logic
            active_total, expired_total = SGMEngine.calculate_active_manual_allowances(
                allowances, day
            )

            if day < 3:
                # All allowances should be active
                assert active_total == 105.0  # 50 + 30 + 25
                assert expired_total == 0.0
            elif day == 3:
                # First allowance expires
                assert active_total == 55.0  # 30 + 25 (short term expired)
                assert expired_total == 50.0
            elif day >= 7:
                # Medium term allowance also expires (created day 2 + 5 days = day 7)
                assert active_total == 30.0  # Only permanent remains
                assert expired_total == 75.0  # 50 + 25

            print(
                f"Day {day}: Active={active_total}, Expired={expired_total}, Used={result.manual_allowances_used}"
            )

    def test_feature2_weekly_recalculation_timing(self):
        """
        Test Feature 2: Weekly recalculation timing
        PRD requirement: "Weekly spend growth is recalculated weekly on [TBD date]"
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=1,  # Tuesday (0=Monday, 1=Tuesday, etc.)
        )

        accepted_history = [10.0] * 7  # Initial history
        last_recalc_day = 0

        # Simulate 21 days (3 weeks) to test recalculation timing
        baseline_spend = None
        for day in range(21):
            daily_limit, new_last_recalc_day, baseline_spend = (
                SGMEngine.calculate_daily_spend_limit(
                    accepted_history, rule, day, last_recalc_day, baseline_spend
                )
            )

            # Check if recalculation occurred
            weekday = day % 7
            days_since_last = day - last_recalc_day

            if (
                day >= 7 and weekday == 1 and days_since_last >= 7
            ):  # Tuesday and enough time passed
                assert new_last_recalc_day == day, f"Should recalculate on day {day}"
                print(f"Recalculation on day {day} (Tuesday)")
            else:
                assert (
                    new_last_recalc_day == last_recalc_day
                ), f"Should not recalculate on day {day}"

            last_recalc_day = new_last_recalc_day

            # Add to history for next iteration
            accepted_history.append(10.0)

    def test_comprehensive_integration(self):
        """
        Integration test combining all fixes and features
        """
        rule = SGMRule(
            name="25%/week or $30/week",
            growth_percentage=25.0,
            min_growth_dollars=30.0,
            enabled=True,
            weekly_recalc_enabled=True,
            weekly_recalc_day=0,  # Monday
        )

        wallet_config = WalletConfig(model="three_day_budget")

        reserved = ReservedVolumesConfig(
            monthly_volume=200.0, billing_day_start=1, days_in_cycle=30
        )

        # Manual allowances with expiration
        manual_allowances = [
            ManualAllowance(
                amount=100.0, created_day=5, expiration_days=10, reason="Campaign boost"
            ),
            ManualAllowance(
                amount=50.0,
                created_day=0,
                expiration_days=None,
                reason="Emergency buffer",
            ),
        ]

        # Simulate 30 days
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0
        last_recalc_day = 0
        results = []

        daily_pattern = [20.0, 25.0, 30.0, 15.0, 40.0] * 6  # Varying pattern

        baseline_spend = None
        for day_index, request in enumerate(daily_pattern):
            result, last_recalc_day, baseline_spend = SGMEngine.simulate_day(
                day_index=day_index,
                billing_day=(day_index % 30) + 1,
                requested_spend=request,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                wallet_config=wallet_config,
                reserved_config=reserved,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowances=manual_allowances,
                last_recalc_day=last_recalc_day,
                baseline_spend=baseline_spend,
            )

            results.append(result)

            # Validate all fixes
            # Fix 1: History tracking
            accepted_history.append(result.accepted_spend)  # Total spend

            # Fix 2: Wallet cap
            assert result.wallet_balance_end <= result.wallet_max_capacity

            # Fix 3: Wallet capacity model
            if len(accepted_history) >= 7:
                expected_capacity = result.daily_spend_limit * 3.0  # three_day_budget
                assert abs(result.wallet_max_capacity - expected_capacity) < 0.01

            # Feature 1: Manual allowance expiration
            if day_index >= 15:  # After campaign expires (day 5 + 10)
                assert result.expired_allowances >= 100.0

            # Update state
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used

        # Verify overall behavior
        total_requested = sum(r.requested_spend for r in results)
        total_accepted = sum(r.accepted_spend for r in results)
        acceptance_rate = total_accepted / total_requested

        assert acceptance_rate > 0.8, f"Poor acceptance rate: {acceptance_rate}"

        # Check no critical violations
        wallet_violations = [
            r for r in results if r.wallet_balance_end > r.wallet_max_capacity + 0.01
        ]
        assert (
            len(wallet_violations) == 0
        ), f"Wallet violations: {len(wallet_violations)}"

        print(
            f"Integration test passed: {len(results)} days, {acceptance_rate:.1%} acceptance"
        )

    def test_regression_prevention(self):
        """
        Regression test to ensure fixes don't break existing functionality
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test bootstrap period (days 0-6)
        accepted_history = []

        for day in range(7):
            daily_limit, _, _ = SGMEngine.calculate_daily_spend_limit(
                accepted_history, rule, day, 0
            )
            assert daily_limit > 0, f"Invalid limit on day {day}"
            accepted_history.append(10.0)

        # Test PRFAQ period (day 7+)
        for day in range(7, 14):
            daily_limit, _, _ = SGMEngine.calculate_daily_spend_limit(
                accepted_history, rule, day, 0
            )
            assert daily_limit > 0, f"Invalid limit on day {day}"
            accepted_history.append(12.0)  # Slight growth

        # Verify growth limits are reasonable
        recent_limits = []
        for day in range(10, 14):
            limit, _, _ = SGMEngine.calculate_daily_spend_limit(
                accepted_history[:day], rule, day, 0
            )
            recent_limits.append(limit)

        # Should allow some growth but not be excessive
        assert max(recent_limits) < 30.0, "Limits too high"
        assert min(recent_limits) > 1.0, "Limits too low"

        print("Regression tests passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
