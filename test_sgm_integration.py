#!/usr/bin/env python3
"""
Integration tests for SGM simulator
Tests the complete system behavior and state management
"""

import pytest

from sgm_simulator import (
    DayResult,
    ReservedVolumesConfig,
    SGMEngine,
    SGMRule,
    create_usage_scenarios,
)


class TestSGMIntegration:
    """Integration tests for complete SGM system behavior"""

    def test_full_month_simulation(self):
        """Test a complete month simulation with all features"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # State tracking
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0

        # Simulate full month with varying patterns
        daily_requests = [5.0] * 10 + [10.0] * 10 + [3.0] * 10
        total_rejected = 0.0

        for day in range(30):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=(day % 30) + 1,
                requested_spend=daily_requests[day],
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=reserved,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowance=0,
            )

            # Update state
            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used
            total_rejected += result.rejected_spend

            # Verify invariants
            assert result.wallet_balance_end >= 0
            assert result.cumulative_reserved_used <= reserved.monthly_volume
            assert result.accepted_spend <= result.requested_spend
            assert result.daily_spend_limit > 0

        # Verify end state
        assert len(accepted_history) == 30
        assert cumulative_reserved <= 100.0
        # With the tracking fix, there should be minimal rejections
        assert total_rejected < 20.0

    def test_scenario_generator_integration(self):
        """Test integration with scenario generator"""
        scenarios = create_usage_scenarios()

        # Test each predefined scenario
        for scenario_name, daily_spends in scenarios.items():
            assert len(daily_spends) == 30
            assert all(spend >= 0 for spend in daily_spends)
            assert max(daily_spends) < 1000  # Reasonable upper bound

    # Removed test_scenario_data_generation as generate_scenario_data doesn't exist

    def test_state_consistency_across_days(self):
        """Test that state remains consistent across multiple days"""
        rule = SGMRule(
            name="Test Rule",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Track all state
        states = {
            "accepted_history": [],
            "wallet_balance": 0.0,
            "cumulative_reserved": 0.0,
        }

        for day in range(14):  # Two weeks
            prev_history_len = len(states["accepted_history"])
            prev_wallet = states["wallet_balance"]

            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=(day % 30) + 1,
                requested_spend=10.0,
                wallet_balance=states["wallet_balance"],
                accepted_history=states["accepted_history"].copy(),
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            # Update state
            states["accepted_history"].append(result.accepted_spend)
            states["wallet_balance"] = result.wallet_balance_end

            # Verify consistency
            assert len(states["accepted_history"]) == prev_history_len + 1
            assert states["wallet_balance"] >= 0

            # Wallet change should match calculation
            expected_wallet = prev_wallet + result.daily_spend_limit - result.sgm_spend
            expected_wallet = min(expected_wallet, result.daily_spend_limit * 2)  # Cap
            assert abs(result.wallet_balance_end - expected_wallet) < 0.01

    def test_mixed_reserved_and_sgm_month(self):
        """Test month with mixed reserved and SGM spending"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=50.0,  # Only $50 reserved (10 days at $5/day)
            billing_day_start=1,
            days_in_cycle=30,
        )

        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0

        results = []
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

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used
            results.append(result)

        # First ~10 days should use reserved
        reserved_days = [r for r in results[:15] if r.reserved_spend > 0]
        assert len(reserved_days) >= 9  # At least 9 days of reserved usage

        # Remaining days should use SGM
        sgm_days = [r for r in results[15:] if r.sgm_spend > 0]
        assert len(sgm_days) >= 10  # At least 10 days of SGM usage

        # No rejections with proper tracking
        total_rejected = sum(r.rejected_spend for r in results)
        assert total_rejected == 0

    def test_intervention_recovery(self):
        """Test system recovery after interventions"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        accepted_history = []
        wallet_balance = 0.0

        # Normal usage, spike, then recovery
        pattern = [20.0] * 7 + [200.0] * 2 + [20.0] * 21
        interventions = []

        for day, request in enumerate(pattern):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=(day % 30) + 1,
                requested_spend=request,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end

            if result.intervention_type != "none":
                interventions.append((day, result.intervention_type))

        # Should see interventions during spike
        spike_interventions = [i for i in interventions if 7 <= i[0] <= 8]
        assert len(spike_interventions) > 0

        # Check recovery trend
        # During spike: shutdown interventions
        # During recovery: may have throttle interventions as limits grow
        spike_types = [i[1] for i in spike_interventions]
        late_interventions = [i for i in interventions if i[0] >= 23]
        late_types = [i[1] for i in late_interventions]

        # Spike should have shutdowns
        assert "shutdown" in spike_types

        # Late period should not have shutdowns (only throttles if any)
        assert "shutdown" not in late_types

    def test_bootstrap_to_prfaq_transition(self):
        """Test smooth transition from bootstrap to PRFAQ"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        accepted_history = []
        wallet_balance = 0.0
        daily_limits = []

        # Simulate first 10 days to see transition
        for day in range(10):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=day + 1,
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
            daily_limits.append(result.daily_spend_limit)

        # Bootstrap period (days 0-6) should show growth
        for i in range(1, 6):
            assert daily_limits[i] >= daily_limits[i - 1]

        # Day 7 starts PRFAQ - limit recalculates based on history
        # May drop if actual spending < bootstrap limits
        assert daily_limits[7] > 0

        # Should stabilize after transition
        assert abs(daily_limits[9] - daily_limits[8]) < 1.0

    def test_cumulative_metrics(self):
        """Test cumulative metrics calculation"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        accepted_history = []
        wallet_balance = 0.0

        total_requested = 0.0
        total_accepted = 0.0
        total_rejected = 0.0

        # Run for 30 days
        for day in range(30):
            request = 10.0 + (day % 7) * 2  # Varying requests

            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=(day % 30) + 1,
                requested_spend=request,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=None,
                cumulative_reserved_used=0,
                manual_allowance=0,
            )

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end

            # Track cumulative metrics
            total_requested += result.requested_spend
            total_accepted += result.accepted_spend
            total_rejected += result.rejected_spend

        # Verify totals
        assert abs(total_requested - (total_accepted + total_rejected)) < 0.01
        assert total_accepted > 0
        assert total_requested > total_accepted  # Some rejections expected

        # Calculate acceptance rate
        acceptance_rate = total_accepted / total_requested
        assert 0.5 < acceptance_rate < 1.0  # Should accept majority


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
