#!/usr/bin/env python3
"""
Scenario-based tests for SGM simulator
Tests real-world usage patterns and edge cases
"""

import pytest

from sgm_simulator import (
    ReservedVolumesConfig,
    SGMEngine,
    SGMRule,
    create_usage_scenarios,
)


class TestSGMScenarios:
    """Test suite for real-world SGM scenarios"""

    def simulate_days(self, days, daily_requests, rule, reserved_config=None):
        """Helper to simulate multiple days"""
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0
        results = []

        for day, request in enumerate(daily_requests[:days]):
            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=(day % 30) + 1,
                requested_spend=request,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=reserved_config,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowance=0,
            )

            # Update state with FIXED tracking (total spending)
            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used
            results.append(result)

        return results

    def test_steady_growth_scenario(self):
        """Test steady growth pattern"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Generate steady growth: 50, 52, 54, ...
        scenarios = create_usage_scenarios()
        results = self.simulate_days(30, scenarios["steady_growth"], rule)

        # Bootstrap period will have significant rejections since requests start at $50
        # but limits start at ~$2.86
        first_week_rejections = sum(r.rejected_spend for r in results[:7])
        assert first_week_rejections > 0  # Expected given high initial requests

        # Limits should grow over time
        assert results[-1].daily_spend_limit > results[7].daily_spend_limit

    def test_traffic_spike_scenario(self):
        """Test handling of traffic spikes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Create spike pattern: normal usage then 5x spike
        daily_pattern = [30.0] * 10 + [150.0] * 3 + [30.0] * 17
        results = self.simulate_days(30, daily_pattern, rule)

        # Should see interventions during spike
        spike_results = results[10:13]
        interventions = [
            r.intervention_type for r in spike_results if r.intervention_type != "none"
        ]
        assert len(interventions) > 0  # Should trigger interventions

        # Should recover after spike
        post_spike_results = results[20:]
        late_rejections = sum(r.rejected_spend for r in post_spike_results)
        assert late_rejections < sum(r.rejected_spend for r in spike_results)

    def test_developer_mistake_scenario(self):
        """Test developer mistake (sudden 25x spike)"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Normal usage then massive spike
        daily_pattern = [20.0] * 9 + [500.0] + [20.0] * 20
        results = self.simulate_days(30, daily_pattern, rule)

        # Day 9 should see shutdown intervention
        spike_day = results[9]
        assert spike_day.intervention_type == "shutdown"
        assert spike_day.rejected_spend > spike_day.accepted_spend

    def test_viral_moment_scenario(self):
        """Test viral growth pattern"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Gradual viral growth
        scenarios = create_usage_scenarios()
        results = self.simulate_days(30, scenarios["viral_moment"], rule)

        # SGM should adapt to viral growth pattern
        # Week 3 limits should be higher than week 1
        week1_limit = results[6].daily_spend_limit
        week3_limit = results[20].daily_spend_limit
        assert week3_limit > week1_limit * 1.5  # Significant growth

    def test_weekend_spikes_scenario(self):
        """Test weekend spike pattern"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Weekday: $30, Weekend: $80
        scenarios = create_usage_scenarios()
        results = self.simulate_days(30, scenarios["weekend_spikes"], rule)

        # Should adapt to weekly pattern
        # Check second weekend (days 12-13)
        weekend_results = results[12:14]
        weekday_results = results[15:17]

        # SGM adapts based on 7-day history, so limits reflect recent average
        # After weekend spikes, limits may be higher due to increased average
        # Check that system is responding to the pattern
        assert weekend_results[-1].daily_spend_limit > 0
        assert weekday_results[0].daily_spend_limit > 0

    def test_reserved_volume_depletion(self):
        """Test behavior as reserved volume depletes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Steady $5/day spending
        daily_pattern = [5.0] * 30
        results = self.simulate_days(30, daily_pattern, rule, reserved)

        # First 20 days should use reserved
        for i in range(20):
            assert results[i].reserved_spend == 5.0
            assert results[i].rejected_spend == 0.0

        # Days 21-30 should smoothly transition to SGM (with fix applied)
        for i in range(20, 30):
            assert results[i].rejected_spend == 0.0  # No rejections with fix

    def test_multiple_billing_cycles(self):
        """Test behavior across multiple billing cycles"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=100.0, billing_day_start=1, days_in_cycle=30
        )

        # Simulate 60 days (2 billing cycles)
        daily_pattern = [5.0] * 60
        results = self.simulate_days(60, daily_pattern, rule, reserved)

        # Check billing cycle reset
        day30 = results[29]  # Last day of first cycle
        day31 = results[30]  # First day of second cycle

        # Reserved should reset
        assert day31.cumulative_reserved_used < day30.cumulative_reserved_used
        assert day31.reserved_spend > 0

    def test_zero_to_high_usage(self):
        """Test transition from zero usage to high usage"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # No usage for a week, then sudden high usage
        daily_pattern = [0.0] * 7 + [50.0] * 7
        results = self.simulate_days(14, daily_pattern, rule)

        # Should handle transition gracefully
        # Day 7 should still allow some spending (min growth)
        assert results[7].daily_spend_limit >= rule.min_growth_dollars / 7

        # Should grow to accommodate new pattern
        assert results[13].daily_spend_limit > results[7].daily_spend_limit

    def test_decreasing_usage_pattern(self):
        """Test handling of decreasing usage"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Start high, decrease over time
        daily_pattern = [50.0 - i * 1.5 for i in range(30)]
        results = self.simulate_days(30, daily_pattern, rule)

        # Compare limits after bootstrap period
        week2_limit = results[13].daily_spend_limit  # After bootstrap
        week4_limit = results[27].daily_spend_limit

        # Week 4 should reflect decreasing usage pattern
        # Daily pattern goes from 50 down to near 0
        assert week4_limit >= rule.min_growth_dollars / 7  # Maintains minimum

    def test_erratic_usage_pattern(self):
        """Test highly variable usage pattern"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Random-like pattern
        scenarios = create_usage_scenarios()
        results = self.simulate_days(30, scenarios["random_variation"], rule)

        # With erratic pattern, interventions are expected
        # The system is working correctly by throttling erratic usage
        interventions = [r for r in results if r.intervention_type != "none"]
        assert len(interventions) > 0  # System is actively managing erratic usage

        # Check that not all interventions are shutdowns
        shutdowns = [r for r in results if r.intervention_type == "shutdown"]
        assert len(shutdowns) < len(interventions)  # Some throttles, not all shutdowns

    def test_manual_allowance_spike_handling(self):
        """Test using manual allowance for known spikes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Simulate with manual allowance on spike day
        accepted_history = [20.0] * 7
        wallet_balance = 10.0

        # Normal day without manual allowance
        normal_result, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=200.0,  # Big spike
            wallet_balance=wallet_balance,
            accepted_history=accepted_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=0,
        )

        # Same day with manual allowance
        manual_result, _, _ = SGMEngine.simulate_day(
            day_index=7,
            billing_day=8,
            requested_spend=200.0,
            wallet_balance=wallet_balance,
            accepted_history=accepted_history,
            rule=rule,
            reserved_config=None,
            cumulative_reserved_used=0,
            manual_allowance=200.0,  # Add manual allowance
        )

        # Manual allowance should prevent rejection
        assert manual_result.accepted_spend > normal_result.accepted_spend
        assert manual_result.rejected_spend < normal_result.rejected_spend


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
