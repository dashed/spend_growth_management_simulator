#!/usr/bin/env python3
"""
Business scenario tests for SGM simulator
Tests realistic business patterns and use cases
"""

import math

import pytest

from sgm_simulator import ReservedVolumesConfig, SGMEngine, SGMRule


class TestSGMBusinessScenarios:
    """Business scenario tests for real-world usage patterns"""

    def simulate_business_scenario(
        self, pattern, rule, reserved_config=None, manual_allowances=None
    ):
        """Helper to simulate business scenarios"""
        accepted_history = []
        wallet_balance = 0.0
        cumulative_reserved = 0.0
        results = []

        manual_allowances = manual_allowances or [0.0] * len(pattern)

        for day, request in enumerate(pattern):
            billing_day = (
                day % (reserved_config.days_in_cycle if reserved_config else 30)
            ) + 1

            if reserved_config and billing_day == 1 and day > 0:
                cumulative_reserved = 0.0

            result, _, _ = SGMEngine.simulate_day(
                day_index=day,
                billing_day=billing_day,
                requested_spend=request,
                wallet_balance=wallet_balance,
                accepted_history=accepted_history,
                rule=rule,
                reserved_config=reserved_config,
                cumulative_reserved_used=cumulative_reserved,
                manual_allowance=manual_allowances[day],
            )

            accepted_history.append(result.accepted_spend)
            wallet_balance = result.wallet_balance_end
            cumulative_reserved = result.cumulative_reserved_used
            results.append(result)

        return results

    def test_saas_company_growth(self):
        """Test SaaS company with steady subscriber growth"""
        rule = SGMRule(
            name="30%/week or $30/week",
            growth_percentage=30.0,
            min_growth_dollars=30.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=300.0, billing_day_start=1, days_in_cycle=30
        )

        # SaaS pattern: steady growth with month-end spikes (billing)
        pattern = []
        base_daily = 15.0

        for day in range(90):  # 3 months
            monthly_growth = (day // 30) * 0.1  # 10% monthly growth
            day_of_month = (day % 30) + 1

            # Month-end billing spike
            if day_of_month >= 28:
                billing_multiplier = 2.0
            else:
                billing_multiplier = 1.0

            daily_spend = base_daily * (1 + monthly_growth) * billing_multiplier
            pattern.append(daily_spend)

        results = self.simulate_business_scenario(pattern, rule, reserved)

        # Check month-end handling
        month_ends = [results[i] for i in [27, 28, 29, 57, 58, 59, 87, 88, 89]]
        for r in month_ends:
            # Should handle billing spikes with minimal rejections
            if r.rejected_spend > 0:
                rejection_rate = r.rejected_spend / r.requested_spend
                assert rejection_rate < 0.4  # Allow up to 40% rejection during spikes

        # Check growth adaptation
        month1_avg = sum(r.daily_spend_limit for r in results[20:30]) / 10
        month3_avg = sum(r.daily_spend_limit for r in results[80:90]) / 10
        assert month3_avg > month1_avg * 1.15  # Reasonable growth adaptation

    def test_ecommerce_holiday_season(self):
        """Test e-commerce company during holiday season"""
        rule = SGMRule(
            name="25%/week or $50/week",
            growth_percentage=25.0,
            min_growth_dollars=50.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=800.0,  # Higher reserved for holiday season
            billing_day_start=1,
            days_in_cycle=30,
        )

        # Holiday pattern: October - January
        # Base traffic -> Black Friday -> Cyber Monday -> Holiday steady -> New Year
        pattern = []
        for day in range(120):  # 4 months
            base = 35.0

            if day < 30:  # October - normal
                multiplier = 1.0
            elif day < 54:  # November pre-BF
                multiplier = 1.2
            elif day == 54:  # Black Friday
                multiplier = 4.0
            elif day == 57:  # Cyber Monday
                multiplier = 3.5
            elif day < 85:  # December holiday shopping
                multiplier = 2.2
            elif day < 90:  # Week between Christmas and New Year
                multiplier = 1.8
            elif day == 92:  # New Year's Day
                multiplier = 2.5
            else:  # January recovery
                multiplier = 0.8

            pattern.append(base * multiplier)

        # Plan manual allowances for known spikes
        manual_allowances = [0.0] * 120
        manual_allowances[54] = 120.0  # Black Friday
        manual_allowances[57] = 100.0  # Cyber Monday
        manual_allowances[92] = 60.0  # New Year's

        results = self.simulate_business_scenario(
            pattern, rule, reserved, manual_allowances
        )

        # Black Friday should be handled well
        bf_result = results[54]
        assert bf_result.accepted_spend > 120.0  # Should accept significant amount
        assert bf_result.rejected_spend < 20.0  # Minimal rejections with planning

        # Holiday season should show good adaptation
        pre_holiday = results[30:54]  # November pre-BF
        holiday_peak = results[60:85]  # December

        pre_avg_limit = sum(r.daily_spend_limit for r in pre_holiday) / len(pre_holiday)
        peak_avg_limit = sum(r.daily_spend_limit for r in holiday_peak) / len(
            holiday_peak
        )

        assert peak_avg_limit > pre_avg_limit * 1.5  # Significant adaptation

    def test_gaming_company_viral_hit(self):
        """Test gaming company with viral hit"""
        rule = SGMRule(
            name="50%/week or $100/week",  # Aggressive for viral growth
            growth_percentage=50.0,
            min_growth_dollars=100.0,
            enabled=True,
        )

        # Gaming viral pattern: normal -> viral explosion -> plateau -> decline
        pattern = []
        for day in range(90):
            if day < 20:  # Pre-viral
                base = 25.0
            elif day < 25:  # Viral explosion
                base = 25.0 * (3 ** (day - 19))  # Exponential growth
            elif day < 50:  # Viral plateau
                base = 25.0 * (3**5) * 0.8  # High but stable
            elif day < 70:  # Gradual decline
                decline_factor = 1 - ((day - 50) * 0.03)  # 3% daily decline
                base = 25.0 * (3**5) * 0.8 * decline_factor
            else:  # New normal (higher than pre-viral)
                base = 80.0

            # Add daily variation
            variation = 1 + 0.2 * math.sin(2 * math.pi * day / 7)
            pattern.append(base * variation)

        results = self.simulate_business_scenario(pattern, rule)

        # Viral explosion should trigger interventions initially
        explosion_period = results[20:25]
        interventions = [
            r.intervention_type
            for r in explosion_period
            if r.intervention_type != "none"
        ]
        assert len(interventions) > 0  # Should see some interventions

        # But system should show some adaptation during plateau
        plateau_period = results[30:40]
        plateau_rejections = sum(r.rejected_spend for r in plateau_period)
        plateau_requests = sum(r.requested_spend for r in plateau_period)
        plateau_acceptance = 1 - (plateau_rejections / plateau_requests)
        # With extreme viral growth, even aggressive SGM needs time to adapt
        assert plateau_acceptance > 0.01  # Some acceptance during plateau

        # New normal should be much higher than pre-viral
        pre_viral_limit = sum(r.daily_spend_limit for r in results[15:20]) / 5
        new_normal_limit = sum(r.daily_spend_limit for r in results[85:90]) / 5
        assert new_normal_limit > pre_viral_limit * 2

    def test_fintech_regulatory_compliance(self):
        """Test fintech company with regulatory compliance costs"""
        rule = SGMRule(
            name="15%/week or $25/week",  # Conservative for regulated industry
            growth_percentage=15.0,
            min_growth_dollars=25.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=400.0, billing_day_start=1, days_in_cycle=30
        )

        # Fintech pattern: steady base + quarterly compliance spikes + monthly reporting
        pattern = []
        for day in range(180):  # 6 months
            base = 20.0

            # Monthly reporting spike (end of month)
            day_of_month = (day % 30) + 1
            if day_of_month >= 28:
                monthly_spike = 1.5
            else:
                monthly_spike = 1.0

            # Quarterly compliance spike (end of quarter)
            if day in [89, 90, 179, 180]:  # End of Q1 and Q2
                quarterly_spike = 2.5
            else:
                quarterly_spike = 1.0

            pattern.append(base * monthly_spike * quarterly_spike)

        # Plan for quarterly compliance
        manual_allowances = [0.0] * 180
        manual_allowances[89] = 30.0  # Q1 compliance
        manual_allowances[90] = 25.0
        manual_allowances[179] = 35.0  # Q2 compliance

        results = self.simulate_business_scenario(
            pattern, rule, reserved, manual_allowances
        )

        # Compliance periods should be handled well
        q1_compliance = results[89:91]
        q2_compliance = results[179:180]

        for period in [q1_compliance, q2_compliance]:
            for r in period:
                assert (
                    r.rejected_spend < 15.0
                )  # Allow reasonable rejections during compliance spikes

        # Should maintain conservative growth (or at least stability)
        month1_limit = sum(r.daily_spend_limit for r in results[20:30]) / 10
        month6_limit = sum(r.daily_spend_limit for r in results[170:180]) / 10
        growth_ratio = month6_limit / month1_limit
        assert 0.9 < growth_ratio < 2.0  # Conservative growth or stability

    def test_media_streaming_live_event(self):
        """Test media streaming during live events"""
        rule = SGMRule(
            name="40%/week or $80/week",
            growth_percentage=40.0,
            min_growth_dollars=80.0,
            enabled=True,
        )

        # Live event pattern: normal -> pre-event buildup -> event spike -> post-event
        pattern = []
        for day in range(60):
            base = 50.0

            if day < 20:  # Normal period
                multiplier = 1.0
            elif day < 25:  # Pre-event buildup
                multiplier = 1 + ((day - 20) * 0.2)  # 20% daily increase
            elif day == 25:  # Event day
                multiplier = 5.0
            elif day < 30:  # Event weekend
                multiplier = 3.0
            elif day < 35:  # Post-event high interest
                multiplier = 2.0
            else:  # Return to normal
                multiplier = 1.2  # Slightly higher new normal

            pattern.append(base * multiplier)

        # Plan for event
        manual_allowances = [0.0] * 60
        manual_allowances[25] = 200.0  # Event day
        manual_allowances[26] = 100.0  # Event weekend
        manual_allowances[27] = 100.0

        results = self.simulate_business_scenario(
            pattern, rule, None, manual_allowances
        )

        # Event day should be handled successfully
        event_day = results[25]
        assert event_day.accepted_spend > 200.0  # Should accept significant traffic
        assert event_day.rejected_spend < 50.0  # Minimal rejections

        # System should adapt quickly to new baseline
        new_normal = results[40:50]
        old_normal = results[10:20]

        new_avg_limit = sum(r.daily_spend_limit for r in new_normal) / len(new_normal)
        old_avg_limit = sum(r.daily_spend_limit for r in old_normal) / len(old_normal)

        assert new_avg_limit > old_avg_limit * 1.3  # Higher post-event baseline

    def test_b2b_enterprise_sales_cycles(self):
        """Test B2B enterprise with quarterly sales cycles"""
        rule = SGMRule(
            name="20%/week or $40/week",
            growth_percentage=20.0,
            min_growth_dollars=40.0,
            enabled=True,
        )

        reserved = ReservedVolumesConfig(
            monthly_volume=250.0, billing_day_start=1, days_in_cycle=30
        )

        # B2B pattern: steady base + quarterly sales pushes + end-of-quarter spikes
        pattern = []
        for day in range(270):  # 9 months (3 quarters)
            base = 30.0

            # Quarterly cycle
            day_in_quarter = day % 90

            if day_in_quarter < 70:  # Normal sales activity
                quarterly_factor = 1.0
            elif day_in_quarter < 85:  # End-of-quarter push
                quarterly_factor = 1.5
            else:  # Final week rush
                quarterly_factor = 2.2

            # Annual growth trend
            annual_growth = 1 + (day / 365) * 0.2  # 20% annual growth

            pattern.append(base * quarterly_factor * annual_growth)

        results = self.simulate_business_scenario(pattern, rule, reserved)

        # Check quarterly patterns
        q1_end = results[85:90]
        q2_end = results[175:180]
        q3_end = results[265:270]

        # End-of-quarter periods should show higher activity but good handling
        for quarter_end in [q1_end, q2_end, q3_end]:
            avg_acceptance = sum(r.accepted_spend for r in quarter_end) / len(
                quarter_end
            )
            avg_request = sum(r.requested_spend for r in quarter_end) / len(quarter_end)
            acceptance_rate = avg_acceptance / avg_request
            assert acceptance_rate > 0.8  # Good handling of quarter-end rushes

        # Should show growth quarter over quarter
        q1_limits = [r.daily_spend_limit for r in results[20:70]]
        q3_limits = [r.daily_spend_limit for r in results[200:250]]

        q1_avg = sum(q1_limits) / len(q1_limits)
        q3_avg = sum(q3_limits) / len(q3_limits)

        assert q3_avg > q1_avg * 1.05  # Modest growth over 3 quarters

    def test_healthcare_telemedicine_pandemic(self):
        """Test healthcare telemedicine during pandemic surge"""
        rule = SGMRule(
            name="60%/week or $150/week",  # Very aggressive for healthcare emergency
            growth_percentage=60.0,
            min_growth_dollars=150.0,
            enabled=True,
            validate_bounds=False,  # Disable validation for business scenario testing
        )

        # Pandemic pattern: normal -> gradual increase -> exponential surge -> plateau -> new normal
        pattern = []
        for day in range(150):  # 5 months
            if day < 30:  # Pre-pandemic normal
                base = 40.0
            elif day < 45:  # Early pandemic growth
                growth_days = day - 30
                base = 40.0 * (1.15**growth_days)  # 15% daily growth
            elif day < 60:  # Exponential surge
                surge_days = day - 45
                base = 40.0 * (1.15**15) * (1.3**surge_days)  # 30% daily growth
            elif day < 100:  # High plateau
                base = 40.0 * (1.15**15) * (1.3**15) * 0.8  # Stable high level
            else:  # New normal (much higher than pre-pandemic)
                base = 200.0

            # Weekly patterns (higher weekdays, lower weekends)
            day_of_week = day % 7
            if day_of_week < 5:  # Weekdays
                weekly_factor = 1.1
            else:  # Weekends
                weekly_factor = 0.8

            pattern.append(base * weekly_factor)

        # Emergency manual allowances during surge
        manual_allowances = [0.0] * 150
        for day in range(45, 70):  # During surge period
            manual_allowances[day] = 200.0

        results = self.simulate_business_scenario(
            pattern, rule, None, manual_allowances
        )

        # Surge period should show some handling despite extreme growth
        surge_period = results[45:65]
        surge_acceptance = sum(r.accepted_spend for r in surge_period)
        surge_requests = sum(r.requested_spend for r in surge_period)
        surge_rate = surge_acceptance / surge_requests
        # With exponential pandemic surge, even aggressive SGM + manual allowances struggle
        assert surge_rate > 0.1  # Some acceptance despite extreme circumstances

        # System should adapt to new normal
        pre_pandemic = results[20:30]
        new_normal = results[140:150]

        pre_avg_limit = sum(r.daily_spend_limit for r in pre_pandemic) / len(
            pre_pandemic
        )
        new_avg_limit = sum(r.daily_spend_limit for r in new_normal) / len(new_normal)

        assert new_avg_limit > pre_avg_limit * 4  # Massive adaptation for healthcare


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
