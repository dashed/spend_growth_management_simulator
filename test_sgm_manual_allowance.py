#!/usr/bin/env python3
"""
Comprehensive tests for manual allowance functionality
Tests all aspects of manual allowance behavior and interactions
"""

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMManualAllowance:
    """Comprehensive tests for manual allowance functionality"""

    def test_basic_manual_allowance(self):
        """Test basic manual allowance functionality"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Build some history first
        accepted_history = [3.0] * 10

        # Test without manual allowance
        without_manual, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=50.0,
            wallet_balance=5.0,
            accepted_history=accepted_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0.0,
        )

        # Test with manual allowance
        with_manual, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=50.0,
            wallet_balance=5.0,
            accepted_history=accepted_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=30.0,
        )

        # Manual allowance should increase accepted spend
        assert with_manual.accepted_spend > without_manual.accepted_spend
        assert with_manual.rejected_spend < without_manual.rejected_spend

    def test_manual_allowance_exact_amounts(self):
        """Test manual allowance with exact request amounts"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        accepted_history = [2.0] * 10

        # Test where manual allowance exactly covers the gap
        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=20.0,
            wallet_balance=2.0,
            accepted_history=accepted_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=15.0,  # Should cover most of the gap
        )

        # Should accept most or all of the request
        assert result.accepted_spend >= 17.0  # wallet + daily_limit + some manual
        assert result.rejected_spend <= 3.0

    def test_manual_allowance_larger_than_request(self):
        """Test manual allowance larger than the request"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        accepted_history = [5.0] * 10

        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=10.0,
            wallet_balance=3.0,
            accepted_history=accepted_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=50.0,  # Much larger than needed
        )

        # Should accept full request, no rejections
        assert result.accepted_spend == 10.0
        assert result.rejected_spend == 0.0

        # Per PRD: Unused manual allowances do NOT roll back into wallet
        # Wallet should only be affected by daily limit addition and SGM spending
        expected_wallet = min(
            3.0 + result.daily_spend_limit - result.sgm_spend,
            result.daily_spend_limit * 2,
        )
        assert abs(result.wallet_balance_end - expected_wallet) < 0.01

    def test_manual_allowance_with_reserved_volumes(self):
        """Test manual allowance interaction with reserved volumes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=30.0, billing_day_start=1, days_in_cycle=30
        )

        # Test with reserved volume available
        result, _, _ = SGMEngine.simulate_day(
            day_index=5,
            billing_day=6,
            requested_spend=100.0,
            wallet_balance=5.0,
            accepted_history=[3.0] * 5,
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=15.0,  # $15 already used
            manual_allowance=70.0,
        )

        # Should use reserved first, then SGM + manual
        assert result.reserved_spend == 15.0  # Remaining reserved (30-15)
        assert result.sgm_spend > 0  # SGM + manual for remainder
        # Total available: 15.0 reserved + 78.6 SGM = 93.6
        # Request of 100.0 exceeds available capacity, so some rejection expected
        assert result.accepted_spend >= 90.0  # Most should be accepted
        assert result.rejected_spend < 10.0  # Small amount rejected

    def test_manual_allowance_with_exhausted_reserved(self):
        """Test manual allowance when reserved volumes are exhausted"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=50.0, billing_day_start=1, days_in_cycle=30
        )

        # Test with reserved volume exhausted
        result, _, _ = SGMEngine.simulate_day(
            day_index=15,
            billing_day=16,
            requested_spend=75.0,
            wallet_balance=10.0,
            accepted_history=[4.0] * 15,
            rule=rule,
            reserved_config=reserved,
            cumulative_reserved_used=50.0,  # Fully exhausted
            manual_allowance=60.0,
        )

        # All should go through SGM + manual
        assert result.reserved_spend == 0.0
        assert result.sgm_spend > 50.0  # wallet + daily_limit + manual
        assert result.accepted_spend >= 70.0  # Most should be accepted

    def test_zero_manual_allowance(self):
        """Test explicitly zero manual allowance"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=25.0,
            wallet_balance=8.0,
            accepted_history=[3.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0.0,
        )

        # Should behave exactly like no manual allowance
        # Available = min(wallet + daily_limit, wallet_cap) due to wallet capacity constraints
        available = min(
            result.wallet_balance_start + result.daily_spend_limit,
            result.wallet_max_capacity,
        )
        assert result.accepted_spend == min(25.0, available)

    def test_fractional_manual_allowance(self):
        """Test manual allowance with fractional amounts"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=12.567,
            wallet_balance=3.123,
            accepted_history=[2.5] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=7.891,
        )

        # Should handle fractional precision correctly
        assert result.accepted_spend <= 12.567 + 0.001
        assert result.wallet_balance_end >= -0.001  # No negative wallet

        # Check precision of calculations
        available = result.wallet_balance_start + result.daily_spend_limit + 7.891
        expected_accepted = min(12.567, available)
        assert abs(result.accepted_spend - expected_accepted) < 0.001

    def test_very_large_manual_allowance(self):
        """Test with extremely large manual allowance"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=100.0,
            wallet_balance=5.0,
            accepted_history=[3.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=1000000.0,  # $1M manual allowance
        )

        # Should accept full request without issues
        assert result.accepted_spend == 100.0
        assert result.rejected_spend == 0.0

        # Wallet should be capped appropriately
        expected_cap = result.daily_spend_limit * 2
        assert result.wallet_balance_end <= expected_cap + 0.01

    def test_manual_allowance_intervention_prevention(self):
        """Test that manual allowance prevents interventions"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test scenario that would normally cause shutdown
        without_manual, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=500.0,  # Very large request
            wallet_balance=2.0,
            accepted_history=[3.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0.0,
        )

        # Should have intervention
        assert without_manual.intervention_type == "shutdown"

        # Now with manual allowance
        with_manual, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=500.0,
            wallet_balance=2.0,
            accepted_history=[3.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=495.0,  # Cover almost all
        )

        # Should have no or reduced intervention
        assert with_manual.intervention_type in ["none", "throttle"]
        assert with_manual.accepted_spend > without_manual.accepted_spend

    def test_manual_allowance_multiple_days(self):
        """Test manual allowance applied over multiple days"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Simulate sequence with varying manual allowances
        accepted_history = []
        wallet_balance = 0.0
        manual_allowances = [0.0, 10.0, 20.0, 0.0, 50.0, 0.0, 0.0]
        requests = [15.0] * 7

        results = []
        for day in range(7):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=day + 1,
                requested_spend=requests[day],
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=manual_allowances[day],
            )

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            results.append(result)

        # Days with manual allowance should have higher acceptance
        day_1_no_manual = results[0]
        day_2_with_manual = results[1]
        day_5_large_manual = results[4]

        assert day_2_with_manual.accepted_spend >= day_1_no_manual.accepted_spend
        assert day_5_large_manual.accepted_spend == 15.0  # Full acceptance
        assert day_5_large_manual.rejected_spend == 0.0

    def test_manual_allowance_wallet_interaction(self):
        """Test how manual allowance affects wallet balance"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Test where manual allowance is not fully used
        result, _, _ = SGMEngine.simulate_day(
            day_index=10,
            billing_day=11,
            requested_spend=5.0,  # Small request
            wallet_balance=3.0,
            accepted_history=[2.0] * 10,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=20.0,  # Much more than needed
        )

        # Per PRD: Unused manual allowances do NOT contribute to wallet balance
        # wallet_end = wallet_start + daily_limit - sgm_spend (no manual allowance rollback)
        expected_wallet_before_cap = 3.0 + result.daily_spend_limit - result.sgm_spend
        expected_wallet_after_cap = min(
            expected_wallet_before_cap, result.daily_spend_limit * 2
        )

        assert abs(result.wallet_balance_end - expected_wallet_after_cap) < 0.01

    def test_manual_allowance_billing_cycle_independence(self):
        """Test that manual allowance is independent of billing cycles"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0,
            billing_day_start=1,
            days_in_cycle=10,  # Short cycle for testing
        )

        # Test manual allowance on different billing days
        test_cases = [
            (1, 0.0),  # Start of cycle, no cumulative reserved
            (5, 20.0),  # Mid cycle, some reserved used
            (10, 50.0),  # End of cycle, more reserved used
        ]

        for billing_day, cumulative_reserved in test_cases:
            result, _, _ = SGMEngine.simulate_day(
                day_index=15,
                billing_day=billing_day,
                requested_spend=50.0,
                wallet_balance=5.0,
                accepted_history=[4.0] * 15,
                rule=rule,
                reserved_config=reserved,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowance=30.0,
            )

            # Manual allowance should work the same regardless of billing day
            # (though total acceptance may vary due to reserved availability)
            assert result.accepted_spend > 30.0  # At least manual allowance worth
            # When reserved volumes can cover the full request, SGM spending may be 0
            # This is correct behavior - reserved is used first


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
