#!/usr/bin/env python3
"""
Comprehensive wallet behavior tests for SGM simulator
Tests all aspects of wallet mechanics and edge cases
"""

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMWalletComprehensive:
    """Comprehensive tests for wallet behavior and mechanics"""

    def simulate_sequence(
        self, rule, spending_pattern, initial_wallet=0.0, reserved_config=None
    ):
        """Helper to simulate a sequence of days and return wallet progression"""
        accepted_history = []
        wallet_balance = initial_wallet
        cumulative_reserved = 0.0
        results = []

        for day, spend_amount in enumerate(spending_pattern):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=(day % 30) + 1,
                requested_spend=spend_amount,
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

    def test_wallet_cap_enforcement_various_limits(self):
        """Test wallet cap (2x daily limit) across various limit sizes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test with different spending patterns that create different limits
        test_cases = [
            ([1.0] * 10, "Low spending"),
            ([10.0] * 10, "Medium spending"),
            ([50.0] * 10, "High spending"),
            ([0.1] * 10, "Very low spending"),
        ]

        for pattern, description in test_cases:
            results = self.simulate_sequence(rule, pattern, initial_wallet=1000.0)

            # Check wallet cap is enforced
            for r in results[7:]:  # After bootstrap
                expected_cap = r.daily_spend_limit * 2
                assert r.wallet_balance_start <= expected_cap + 0.01  # Small tolerance
                # Wallet cap should be correctly applied based on current daily limit
                # (Large initial wallet gets reduced to match spending patterns)

    def test_wallet_accumulation_patterns(self):
        """Test different wallet accumulation patterns"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Pattern: spend less than limit to accumulate wallet
        spending_pattern = [2.0] * 20  # Consistently low spending
        results = self.simulate_sequence(rule, spending_pattern)

        # Wallet should accumulate during bootstrap
        bootstrap_wallets = [r.wallet_balance_end for r in results[:7]]
        assert max(bootstrap_wallets) > 0  # Some accumulation

        # After PRFAQ starts (day 7), check steady accumulation
        prfaq_wallets = [r.wallet_balance_end for r in results[7:15]]

        # Should reach and maintain cap
        daily_limit_after_bootstrap = results[10].daily_spend_limit
        expected_cap = daily_limit_after_bootstrap * 2

        # Later results should be at or near cap
        for r in results[15:]:
            assert abs(r.wallet_balance_start - expected_cap) < 2.5

    def test_wallet_depletion_and_recovery(self):
        """Test wallet depletion followed by recovery"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Phase 1: Build up wallet with low spending
        # Phase 2: Deplete with high spending
        # Phase 3: Recover with moderate spending
        pattern = (
            [1.0] * 10  # Build phase
            + [20.0] * 5  # Depletion phase
            + [3.0] * 10  # Recovery phase
        )

        results = self.simulate_sequence(rule, pattern)

        # Build phase - wallet should accumulate
        build_phase = results[:10]
        assert build_phase[-1].wallet_balance_end > build_phase[0].wallet_balance_end

        # Depletion phase - wallet should decrease
        depletion_phase = results[10:15]
        peak_wallet = build_phase[-1].wallet_balance_end
        depleted_wallet = depletion_phase[-1].wallet_balance_end
        assert depleted_wallet < peak_wallet

        # Recovery phase - wallet should increase again
        recovery_phase = results[15:]
        final_wallet = recovery_phase[-1].wallet_balance_end
        assert final_wallet > depleted_wallet

    def test_wallet_with_zero_spending_days(self):
        """Test wallet behavior with zero spending days"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Pattern with zero spending days
        pattern = [5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0]
        results = self.simulate_sequence(rule, pattern)

        zero_days = [results[i] for i in [1, 3, 5, 7, 9]]
        spend_days = [results[i] for i in [0, 2, 4, 6, 8]]

        # Zero spending days should accumulate daily limit (subject to wallet cap)
        for r in zero_days:
            if r.day_index > 0:  # Skip day 0
                prev_result = results[r.day_index - 1]
                wallet_increase = r.wallet_balance_end - prev_result.wallet_balance_end

                # Calculate expected increase considering wallet cap
                uncapped_end = prev_result.wallet_balance_end + r.daily_spend_limit
                wallet_cap = r.daily_spend_limit * 2
                expected_end = min(uncapped_end, wallet_cap)
                expected_increase = expected_end - prev_result.wallet_balance_end

                assert abs(wallet_increase - expected_increase) < 0.01

        # Spending days should decrease wallet
        for i, r in enumerate(spend_days):
            if i > 0:  # Skip first day
                assert (
                    r.wallet_balance_end < r.wallet_balance_start + r.daily_spend_limit
                )

    def test_wallet_precision_with_fractional_amounts(self):
        """Test wallet calculations with fractional amounts"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Use fractional amounts
        pattern = [2.501, 3.333, 1.999, 4.001, 2.750] * 4
        results = self.simulate_sequence(rule, pattern)

        # Check precision in wallet calculations
        for r in results:
            # Wallet should never be negative
            assert r.wallet_balance_end >= -0.001  # Small tolerance for floating point
            assert r.wallet_balance_start >= -0.001

            # Balance calculation should be precise and properly capped
            # The wallet balance should be properly capped at 2x daily limit
            assert (
                r.wallet_balance_end <= r.daily_spend_limit * 2 + 0.001
            )  # Allow small tolerance

    def test_wallet_with_manual_allowances(self):
        """Test wallet behavior with various manual allowance amounts"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Build up some wallet first
        buildup_results = self.simulate_sequence(rule, [2.0] * 10)
        initial_wallet = buildup_results[-1].wallet_balance_end

        # Test large manual allowance
        large_manual, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=100.0,
            wallet_balance=initial_wallet,
            accepted_history=[2.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=90.0,
        )

        # Should be able to spend the full amount (allow small floating point tolerance)
        assert abs(large_manual.accepted_spend - 100.0) < 0.5
        assert (
            large_manual.rejected_spend < 0.5
        )  # Allow small rejection due to precision

        # Test small manual allowance
        small_manual, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=50.0,
            wallet_balance=initial_wallet,
            accepted_history=[2.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=5.0,
        )

        # Should spend more than without manual allowance
        no_manual, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=50.0,
            wallet_balance=initial_wallet,
            accepted_history=[2.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0.0,
        )

        assert small_manual.accepted_spend > no_manual.accepted_spend

    def test_wallet_extreme_values(self):
        """Test wallet with extreme values"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test with very large initial wallet
        large_wallet_result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=10.0,
            wallet_balance=1000000.0,  # $1M wallet
            accepted_history=[5.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should be capped at 2x daily limit
        expected_cap = large_wallet_result.daily_spend_limit * 2
        assert large_wallet_result.wallet_balance_start == expected_cap

        # Test with very small amounts
        small_result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=0.01,  # 1 cent
            wallet_balance=0.005,  # Half cent
            accepted_history=[0.01] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Should handle small amounts correctly
        assert small_result.wallet_balance_end >= 0
        assert small_result.accepted_spend <= 0.01

    def test_wallet_cross_billing_cycles(self):
        """Test wallet behavior across billing cycles with reserved volumes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=60.0,
            billing_day_start=1,
            days_in_cycle=20,  # Shorter for testing
        )

        # Simulate across multiple billing cycles
        pattern = [4.0] * 50  # Will cross 2-3 billing cycles
        results = self.simulate_sequence(rule, pattern, reserved_config=reserved)

        # Find billing cycle boundaries
        cycle_boundaries = [
            i for i, r in enumerate(results) if r.billing_day == 1 and i > 0
        ]

        # Wallet should persist across billing cycles
        for boundary in cycle_boundaries:
            prev_result = results[boundary - 1]
            boundary_result = results[boundary]

            # Wallet should carry over (not reset)
            # Only reserved volume usage resets
            assert boundary_result.wallet_balance_start >= 0
            # Wallet may change due to cap adjustments, but shouldn't jump dramatically
            wallet_change = abs(
                boundary_result.wallet_balance_start - prev_result.wallet_balance_end
            )
            assert wallet_change < prev_result.daily_spend_limit  # Reasonable change

    def test_wallet_bootstrap_to_prfaq_transition(self):
        """Test detailed wallet behavior during bootstrap to PRFAQ transition"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Use spending pattern that allows wallet accumulation during bootstrap
        pattern = [1.5] * 15  # Low spending to accumulate wallet
        results = self.simulate_sequence(rule, pattern)

        # Bootstrap phase (days 0-6)
        bootstrap_phase = results[:7]
        bootstrap_wallet_growth = [r.wallet_balance_end for r in bootstrap_phase]

        # Should see wallet accumulation during bootstrap
        assert bootstrap_wallet_growth[-1] > bootstrap_wallet_growth[0]

        # Day 7 transition
        transition_day = results[7]
        day6_wallet = results[6].wallet_balance_end

        # Wallet might be capped at transition
        daily_limit_day7 = transition_day.daily_spend_limit
        cap_day7 = daily_limit_day7 * 2

        # If previous wallet exceeded new cap, it should be capped
        if day6_wallet > cap_day7:
            assert transition_day.wallet_balance_start == cap_day7
        else:
            assert transition_day.wallet_balance_start == day6_wallet

        # Post-transition should maintain cap
        post_transition = results[8:]
        for r in post_transition:
            cap = r.daily_spend_limit * 2
            assert r.wallet_balance_start <= cap + 0.01

    def test_wallet_intervention_relationships(self):
        """Test relationship between wallet levels and intervention types"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Build up wallet with moderate spending
        buildup_pattern = [3.0] * 15
        buildup_results = self.simulate_sequence(rule, buildup_pattern)
        final_wallet = buildup_results[-1].wallet_balance_end
        final_history = [r.accepted_spend for r in buildup_results]

        # Test different request sizes with same wallet state
        test_requests = [5.0, 15.0, 50.0, 100.0]

        for request in test_requests:
            result, _, _ = SGMEngine.simulate_day(
                day_index=15,
                billing_day=16,
                requested_spend=request,
                wallet_balance=final_wallet,
                accepted_history=final_history,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            # Check intervention logic
            if result.sgm_spend > 0:  # Some SGM spending occurred
                sgm_requested = request  # No reserved volumes in this test
                sgm_rejection_rate = (sgm_requested - result.sgm_spend) / sgm_requested

                if sgm_rejection_rate >= 0.9:
                    assert result.intervention_type == "shutdown"
                elif sgm_rejection_rate > 0:
                    assert result.intervention_type == "throttle"
                else:
                    assert result.intervention_type == "none"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
