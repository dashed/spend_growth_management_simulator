#!/usr/bin/env python3
"""
Comprehensive scenario tests for SGM simulator
Tests real-world business patterns and complex usage scenarios
"""

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMComprehensiveScenarios:
    """Comprehensive scenario tests for various business patterns"""

    def simulate_days(
        self, days, daily_requests, rule, reserved_config=None, manual_allowances=None
    ):
        """Helper to simulate multiple days with optional manual allowances"""
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0
        results = []

        manual_allowances = manual_allowances or [0.0] * days

        for day in range(days):
            # Handle billing day reset for reserved
            billing_day = (
                day % (reserved_config.days_in_cycle if reserved_config else 30)
            ) + 1

            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=billing_day,
                requested_spend=daily_requests[day],
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=reserved_config,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowance=manual_allowances[day],
            )

            # Update state with total spending (the fix)
            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used

            # Reset cumulative reserved on new billing cycle
            if reserved_config and billing_day == 1 and day > 0:
                cumulative_reserved = result.cumulative_reserved_used

            results.append(result)

        return results

    def test_black_friday_scenario(self):
        """Test Black Friday traffic pattern"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=500.0,  # Higher reserved for peak season
            billing_day_start=1,
            days_in_cycle=30,
        )

        # November pattern: normal, build-up, Black Friday spike, recovery
        pattern = (
            [25.0] * 20  # Normal November traffic
            + [40.0] * 3  # Pre-Black Friday buildup
            + [200.0] * 2  # Black Friday + Cyber Monday
            + [60.0] * 3  # Post-spike elevated traffic
            + [30.0] * 2  # Return to normal
        )

        # Use manual allowances for known spikes
        manual_allowances = [0.0] * 23 + [150.0, 180.0] + [0.0] * 5

        results = self.simulate_days(30, pattern, rule, reserved, manual_allowances)

        # Check Black Friday handling
        bf_results = results[23:25]  # Black Friday + Cyber Monday

        # Should handle spikes with combination of reserved + manual + SGM
        for r in bf_results:
            assert r.accepted_spend > 100.0  # Significant spending accepted
            assert r.rejected_spend < 50.0  # Minimal rejections with planning

        # Check recovery after spike
        recovery_results = results[25:28]
        for r in recovery_results:
            assert r.intervention_type in ["none", "throttle"]  # No shutdowns

    def test_product_launch_scenario(self):
        """Test product launch with gradual ramp-up"""
        rule = SGMRule(
            name="30%/week or $30/week",  # Higher growth for startup
            growth_percentage=30.0,
            min_growth_dollars=30.0,
            enabled=True,
        )

        # Product launch pattern: pre-launch, launch spike, viral growth
        pattern = (
            [5.0] * 7  # Pre-launch development
            + [50.0]
            + [100.0]
            + [150.0]  # Launch spike
            + [i * 10 for i in range(4, 11)]  # Viral growth 40-100
            + [120.0] * 5  # Plateau
            + [140.0] * 5  # Next growth phase
            + [100.0] * 5  # Stabilization
        )

        results = self.simulate_days(30, pattern, rule)

        # Pre-launch should have minimal rejections
        pre_launch = results[:7]
        pre_launch_rejected = sum(r.rejected_spend for r in pre_launch)
        assert pre_launch_rejected < 10.0

        # Launch spike should be partially rejected but manageable
        launch_spike = results[7:10]
        assert all(r.accepted_spend > 0 for r in launch_spike)

        # Viral growth should show increasing limits
        viral_growth = results[10:17]
        assert viral_growth[-1].daily_spend_limit > viral_growth[0].daily_spend_limit

        # Final plateau should have stable high limits
        plateau = results[-5:]
        plateau_limits = [r.daily_spend_limit for r in plateau]
        assert max(plateau_limits) - min(plateau_limits) < 15.0  # Reasonably stable

    def test_seasonal_business_scenario(self):
        """Test seasonal business with quarterly patterns"""
        rule = SGMRule(
            name="15%/week or $15/week",
            growth_percentage=15.0,
            min_growth_dollars=15.0,
            enabled=True,
            validate_bounds=False,  # Disable validation for scenario testing
        )

        # 90-day seasonal pattern (Q1): ramp up to peak mid-quarter, then down
        days = 90
        pattern = []
        for day in range(days):
            # Sine wave pattern with growing amplitude
            import math

            base = 20 + day * 0.2  # Growing base
            seasonal = 15 * math.sin(2 * math.pi * day / 30)  # Monthly cycle
            pattern.append(max(5.0, base + seasonal))

        results = self.simulate_days(days, pattern, rule)

        # Check that system adapts to seasonal patterns
        month1 = results[:30]
        month2 = results[30:60]
        month3 = results[60:90]

        # Average limits should increase month over month
        avg_limit_m1 = sum(r.daily_spend_limit for r in month1) / len(month1)
        avg_limit_m2 = sum(r.daily_spend_limit for r in month2) / len(month2)
        avg_limit_m3 = sum(r.daily_spend_limit for r in month3) / len(month3)

        assert avg_limit_m2 > avg_limit_m1
        assert avg_limit_m3 > avg_limit_m2

        # Total rejections should be reasonable
        total_rejected = sum(r.rejected_spend for r in results)
        total_requested = sum(r.requested_spend for r in results)
        rejection_rate = total_rejected / total_requested
        assert (
            rejection_rate < 0.5
        )  # Less than 50% rejection rate for complex seasonal pattern

    def test_maintenance_window_scenario(self):
        """Test planned maintenance windows with zero traffic"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Pattern: normal, maintenance window, recovery
        pattern = (
            [30.0] * 10  # Normal operation
            + [0.0] * 5  # Maintenance window (5 days)
            + [35.0] * 10  # Post-maintenance recovery
            + [40.0] * 5  # New normal (higher usage)
        )

        results = self.simulate_days(30, pattern, rule)

        # Maintenance window should not break the system
        maintenance = results[10:15]
        for r in maintenance:
            assert r.accepted_spend == 0.0
            assert r.rejected_spend == 0.0
            assert r.daily_spend_limit > 0  # Limits should remain positive

        # Recovery should be smooth
        recovery = results[15:25]
        recovery_rejected = sum(r.rejected_spend for r in recovery)
        assert (
            recovery_rejected < 300.0
        )  # Some rejections expected during recovery phase

        # New normal should achieve higher limits
        new_normal = results[25:]
        avg_new_limit = sum(r.daily_spend_limit for r in new_normal) / len(new_normal)
        pre_maintenance_limit = results[9].daily_spend_limit
        assert avg_new_limit > pre_maintenance_limit

    def test_emergency_scaling_scenario(self):
        """Test emergency scaling with immediate high usage"""
        rule = SGMRule(
            name="50%/week or $50/week",  # Aggressive growth for emergencies
            growth_percentage=50.0,
            min_growth_dollars=50.0,
            enabled=True,
        )

        # Emergency scenario: normal, sudden 10x spike, sustained high usage
        pattern = (
            [20.0] * 7  # Normal week
            + [200.0] * 1  # Emergency spike
            + [150.0] * 7  # Sustained emergency level
            + [100.0] * 7  # Gradual reduction
            + [60.0] * 8  # New elevated normal
        )

        # Emergency manual allowances
        manual_allowances = [0.0] * 7 + [180.0] + [100.0] * 7 + [50.0] * 7 + [0.0] * 8

        results = self.simulate_days(30, pattern, rule, None, manual_allowances)

        # Emergency day should be mostly handled
        emergency_day = results[7]
        assert emergency_day.accepted_spend > 180.0  # Manual + SGM

        # Sustained period should adapt quickly with high growth rate
        sustained = results[8:15]
        sustained_limits = [r.daily_spend_limit for r in sustained]
        assert sustained_limits[-1] > sustained_limits[0] * 2  # Significant growth

        # New normal should be much higher than original
        new_normal = results[-5:]
        original_normal = results[:5]
        avg_new = sum(r.daily_spend_limit for r in new_normal) / len(new_normal)
        avg_original = sum(r.daily_spend_limit for r in original_normal) / len(
            original_normal
        )
        assert avg_new > avg_original * 3  # At least 3x higher

    def test_gradual_business_growth_scenario(self):
        """Test steady business growth over extended period"""
        rule = SGMRule(
            name="25%/week or $25/week",
            growth_percentage=25.0,
            min_growth_dollars=25.0,
            enabled=True,
        )

        # 6-month gradual growth: 5% monthly growth
        days = 180
        pattern = []
        for day in range(days):
            base_growth = 20 * (1.05 ** (day / 30))  # 5% monthly growth
            daily_variation = 1 + 0.1 * ((day * 17) % 7 - 3) / 3  # ±10% daily variation
            pattern.append(base_growth * daily_variation)

        results = self.simulate_days(days, pattern, rule)

        # Check month-over-month growth in limits
        monthly_avg_limits = []
        for month in range(6):
            start = month * 30
            end = start + 30
            month_results = results[start:end]
            avg_limit = sum(r.daily_spend_limit for r in month_results) / len(
                month_results
            )
            monthly_avg_limits.append(avg_limit)

        # Each month should have higher average limits than previous
        for i in range(1, 6):
            assert monthly_avg_limits[i] > monthly_avg_limits[i - 1]

        # Final month should be significantly higher than first
        assert (
            monthly_avg_limits[-1] > monthly_avg_limits[0] * 2.5
        )  # Significant growth over 6 months

        # Overall acceptance rate should be good
        total_accepted = sum(r.accepted_spend for r in results)
        total_requested = sum(r.requested_spend for r in results)
        acceptance_rate = total_accepted / total_requested
        assert acceptance_rate > 0.8  # At least 80% acceptance

    def test_microservices_mixed_pattern(self):
        """Test mixed usage pattern like multiple microservices"""
        rule = SGMRule(
            name="20%/week or $20/week",
            growth_percentage=20.0,
            min_growth_dollars=20.0,
            enabled=True,
        )

        # Mixed pattern: base load + periodic spikes + random variations
        import random

        random.seed(42)  # Reproducible

        pattern = []
        for day in range(60):
            base_load = 15 + day * 0.1  # Gradually increasing base

            # Weekly spikes (weekend)
            if day % 7 in [5, 6]:  # Weekend
                base_load *= 1.5

            # Random spikes (5% chance)
            if random.random() < 0.05:
                base_load *= 3

            # Daily variation (±20%)
            variation = 1 + 0.4 * (random.random() - 0.5)
            pattern.append(base_load * variation)

        results = self.simulate_days(60, pattern, rule)

        # System should adapt to mixed patterns
        week1_limits = [r.daily_spend_limit for r in results[:7]]
        week8_limits = [r.daily_spend_limit for r in results[49:56]]

        avg_week1 = sum(week1_limits) / len(week1_limits)
        avg_week8 = sum(week8_limits) / len(week8_limits)

        assert avg_week8 > avg_week1 * 1.5  # Significant adaptation

        # Should handle random spikes reasonably
        all_interventions = [r.intervention_type for r in results]
        shutdown_count = all_interventions.count("shutdown")
        assert shutdown_count < 5  # Limited shutdowns for random spikes

    def test_international_launch_scenario(self):
        """Test international expansion with timezone-based usage"""
        rule = SGMRule(
            name="40%/week or $40/week",  # High growth for expansion
            growth_percentage=40.0,
            min_growth_dollars=40.0,
            enabled=True,
        )

        # Pattern: US launch, EU launch (+8 hours), APAC launch (+16 hours)
        pattern = []
        for day in range(45):  # 45-day international rollout
            base = 10 + day * 0.5  # Base growth

            # US market (days 0+)
            us_contribution = base if day >= 0 else 0

            # EU market (days 15+)
            eu_contribution = base * 0.8 if day >= 15 else 0

            # APAC market (days 30+)
            apac_contribution = base * 1.2 if day >= 30 else 0

            total = us_contribution + eu_contribution + apac_contribution

            # Add timezone stagger effect (24-hour cycle)
            import math

            timezone_factor = 1 + 0.3 * math.sin(2 * math.pi * day / 7)
            pattern.append(total * timezone_factor)

        results = self.simulate_days(45, pattern, rule)

        # Check growth phases
        us_only = results[:15]
        us_eu = results[15:30]
        global_launch = results[30:45]

        avg_us = sum(r.daily_spend_limit for r in us_only) / len(us_only)
        avg_us_eu = sum(r.daily_spend_limit for r in us_eu) / len(us_eu)
        avg_global = sum(r.daily_spend_limit for r in global_launch) / len(
            global_launch
        )

        # Each phase should have higher limits
        assert avg_us_eu > avg_us * 1.3  # EU launch effect
        assert avg_global > avg_us_eu * 1.3  # APAC launch effect

        # Final limits should be significantly higher
        assert avg_global > avg_us * 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
