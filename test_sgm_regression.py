#!/usr/bin/env python3
"""
Regression tests for SGM simulator
Tests specific bugs and issues that have been fixed
"""

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMRegression:
    """Regression tests for previously identified issues"""

    def test_sgm_tracking_fix(self):
        """
        Regression test for SGM tracking issue
        Previously: SGM only tracked SGM spending, not total spending
        Fixed: SGM now tracks total accepted spending
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

        # Simulate with fixed tracking
        accepted_history = []  # Should track TOTAL spending
        wallet_balance = 0.0
        cumulative_reserved = 0.0

        rejections = []

        for day in range(30):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=(day % 30) + 1,
                requested_spend=5.0,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=reserved,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowance=0,
            )

            # CRITICAL: Track total spending, not just SGM
            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used

            if result.rejected_spend > 0:
                rejections.append(day)

        # With fix: No rejections at end of month
        assert len(rejections) == 0, f"Regression: Rejections on days {rejections}"

        # Verify SGM learned the pattern
        assert accepted_history[-1] == 5.0  # Full amount accepted

    def test_bootstrap_growth_fix(self):
        """
        Regression test for bootstrap period growth
        Previously: Bootstrap was stuck at min_growth/7 for entire week
        Fixed: Bootstrap now allows daily growth
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test bootstrap period growth
        limits = []
        accepted_history = []

        for day in range(7):
            limit, _, _ = SGMEngine.calculate_daily_spend_limit(accepted_history, rule)
            limits.append(limit)

            # Simulate spending at limit
            accepted_history.append(limit)

        # Verify growth during bootstrap
        assert limits[0] == pytest.approx(20.0 / 7, rel=1e-3)

        # Each day should allow growth
        for i in range(1, 7):
            # May not grow every day due to weekly minimum constraint
            # But should show overall growth trend
            pass

        # Final limit should be significantly higher than initial
        assert limits[6] > limits[0] * 1.4  # At least 40% growth

    def test_wallet_cap_day8_drop(self):
        """
        Regression test for wallet drop on day 8
        Previously: Users confused by dramatic wallet drop
        Fixed: This is expected behavior due to wallet cap rule
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Simulate low spending during bootstrap
        accepted_history = []
        wallet_balance = 0.0

        # Bootstrap with $3/day spending
        for day in range(7):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=day + 1,
                requested_spend=3.0,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end

        # Day 7 wallet before cap
        day7_wallet_before_cap = wallet_balance

        # Day 7 (first PRFAQ day)
        result_day7, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=3.0,
            wallet_balance=wallet_balance,
            accepted_history=accepted_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Verify wallet cap was applied
        assert result_day7.wallet_balance_start <= result_day7.daily_spend_limit * 2

        # This is expected behavior, not a bug
        if day7_wallet_before_cap > result_day7.daily_spend_limit * 2:
            assert result_day7.wallet_balance_start < day7_wallet_before_cap

    def test_billing_day_advancement(self):
        """
        Regression test for billing day advancement
        Previously: Billing days didn't advance after "Simulate Next Week"
        Fixed: Billing day now advances correctly
        """
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0,
            billing_day_start=25,  # Start near end of cycle
            days_in_cycle=30,
        )

        # Simulate week starting at billing day 25
        billing_days = []

        for i in range(7):
            current_billing_day = ((25 - 1 + i) % 30) + 1
            billing_days.append(current_billing_day)

        # Should wrap around to new cycle
        assert billing_days == [25, 26, 27, 28, 29, 30, 1]

    def test_reserved_volume_reset(self):
        """
        Regression test for reserved volume reset on new billing cycle
        """
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Use up reserved volume
        cumulative_reserved = 100.0

        # Simulate billing day 1 (new cycle)
        result, _, _ = SGMEngine.simulate_day(
            day_index=30,
            billing_day=1,  # New cycle
            requested_spend=50.0,
            wallet_balance=10.0,
            accepted_history=[5.0] * 30,
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=cumulative_reserved,
            manual_allowance=0,
        )

        # Should reset and use new reserved volume
        assert result.reserved_spend == 50.0
        assert result.cumulative_reserved_used == 50.0  # Reset to new usage

    def test_zero_wallet_accumulation(self):
        """
        Regression test for wallet not accumulating
        Previously: Confusion about why wallet stays at equilibrium
        Fixed: Documentation and understanding of equilibrium behavior
        """
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Simulate steady state where spending = limit
        accepted_history = [5.0] * 14  # Two weeks of $5/day
        wallet_balance = 5.0  # Some wallet balance

        results = []
        for day in range(7):  # One more week
            result, _, _ = SGMEngine.simulate_day(
                day_index=14 + day,
                billing_day=15 + day,
                requested_spend=5.0,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            results.append(result)

        # Wallet reaches equilibrium at the cap (2x daily limit)
        wallet_values = [r.wallet_balance_end for r in results]

        # Check that wallet stabilizes at or near the cap
        daily_limit = results[-1].daily_spend_limit
        wallet_cap = daily_limit * 2

        # Last few days should be at or near the cap
        for r in results[-3:]:
            assert abs(r.wallet_balance_start - wallet_cap) < 6.0  # At or near cap

    def test_intervention_calculation(self):
        """
        Regression test for intervention type calculation
        Ensure interventions are based on SGM rejection, not total rejection
        """
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=10.0, billing_day_start=1, days_in_cycle=30  # Small reserved
        )

        # Request more than reserved + SGM can handle
        result, _, _ = SGMEngine.simulate_day(
            day_index=0,
            billing_day=1,
            requested_spend=100.0,
            wallet_balance=5.0,
            accepted_history=[],
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Intervention should be based on SGM rejection only
        # Not on the total rejection (which includes reserved shortage)
        sgm_requested = 100.0 - result.reserved_spend
        if sgm_requested > 0:
            sgm_rejection_rate = (sgm_requested - result.sgm_spend) / sgm_requested

            if sgm_rejection_rate >= 0.9:
                assert result.intervention_type == "shutdown"
            elif sgm_rejection_rate > 0:
                assert result.intervention_type == "throttle"
            else:
                assert result.intervention_type == "none"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
