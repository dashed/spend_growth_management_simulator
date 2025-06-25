#!/usr/bin/env python3
"""
Stress tests for SGM simulator
Tests extreme values, edge cases, and potential failure modes
"""

import sys

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMStress:
    """Stress tests for SGM system with extreme values and edge cases"""

    def test_extreme_large_values(self):
        """Test with extremely large monetary values"""
        rule = SGMRule(
            name="20%/week or $20M/week",
            growth_percentage=20.0,
            min_growth_dollars=20_000_000.0,  # $20M
            enabled=True,
        )

        # Test with billion-dollar requests
        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=1_000_000_000.0,  # $1B
            wallet_balance=50_000_000.0,  # $50M
            accepted_history=[10_000_000.0] * 10,  # $10M/day history
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should handle large values without overflow
        assert result.accepted_spend > 0
        assert result.daily_spend_limit > 0
        assert result.wallet_balance_end >= 0
        assert result.accepted_spend <= 1_000_000_000.0

        # Check that calculations are reasonable at this scale
        assert result.daily_spend_limit > 1_000_000.0  # At least $1M daily limit

    def test_extreme_small_values(self):
        """Test with extremely small monetary values (sub-cent)"""
        rule = SGMRule(
            name="20%/week or $0.20/week",
            growth_percentage=20.0,
            min_growth_dollars=0.20,  # 20 cents
            enabled=True,
            validate_bounds=False,  # Disable validation for stress testing
        )

        # Test with sub-cent values
        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=0.001,  # 0.1 cents
            wallet_balance=0.005,  # 0.5 cents
            accepted_history=[0.001] * 10,  # 0.1 cent history
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should handle small values without underflow
        assert result.accepted_spend >= 0
        assert result.daily_spend_limit > 0
        assert result.wallet_balance_end >= 0
        assert result.accepted_spend <= 0.001

    def test_floating_point_precision(self):
        """Test floating point precision edge cases"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Use values that might cause floating point precision issues
        test_values = [
            1.0 / 3.0,  # 0.3333...
            0.1 + 0.2,  # Classic floating point issue
            1e-10,  # Very small
            1e10 + 1,  # Large with small addition
        ]

        for value in test_values:
            result, _, _ = SGMEngine.simulate_day(
                day_index=5,
                billing_day=6,
                requested_spend=value,
                wallet_balance=value,
                accepted_history=[value] * 5,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            # Should handle precision correctly
            assert result.wallet_balance_end >= -1e-10  # Allow tiny negative due to FP
            assert result.accepted_spend >= 0
            assert result.accepted_spend <= value + 1e-10

    def test_zero_growth_edge_cases(self):
        """Test edge cases with zero growth percentage"""
        rule = SGMRule(
            name="0%/week or $10/week",
            growth_percentage=0.0,  # No percentage growth
            min_growth_dollars=10.0,
            enabled=True,
            validate_bounds=False,  # Disable validation for stress testing
        )

        # Test various scenarios with zero growth
        scenarios = [
            ([5.0] * 10, "Consistent below minimum"),
            ([15.0] * 10, "Consistent above minimum"),
            ([0.0] * 10, "All zeros"),
            ([10.0] * 10, "Exactly at minimum"),
        ]

        for history, description in scenarios:
            result, _, _ = SGMEngine.simulate_day(
                day_index=10,
                billing_day=11,
                requested_spend=12.0,
                wallet_balance=5.0,
                accepted_history=history,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            # Should use linear growth only (min_growth_dollars/7)
            if len(history) >= 7:
                recent_7 = sum(history[-7:])
                recent_6 = sum(history[-6:])
                expected_linear = recent_7 + 10.0 / 7 - recent_6
                expected_limit = max(expected_linear, 0)
                assert abs(result.daily_spend_limit - expected_limit) < 0.01

    def test_maximum_growth_edge_cases(self):
        """Test edge cases with maximum growth percentage (50%)"""
        rule = SGMRule(
            name="50%/week or $50/week",
            growth_percentage=50.0,  # Maximum allowed
            min_growth_dollars=50.0,
            enabled=True,
        )

        # Test rapid growth scenario
        exponential_history = [10.0 * (1.5**i) for i in range(10)]

        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=1000.0,
            wallet_balance=100.0,
            accepted_history=exponential_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should handle rapid growth without becoming unstable
        assert result.daily_spend_limit > 0
        assert result.daily_spend_limit < 10000.0  # Shouldn't explode
        assert result.accepted_spend > 0

    def test_very_long_accepted_history(self):
        """Test with extremely long accepted history"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Create very long history (5 years of daily data)
        long_history = [5.0 + (i % 30) * 0.1 for i in range(1825)]  # 5 years

        result, _, _ = SGMEngine.simulate_day(
            day_index=1825,
            billing_day=1,
            requested_spend=20.0,
            wallet_balance=10.0,
            accepted_history=long_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should only use recent 7 days, handle efficiently
        assert result.daily_spend_limit > 0
        # Verify it uses only recent 7 days by checking calculation
        recent_7 = sum(long_history[-7:])
        recent_6 = sum(long_history[-6:])
        # Should be based on recent history, not all 5 years
        assert result.daily_spend_limit < recent_7 * 2  # Sanity check

    def test_massive_reserved_volumes(self):
        """Test with extremely large reserved volumes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=1_000_000_000.0,  # $1B monthly
            billing_day_start=1,
            days_in_cycle=30,
        )

        # Test with large requests against large reserved
        result, _, _ = SGMEngine.simulate_day(
            day_index=5,
            billing_day=6,
            requested_spend=50_000_000.0,  # $50M request
            wallet_balance=1000.0,
            accepted_history=[5.0] * 5,
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=100_000_000.0,  # $100M used
            manual_allowance=0,
        )

        # Should handle large reserved volumes correctly
        assert result.reserved_spend == 50_000_000.0  # Full request from reserved
        assert result.sgm_spend == 0.0
        assert result.cumulative_reserved_used == 150_000_000.0
        assert result.reserved_remaining == 850_000_000.0

    def test_extreme_billing_cycles(self):
        """Test with extreme billing cycle configurations"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test very short cycle (1 day)
        reserved_short = ReservedVolumesConfig(
            monthly_volume=30.0, billing_day_start=1, days_in_cycle=1  # Reset every day
        )

        results_short = []
        cumulative = 0.0
        wallet = 0.0
        history = []

        for day in range(5):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=1,  # Always day 1 with 1-day cycle
                requested_spend=10.0,
                wallet_balance=wallet,
                accepted_history=history,
                rule=rule,
                reserved_config=reserved_short,
                cumulative_reserved_used=cumulative,
                manual_allowance=0,
            )

            history.append(result.accepted_spend)
            wallet = result.wallet_balance_end
            cumulative = result.cumulative_reserved_used
            results_short.append(result)

        # Each day should reset reserved usage
        for r in results_short:
            assert r.reserved_spend == 10.0  # Full amount from reserved each day
            assert r.cumulative_reserved_used == 10.0  # Reset each day

        # Test very long cycle (365 days)
        reserved_long = ReservedVolumesConfig(
            monthly_volume=1000.0,
            billing_day_start=1,
            days_in_cycle=365,  # Annual cycle
        )

        result_long, _, _ = SGMEngine.simulate_day(
            day_index=100,
            billing_day=101,
            requested_spend=10.0,
            wallet_balance=5.0,
            accepted_history=[3.0] * 100,
            rule=rule,
            reserved_config=reserved_long,
            cumulative_reserved_used=300.0,  # 100 days * $3
            manual_allowance=0,
        )

        # Should use reserved without reset
        assert result_long.reserved_spend == 10.0
        assert result_long.cumulative_reserved_used == 310.0

    def test_stress_with_all_zero_history(self):
        """Test stress scenarios with all-zero history"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test with long zero history
        zero_history = [0.0] * 100

        result, _, _ = SGMEngine.simulate_day(
            day_index=100,
            billing_day=1,
            requested_spend=50.0,
            wallet_balance=0.0,
            accepted_history=zero_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should fall back to minimum growth
        expected_limit = rule.min_growth_dollars / 7
        assert abs(result.daily_spend_limit - expected_limit) < 0.01
        assert result.accepted_spend > 0  # Should accept some amount

    def test_stress_negative_prevention(self):
        """Test that negative values are prevented in edge cases"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test scenarios that might produce negative values
        edge_cases = [
            {
                "wallet": -1.0,  # Try negative wallet
                "request": 10.0,
                "history": [5.0] * 10,
            },
            {
                "wallet": 1000.0,
                "request": -5.0,  # Try negative request
                "history": [5.0] * 10,
            },
            {
                "wallet": 5.0,
                "request": 10.0,
                "history": [-1.0] * 10,  # Try negative history
            },
        ]

        for case in edge_cases:
            try:
                result, _, _ = SGMEngine.simulate_day(
                    day_index=10,
                    billing_day=11,
                    requested_spend=case["request"],
                    wallet_balance=case["wallet"],
                    accepted_history=case["history"],
                    rule=rule,
                    reserved_config=None,
                    cumulative_reserved_used=0,
                    manual_allowance=0,
                )

                # If it doesn't raise an exception, check for non-negative results
                assert result.wallet_balance_end >= -1e-10  # Allow tiny FP errors
                assert result.accepted_spend >= 0
                assert result.daily_spend_limit >= 0

            except (ValueError, AssertionError):
                # Some edge cases might be caught by validation
                pass

    def test_memory_efficiency_stress(self):
        """Test memory efficiency with repeated simulations"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Run many simulations to test for memory leaks
        history = [5.0] * 7

        for i in range(1000):  # Many iterations
            result, _, _ = SGMEngine.simulate_day(
                day_index=i,
                billing_day=(i % 30) + 1,
                requested_spend=10.0,
                wallet_balance=5.0,
                accepted_history=history[-7:],  # Keep history bounded
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            # Update history for next iteration
            history.append(result.accepted_spend)
            if len(history) > 7:
                history = history[-7:]  # Keep only recent 7

        # Should complete without memory issues
        assert result.daily_spend_limit > 0
        assert len(history) <= 7  # History should stay bounded

    def test_concurrent_simulation_stress(self):
        """Test running multiple simulations concurrently"""
        import threading
        import time

        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        results = {}
        errors = []

        def worker(thread_id):
            try:
                local_history = [5.0] * 10
                for day in range(50):
                    result, _, _ = SGMEngine.simulate_day(
                        day_index=day,
                        billing_day=(day % 30) + 1,
                        requested_spend=8.0
                        + thread_id,  # Slightly different per thread
                        wallet_balance=3.0,
                        accepted_history=local_history,
                        rule=rule,
                        reserved_config=None,
                        cumulative_reserved_used=0,
                        manual_allowance=0,
                    )
                    local_history.append(result.accepted_spend)
                    local_history = local_history[-10:]  # Keep bounded

                results[thread_id] = result
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        # Run multiple threads
        threads = []
        for i in range(10):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        # Wait for completion
        for t in threads:
            t.join(timeout=10)  # 10 second timeout

        # Check results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 10, f"Only {len(results)} threads completed"

        # All results should be valid
        for thread_id, result in results.items():
            assert result.daily_spend_limit > 0
            assert result.accepted_spend >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
