#!/usr/bin/env python3
"""
Comprehensive tests for reserved volumes functionality
Tests all aspects of reserved volume behavior and edge cases
"""

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMReservedVolumes:
    """Comprehensive tests for reserved volumes functionality"""

    def simulate_days(
        self, days, daily_requests, rule, reserved_config, start_cumulative=0
    ):
        """Helper to simulate multiple days with reserved volumes"""
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = start_cumulative
        results = []

        for day in range(days):
            # Calculate billing day considering the billing_day_start
            billing_day = (
                (day + reserved_config.billing_day_start - 1)
                % reserved_config.days_in_cycle
            ) + 1

            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=billing_day,
                requested_spend=(
                    daily_requests[day]
                    if isinstance(daily_requests, list)
                    else daily_requests
                ),
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=reserved_config,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowance=0,
            )

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used
            results.append(result)

        return results

    def test_zero_reserved_volume(self):
        """Test with zero reserved volume"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=0.0, billing_day_start=1, days_in_cycle=30  # Zero reserved
        )

        results = self.simulate_days(10, 5.0, rule, reserved)

        # All spending should go through SGM
        for r in results:
            assert r.reserved_spend == 0.0
            assert r.sgm_spend > 0.0
            assert r.cumulative_reserved_used == 0.0

    def test_very_large_reserved_volume(self):
        """Test with very large reserved volume"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100000.0, billing_day_start=1, days_in_cycle=30  # Very large
        )

        results = self.simulate_days(30, 1000.0, rule, reserved)  # $1000/day requests

        # All spending should go through reserved
        for r in results:
            assert r.reserved_spend == 1000.0
            assert r.sgm_spend == 0.0
            assert r.rejected_spend == 0.0

        # Should still have reserved left
        assert results[-1].reserved_remaining >= 70000.0

    def test_different_billing_day_starts(self):
        """Test reserved volumes with different billing day starts"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test billing starting on day 15
        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=15, days_in_cycle=30
        )

        # Simulate 45 days to cross billing cycles
        results = self.simulate_days(45, 5.0, rule, reserved)

        # Find billing day 1 occurrences (should reset reserved when cycle wraps)
        reset_days = [r for r in results if r.billing_day == 1 and r.day_index > 0]

        # Check that cumulative resets on billing day 1 (when cycle wraps around)
        for r in reset_days:
            day_before = results[r.day_index - 1]
            # Should start new billing cycle with reset cumulative usage
            assert r.cumulative_reserved_used < day_before.cumulative_reserved_used

    def test_different_cycle_lengths(self):
        """Test different billing cycle lengths"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test 28-day cycle (February)
        reserved_28 = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=28
        )

        results_28 = self.simulate_days(56, 4.0, rule, reserved_28)  # 2 cycles

        # Check that cycle resets after 28 days
        cycle1_end = results_28[27]  # Day 28
        cycle2_start = results_28[28]  # Day 1 of next cycle

        assert cycle1_end.billing_day == 28
        assert cycle2_start.billing_day == 1
        assert (
            cycle2_start.cumulative_reserved_used < cycle1_end.cumulative_reserved_used
        )

    def test_reserved_volume_exact_exhaustion(self):
        """Test exact exhaustion of reserved volume"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Use exactly $5/day for 20 days = $100
        results = self.simulate_days(21, 5.0, rule, reserved)

        # First 20 days should use reserved
        for i in range(20):
            assert results[i].reserved_spend == 5.0
            assert results[i].sgm_spend == 0.0

        # Day 21 should be exactly at exhaustion
        assert results[19].cumulative_reserved_used == 100.0
        assert results[20].reserved_spend == 0.0  # No more reserved
        assert results[20].sgm_spend > 0.0  # SGM takes over

    def test_partial_reserved_usage(self):
        """Test when daily request is larger than remaining reserved"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=50.0, billing_day_start=1, days_in_cycle=30
        )

        # Use $6/day when only $50 total reserved
        results = self.simulate_days(15, 6.0, rule, reserved)

        # Find the crossover point
        crossover_day = None
        for i, r in enumerate(results):
            if r.reserved_spend < 6.0 and r.reserved_spend > 0:
                crossover_day = i
                break

        assert crossover_day is not None  # Should find partial usage day

        crossover_result = results[crossover_day]
        assert 0 < crossover_result.reserved_spend < 6.0
        assert crossover_result.sgm_spend > 0
        assert crossover_result.reserved_spend + crossover_result.sgm_spend <= 6.0

    def test_multiple_billing_cycles(self):
        """Test behavior across multiple billing cycles"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=60.0, billing_day_start=1, days_in_cycle=30
        )

        # Simulate 3 full billing cycles
        results = self.simulate_days(90, 3.0, rule, reserved)

        # Check each cycle
        cycle_starts = [0, 30, 60]
        for cycle_start in cycle_starts:
            cycle_results = results[
                cycle_start : cycle_start + 20
            ]  # First 20 days of cycle

            # Each cycle should start with full reserved volume
            assert cycle_results[0].cumulative_reserved_used in [
                0.0,
                3.0,
            ]  # 0 or first day

            # Should use reserved for 20 days (20 * $3 = $60)
            for i, r in enumerate(cycle_results):
                assert r.reserved_spend == 3.0
                assert r.cumulative_reserved_used == (i + 1) * 3.0

    def test_reserved_with_manual_allowance(self):
        """Test reserved volumes combined with manual allowances"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=50.0, billing_day_start=1, days_in_cycle=30
        )

        # Simulate with manual allowance
        result, _, _ = SGMEngine.simulate_day(
            day_index=0,
            billing_day=1,
            requested_spend=100.0,  # Large request
            wallet_balance=0.0,
            accepted_history=[],
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=0,
            manual_allowance=60.0,
        )

        # Should use reserved first, then SGM + manual
        assert result.reserved_spend <= 50.0  # Limited by reserved volume
        assert result.sgm_spend > 0  # SGM handles remainder
        assert result.accepted_spend > 50.0  # More than just reserved
        assert result.accepted_spend <= 100.0  # Not more than requested

    def test_reserved_volume_reporting(self):
        """Test accurate reporting of reserved volume usage"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        results = self.simulate_days(25, 4.5, rule, reserved)

        # Track cumulative usage accuracy
        for i, r in enumerate(results):
            # Once reserved is exhausted, cumulative should not exceed 100.0
            expected_cumulative = min((i + 1) * 4.5, 100.0)
            assert abs(r.cumulative_reserved_used - expected_cumulative) < 0.01

            expected_remaining = 100.0 - expected_cumulative
            assert abs(r.reserved_remaining - expected_remaining) < 0.01

    def test_reserved_edge_case_fractional_cents(self):
        """Test reserved volumes with fractional cent amounts"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=10.001,  # Fractional volume
            billing_day_start=1,
            days_in_cycle=30,
        )

        # Use fractional daily amounts
        daily_amount = 1.0001
        results = self.simulate_days(12, daily_amount, rule, reserved)

        # Should handle fractional amounts correctly
        reserved_exhausted = False
        for r in results:
            if r.reserved_spend < daily_amount:
                reserved_exhausted = True

            # Check precision
            assert r.reserved_spend >= 0.0
            assert r.cumulative_reserved_used <= 10.001 + 0.0001  # Small tolerance

        assert reserved_exhausted  # Should exhaust before 12 days

    def test_reserved_with_zero_requests(self):
        """Test reserved volumes when there are zero spending requests"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Mix of zero and non-zero requests
        pattern = [0.0, 0.0, 5.0, 0.0, 10.0, 0.0, 0.0, 15.0]
        results = self.simulate_days(8, pattern, rule, reserved)

        # Zero request days should not consume reserved
        zero_days = [results[i] for i in [0, 1, 3, 5, 6]]
        for r in zero_days:
            assert r.reserved_spend == 0.0
            assert r.sgm_spend == 0.0

        # Non-zero days should consume reserved
        nonzero_days = [results[i] for i in [2, 4, 7]]
        for r in nonzero_days:
            assert r.reserved_spend > 0.0

        # Cumulative should only increase on spending days
        assert results[7].cumulative_reserved_used == 5.0 + 10.0 + 15.0

    def test_reserved_billing_cycle_boundary(self):
        """Test reserved volume behavior at billing cycle boundaries"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=90.0,
            billing_day_start=28,  # Start near end of 30-day cycle
            days_in_cycle=30,
        )

        # Simulate across boundary
        results = self.simulate_days(10, 10.0, rule, reserved)

        # Days should be: 28, 29, 30, 1, 2, 3, 4, 5, 6, 7
        expected_billing_days = [28, 29, 30, 1, 2, 3, 4, 5, 6, 7]
        for i, r in enumerate(results):
            assert r.billing_day == expected_billing_days[i]

        # Reset should happen on day index 3 (billing day 1)
        assert results[2].billing_day == 30  # Last day of cycle
        assert results[3].billing_day == 1  # First day of new cycle

        # Cumulative should reset
        assert results[3].cumulative_reserved_used == 10.0  # Only current day
        assert results[2].cumulative_reserved_used == 30.0  # Three days accumulated

    def test_reserved_exhaustion_recovery(self):
        """Test behavior after reserved volume is exhausted and replenished"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=50.0,
            billing_day_start=1,
            days_in_cycle=20,  # Shorter cycle for testing
        )

        # Exhaust reserved in first cycle, then get new allocation
        results = self.simulate_days(35, 5.0, rule, reserved)

        # First 10 days should use reserved (10 * $5 = $50)
        first_cycle_reserved = results[:10]
        for r in first_cycle_reserved:
            assert r.reserved_spend == 5.0
            assert r.sgm_spend == 0.0

        # Days 11-20 should use SGM only (reserved exhausted)
        middle_period = results[10:20]
        for r in middle_period:
            assert r.reserved_spend == 0.0
            assert r.sgm_spend > 0.0

        # Days 21+ should use reserved again (new cycle)
        second_cycle = results[20:30]
        for r in second_cycle:
            assert r.reserved_spend == 5.0
            assert r.sgm_spend == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
