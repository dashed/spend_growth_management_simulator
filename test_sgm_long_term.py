#!/usr/bin/env python3
"""
Long-term simulation tests for SGM
Tests system behavior over extended periods (months to years)
"""

import math

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMLongTerm:
    """Long-term simulation tests for SGM system"""

    def simulate_long_term(self, days, daily_pattern_func, rule, reserved_config=None):
        """Helper to simulate long periods with pattern function"""
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0
        results = []

        for day in range(days):
            billing_day = (
                day % (reserved_config.days_in_cycle if reserved_config else 30)
            ) + 1

            # Reset reserved volume on new billing cycle
            if reserved_config and billing_day == 1 and day > 0:
                cumulative_reserved = 0.0

            daily_request = daily_pattern_func(day)

            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=billing_day,
                requested_spend=daily_request,
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

    def test_one_year_steady_growth(self):
        """Test one year of steady business growth"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # 365 days with 2% monthly growth (26% annual)
        def growth_pattern(day):
            monthly_growth_rate = 0.02
            base_spend = 25.0
            months_elapsed = day / 30.44  # Average days per month
            return base_spend * ((1 + monthly_growth_rate) ** months_elapsed)

        results = self.simulate_long_term(365, growth_pattern, rule)

        # Check quarterly progression
        quarters = [
            results[0:91],  # Q1
            results[91:182],  # Q2
            results[182:273],  # Q3
            results[273:365],  # Q4
        ]

        # Average daily limits should increase each quarter
        quarterly_avg_limits = []
        for quarter in quarters:
            avg_limit = sum(r.daily_spend_limit for r in quarter) / len(quarter)
            quarterly_avg_limits.append(avg_limit)

        for i in range(1, 4):
            assert quarterly_avg_limits[i] > quarterly_avg_limits[i - 1]

        # Final quarter should be significantly higher than first
        assert (
            quarterly_avg_limits[3] > quarterly_avg_limits[0] * 1.5
        )  # Reasonable annual growth

        # Overall acceptance rate should be high
        total_accepted = sum(r.accepted_spend for r in results)
        total_requested = sum(r.requested_spend for r in results)
        acceptance_rate = total_accepted / total_requested
        assert acceptance_rate > 0.85  # High acceptance rate

    def test_multi_year_with_economic_cycles(self):
        """Test multi-year simulation with economic cycles"""
        rule = SGMRule(
            name="25%/week or $25/week",
            growth_percentage=25.0,
            min_growth_dollars=25.0,
            enabled=True,
        )

        # 2 years with economic cycles (boom, recession, recovery)
        def economic_cycle_pattern(day):
            base = 50.0
            years = day / 365.25

            # 2-year economic cycle
            cycle_position = (years * 2 * math.pi) % (2 * math.pi)

            # Growth trend + cycle
            trend_growth = 1.1**years  # 10% annual base growth
            cycle_effect = 1 + 0.3 * math.sin(cycle_position)  # ±30% cycle

            return base * trend_growth * cycle_effect

        results = self.simulate_long_term(730, economic_cycle_pattern, rule)  # 2 years

        # Check that system adapts to cycles
        # Find approximate boom and bust periods
        monthly_averages = []
        for month in range(24):  # 24 months
            start = month * 30
            end = min(start + 30, len(results))
            month_results = results[start:end]
            avg_request = sum(r.requested_spend for r in month_results) / len(
                month_results
            )
            avg_limit = sum(r.daily_spend_limit for r in month_results) / len(
                month_results
            )
            monthly_averages.append((avg_request, avg_limit))

        # Limits should generally track request patterns over time
        # Find months with high requests vs low requests
        requests = [avg[0] for avg in monthly_averages]
        limits = [avg[1] for avg in monthly_averages]

        max_request_month = requests.index(max(requests))
        min_request_month = requests.index(min(requests))

        # High request month should have higher limits (with some lag)
        # Check a few months after the peak
        lag_month = min(max_request_month + 2, len(limits) - 1)
        assert limits[lag_month] > limits[min_request_month]

    def test_seasonal_business_full_year(self):
        """Test full year of seasonal business patterns"""
        rule = SGMRule(
            name="30%/week or $30/week",
            growth_percentage=30.0,
            min_growth_dollars=30.0,
            enabled=True,
        )

        # Seasonal pattern: high in Q4 (holiday), low in Q1, moderate Q2-Q3
        def seasonal_pattern(day):
            base = 40.0
            day_of_year = day % 365

            # Seasonal multipliers by quarter
            if day_of_year < 90:  # Q1 - post-holiday low
                seasonal = 0.8
            elif day_of_year < 180:  # Q2 - spring growth
                seasonal = 1.0
            elif day_of_year < 270:  # Q3 - summer steady
                seasonal = 1.1
            else:  # Q4 - holiday surge
                seasonal = 1.8

            # Add some daily variation
            daily_variation = 1 + 0.2 * math.sin(
                2 * math.pi * day / 7
            )  # Weekly pattern

            return base * seasonal * daily_variation

        results = self.simulate_long_term(365, seasonal_pattern, rule)

        # Check seasonal adaptation
        q1_limits = [r.daily_spend_limit for r in results[60:90]]  # Mid Q1
        q4_limits = [r.daily_spend_limit for r in results[300:330]]  # Mid Q4

        avg_q1_limit = sum(q1_limits) / len(q1_limits)
        avg_q4_limit = sum(q4_limits) / len(q4_limits)

        # Q4 limits should be significantly higher than Q1
        assert avg_q4_limit > avg_q1_limit * 1.5

        # Holiday season should handle traffic well
        holiday_period = results[330:365]  # Last month
        holiday_acceptance = sum(r.accepted_spend for r in holiday_period)
        holiday_requests = sum(r.requested_spend for r in holiday_period)
        holiday_rate = holiday_acceptance / holiday_requests
        assert holiday_rate > 0.8  # Good acceptance during holidays

    def test_startup_growth_trajectory(self):
        """Test startup growth from small to large scale"""
        rule = SGMRule(
            name="50%/week or $50/week",  # Aggressive growth for startup
            growth_percentage=50.0,
            min_growth_dollars=50.0,
            enabled=True,
        )

        # Startup growth: exponential early, then logarithmic
        def startup_pattern(day):
            if day < 30:  # Month 1: exponential growth
                return 5.0 * (1.1**day)
            elif day < 90:  # Months 2-3: rapid growth
                base = 5.0 * (1.1**30)
                return base * (1.05 ** (day - 30))
            elif day < 180:  # Months 4-6: moderate growth
                base = 5.0 * (1.1**30) * (1.05**60)
                return base * (1.02 ** (day - 90))
            else:  # Months 7+: steady state with small growth
                base = 5.0 * (1.1**30) * (1.05**60) * (1.02**90)
                return base * (1.01 ** (day - 180))

        results = self.simulate_long_term(270, startup_pattern, rule)  # 9 months

        # Check growth phases
        phase1 = results[0:30]  # Exponential
        phase2 = results[30:90]  # Rapid
        phase3 = results[90:180]  # Moderate
        phase4 = results[180:270]  # Steady

        # Limits should increase through phases
        phase_avg_limits = []
        for phase in [phase1, phase2, phase3, phase4]:
            avg = (
                sum(r.daily_spend_limit for r in phase[-10:]) / 10
            )  # Last 10 days of phase
            phase_avg_limits.append(avg)

        for i in range(1, 4):
            assert phase_avg_limits[i] > phase_avg_limits[i - 1]

        # Final phase should be much higher than first
        assert phase_avg_limits[3] > phase_avg_limits[0] * 10

    def test_long_term_stability_with_constant_usage(self):
        """Test long-term stability with constant usage pattern"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Constant $15/day for 6 months
        def constant_pattern(day):
            return 15.0

        results = self.simulate_long_term(180, constant_pattern, rule)

        # After initial adaptation, limits should stabilize
        early_period = results[30:60]  # Days 30-60
        late_period = results[150:180]  # Days 150-180

        early_avg_limit = sum(r.daily_spend_limit for r in early_period) / len(
            early_period
        )
        late_avg_limit = sum(r.daily_spend_limit for r in late_period) / len(
            late_period
        )

        # Should be stable (small difference)
        stability_ratio = late_avg_limit / early_avg_limit
        assert 0.95 < stability_ratio < 1.05  # Within 5%

        # Should consistently accept full amount after stabilization
        stable_period = results[60:]  # After 2 months
        stable_rejections = sum(r.rejected_spend for r in stable_period)
        assert stable_rejections < 10.0  # Very few rejections

    def test_long_term_with_reserved_volumes(self):
        """Test long-term behavior with reserved volumes"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=200.0, billing_day_start=1, days_in_cycle=30
        )

        # Pattern that uses some reserved, some SGM
        def mixed_pattern(day):
            base = 8.0  # Base usage
            # Monthly cycle: higher usage mid-month
            day_of_month = (day % 30) + 1
            if 10 <= day_of_month <= 20:
                return base * 1.5
            return base

        results = self.simulate_long_term(365, mixed_pattern, rule, reserved)

        # Check monthly patterns
        monthly_stats = []
        for month in range(12):
            start = month * 30
            end = start + 30
            month_results = results[start:end]

            total_reserved = sum(r.reserved_spend for r in month_results)
            total_sgm = sum(r.sgm_spend for r in month_results)
            monthly_stats.append((total_reserved, total_sgm))

        # Each month should use similar amounts of reserved volume
        reserved_amounts = [stats[0] for stats in monthly_stats]
        assert max(reserved_amounts) - min(reserved_amounts) < 50.0  # Consistent usage

        # SGM usage should be positive (handling mid-month spikes)
        sgm_amounts = [stats[1] for stats in monthly_stats]
        assert all(amount > 0 for amount in sgm_amounts)

    def test_memory_and_performance_long_term(self):
        """Test that long-term simulation doesn't have memory issues"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Simple constant pattern for performance testing
        def simple_pattern(day):
            return 10.0 + (day % 7) * 2  # Weekly variation

        # Test with very long history
        results = self.simulate_long_term(1000, simple_pattern, rule)  # ~2.7 years

        # System should still be functional
        assert len(results) == 1000

        # Limits should be reasonable (not exploding or collapsing)
        final_limits = [r.daily_spend_limit for r in results[-30:]]
        avg_final_limit = sum(final_limits) / len(final_limits)
        assert 5.0 < avg_final_limit < 100.0  # Reasonable range

        # Should still be accepting most requests
        final_acceptance = sum(r.accepted_spend for r in results[-30:])
        final_requests = sum(r.requested_spend for r in results[-30:])
        final_rate = final_acceptance / final_requests
        assert final_rate > 0.8

    def test_extreme_volatility_long_term(self):
        """Test system behavior with extreme volatility over long term"""
        rule = SGMRule(
            name="40%/week or $40/week",  # High growth to handle volatility
            growth_percentage=40.0,
            min_growth_dollars=40.0,
            enabled=True,
        )

        # Extremely volatile pattern
        def volatile_pattern(day):
            import random

            random.seed(day)  # Reproducible

            base = 20.0
            # Random spikes (10% chance of 5x spike)
            if random.random() < 0.1:
                return base * 5
            # Random drops (5% chance of near-zero)
            elif random.random() < 0.05:
                return base * 0.1
            # Normal variation ±50%
            else:
                variation = 1 + (random.random() - 0.5)
                return base * variation

        results = self.simulate_long_term(180, volatile_pattern, rule)  # 6 months

        # System should remain stable despite volatility
        # Check that limits don't become extreme
        all_limits = [r.daily_spend_limit for r in results[30:]]  # After initial period

        # Limits should stay in reasonable range
        max_limit = max(all_limits)
        min_limit = min(all_limits)
        assert max_limit < 200.0  # Not too high
        assert min_limit > 1.0  # Not too low

        # Should handle most normal requests (non-spike days)
        normal_days = [r for r in results if r.requested_spend < 40.0]
        if normal_days:  # Should have some normal days
            normal_acceptance = sum(r.accepted_spend for r in normal_days)
            normal_requests = sum(r.requested_spend for r in normal_days)
            normal_rate = normal_acceptance / normal_requests
            assert normal_rate > 0.7  # Good acceptance for normal days


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
