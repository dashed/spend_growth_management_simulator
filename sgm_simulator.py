# /// script
# dependencies = [
#   "streamlit>=1.28.0",
#   "plotly>=5.0.0",
#   "numpy>=1.26.0",
# ]
# requires-python = ">=3.10"
# ///

"""
SGM Simulator
=========================================
This version moves navigation controls to a more accessible location
in the main content area for better user experience.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, NamedTuple, Optional, Tuple

try:
    import plotly.express as px
    import plotly.graph_objects as go

    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

# =============================================================================
# CORE DOMAIN MODELS
# =============================================================================


@dataclass
class SGMRule:
    """SGM Rule configuration"""

    name: str
    growth_percentage: float  # 5-50% per PRD
    min_growth_dollars: float  # minimum $20 per PRD
    enabled: bool = True
    # New: Weekly recalculation settings
    weekly_recalc_enabled: bool = False
    weekly_recalc_day: int = 0  # 0=Monday, 6=Sunday
    validate_bounds: bool = True  # Allow disabling validation for tests

    def __post_init__(self):
        """Validate SGM rule parameters per PRD requirements"""
        if self.validate_bounds:
            if not (5.0 <= self.growth_percentage <= 50.0):
                raise ValueError(
                    f"Growth percentage must be between 5% and 50% per PRD, got {self.growth_percentage}%"
                )
            if self.min_growth_dollars < 20.0:
                raise ValueError(
                    f"Minimum growth dollars must be at least $20 per PRD, got ${self.min_growth_dollars}"
                )

        # Always validate weekly recalc day regardless of validate_bounds
        if not (0 <= self.weekly_recalc_day <= 6):
            raise ValueError(
                f"Weekly recalculation day must be 0-6 (Monday-Sunday), got {self.weekly_recalc_day}"
            )


@dataclass
class ManualAllowance:
    """Manual allowance with expiration support"""

    amount: float
    created_day: int
    expiration_days: Optional[int] = None  # None = never expires
    reason: str = ""

    def is_expired(self, current_day: int) -> bool:
        """Check if allowance has expired"""
        if self.expiration_days is None:
            return False
        return current_day >= self.created_day + self.expiration_days

    def remaining_amount(self, current_day: int) -> float:
        """Get remaining allowance amount (0 if expired)"""
        return 0.0 if self.is_expired(current_day) else self.amount


@dataclass
class WalletConfig:
    """Wallet capacity configuration"""

    model: str = "daily_limit_2x"  # "daily_limit_2x" or "three_day_budget"
    custom_multiplier: Optional[float] = None  # For custom models

    def calculate_max_capacity(self, daily_limit: float) -> float:
        """Calculate maximum wallet capacity based on model"""
        if self.model == "daily_limit_2x":
            return daily_limit * 2.0
        elif self.model == "three_day_budget":
            return daily_limit * 3.0
        elif self.model == "custom" and self.custom_multiplier:
            return daily_limit * self.custom_multiplier
        else:
            return daily_limit * 2.0  # Default fallback


@dataclass
class Invoice:
    """Monthly invoice with prepaid reserved + accumulated SGM usage"""

    billing_cycle: int  # Which billing cycle (1, 2, 3, ...)
    cycle_start_day: int  # Day when billing cycle started
    cycle_end_day: int  # Day when billing cycle ended
    prepaid_reserved: float  # Monthly reserved volume amount
    accumulated_sgm: float  # Total SGM spend during the cycle
    total_amount: float  # prepaid_reserved + accumulated_sgm
    generated_on_day: int  # Simulation day when invoice was generated

    @property
    def monthly_revenue(self) -> float:
        """Monthly revenue for ARR calculation"""
        return self.total_amount


@dataclass
class ReservedVolumesConfig:
    """Reserved volumes configuration"""

    monthly_volume: float
    billing_day_start: int  # Starting billing day (1-30)
    days_in_cycle: int = 30

    def advance_billing_day(self, current_day: int) -> int:
        """Calculate next billing day"""
        next_day = current_day + 1
        return 1 if next_day > self.days_in_cycle else next_day


@dataclass
class DayResult:
    """Result of simulating a single day"""

    day_index: int  # 0-based simulation day
    billing_day: int  # 1-based billing cycle day
    requested_spend: float
    accepted_spend: float
    rejected_spend: float
    reserved_spend: float
    sgm_spend: float
    daily_spend_limit: float
    wallet_balance_start: float
    wallet_balance_end: float
    wallet_max_capacity: float  # New: Track wallet capacity
    intervention_type: str  # "none", "throttle", "shutdown"
    reserved_remaining: float
    cumulative_reserved_used: float  # Track total used this month
    manual_allowances_used: float = 0.0  # New: Track manual allowance usage
    expired_allowances: float = 0.0  # New: Track expired allowances


# =============================================================================
# STATELESS SIMULATION ENGINE
# =============================================================================


class SGMEngine:
    """Stateless SGM simulation engine - pure functions only"""

    @staticmethod
    def calculate_daily_spend_limit(
        accepted_history: List[float],
        rule: SGMRule,
        current_day_index: int = 0,
        last_recalc_day: int = 0,
        baseline_spend: Optional[float] = None,
    ) -> Tuple[float, int, Optional[float]]:
        """
        Calculate daily spend limit with weekly recalculation support
        Returns: (daily_limit, last_recalc_day, baseline_spend)
        """
        # Check if we need weekly recalculation
        should_recalc = False
        if rule.weekly_recalc_enabled and len(accepted_history) >= 7:
            days_since_recalc = current_day_index - last_recalc_day
            current_weekday = current_day_index % 7
            if days_since_recalc >= 7 and current_weekday == rule.weekly_recalc_day:
                should_recalc = True
                last_recalc_day = current_day_index

                # WEEKLY RECALCULATION: Calculate new baseline from recent 7-day average
                baseline_spend = sum(accepted_history[-7:]) / 7.0
        if len(accepted_history) < 7:
            # Bootstrap period - use a more reasonable approach
            if len(accepted_history) == 0:
                # Day 0: Allow minimum weekly amount divided by 7
                daily_limit = rule.min_growth_dollars / 7
                return daily_limit, last_recalc_day, baseline_spend

            # Days 1-6: Allow growth based on actual history
            # Calculate what we need to reach weekly minimum
            days_elapsed = len(accepted_history)
            total_so_far = sum(accepted_history)
            days_remaining = 7 - days_elapsed

            # How much do we need per day to reach weekly minimum?
            needed_per_day = (rule.min_growth_dollars - total_so_far) / days_remaining

            # Also calculate growth based on current average
            current_avg = total_so_far / days_elapsed
            growth_based = current_avg * (1 + rule.growth_percentage / 100)

            # Take the maximum of:
            # 1. What we need to reach weekly minimum
            # 2. Growth based on current average
            # 3. Daily minimum allowance
            daily_limit = max(needed_per_day, growth_based, rule.min_growth_dollars / 7)
            return daily_limit, last_recalc_day, baseline_spend

        # PRFAQ algorithm for 7+ days of history
        # Use baseline if weekly recalculation is enabled and we have a baseline
        if rule.weekly_recalc_enabled and baseline_spend is not None:
            # Use baseline for growth calculations (PRD-style)
            weekly_baseline = baseline_spend * 7.0
            weekly_growth_limit = max(
                rule.min_growth_dollars,
                weekly_baseline * (1 + rule.growth_percentage / 100),
            )
            daily_limit = weekly_growth_limit / 7.0
        else:
            # Standard PRFAQ rolling-window algorithm
            recent_7 = sum(accepted_history[-7:])
            recent_6 = sum(accepted_history[-6:])

            growth_factor = (1 + rule.growth_percentage / 100) ** (1.0 / 7)
            exponential_limit = recent_7 * growth_factor - recent_6
            linear_limit = recent_7 + rule.min_growth_dollars / 7 - recent_6

            daily_limit = max(exponential_limit, linear_limit, 0)

        return daily_limit, last_recalc_day, baseline_spend

    @staticmethod
    def calculate_active_manual_allowances(
        allowances: List[ManualAllowance], current_day: int
    ) -> Tuple[float, float]:
        """
        Calculate total active and expired manual allowances
        Returns: (active_total, expired_total)
        """
        active_total = 0.0
        expired_total = 0.0

        for allowance in allowances:
            if allowance.is_expired(current_day):
                expired_total += allowance.amount
            else:
                active_total += allowance.remaining_amount(current_day)

        return active_total, expired_total

    @staticmethod
    def simulate_day(
        day_index: int,
        billing_day: int,
        requested_spend: float,
        wallet_balance: float,
        accepted_history: List[float],
        rule: SGMRule,
        wallet_config: Optional[WalletConfig] = None,
        reserved_config: Optional[ReservedVolumesConfig] = None,
        cumulative_reserved_used: float = 0,
        manual_allowances: Optional[List[ManualAllowance]] = None,
        last_recalc_day: int = 0,
        baseline_spend: Optional[float] = None,
        manual_allowance: float = 0,  # Legacy parameter for backward compatibility
    ) -> Tuple[DayResult, int, Optional[float]]:
        """
        Simulate a single day with enhanced wallet cap enforcement
        Returns: (DayResult, updated_last_recalc_day, updated_baseline_spend)
        """
        if wallet_config is None:
            wallet_config = WalletConfig()
        if manual_allowances is None:
            manual_allowances = []

        # Handle legacy manual_allowance parameter
        if manual_allowance > 0:
            legacy_allowance = ManualAllowance(
                amount=manual_allowance,
                created_day=day_index,
                expiration_days=None,
                reason="Legacy compatibility",
            )
            manual_allowances = manual_allowances + [legacy_allowance]

        # Step 1: Handle reserved volumes first
        reserved_spend = 0.0
        new_cumulative_reserved = cumulative_reserved_used
        reserved_remaining = 0.0

        if reserved_config and reserved_config.monthly_volume > 0:
            # Reset cumulative usage on new billing cycle when billing_day wraps to 1
            # (but not on day 0 if we start with billing_day_start == 1)
            if billing_day == 1 and day_index > 0:
                new_cumulative_reserved = 0.0
            else:
                new_cumulative_reserved = cumulative_reserved_used

            reserved_available = max(
                0, reserved_config.monthly_volume - new_cumulative_reserved
            )
            reserved_remaining = reserved_available
            reserved_spend = min(requested_spend, reserved_available)
            new_cumulative_reserved = new_cumulative_reserved + reserved_spend

        remaining_spend = requested_spend - reserved_spend

        # Step 2: Calculate SGM limits with weekly recalculation
        daily_limit, updated_last_recalc_day, updated_baseline = (
            SGMEngine.calculate_daily_spend_limit(
                accepted_history, rule, day_index, last_recalc_day, baseline_spend
            )
        )

        # Step 3: Calculate wallet capacity and enforce strict cap
        max_wallet_capacity = wallet_config.calculate_max_capacity(daily_limit)

        # CRITICAL FIX: Ensure wallet never exceeds capacity at any point
        wallet_start = min(wallet_balance, max_wallet_capacity)

        # Calculate manual allowances (active vs expired)
        active_allowances, expired_allowances = (
            SGMEngine.calculate_active_manual_allowances(manual_allowances, day_index)
        )

        # Calculate base SGM capacity (subject to wallet cap)
        base_sgm_capacity = min(wallet_start + daily_limit, max_wallet_capacity)

        # Add manual allowances on top (not subject to wallet cap)
        wallet_available = base_sgm_capacity + active_allowances

        # Step 4: Handle remaining spend through SGM
        sgm_spend = min(remaining_spend, wallet_available)

        # Calculate how much was spent from wallet vs manual allowances
        sgm_from_wallet = min(sgm_spend, base_sgm_capacity)
        sgm_from_manual = sgm_spend - sgm_from_wallet

        # Update wallet balance (only affected by wallet spending, not manual allowance spending)
        wallet_end = base_sgm_capacity - sgm_from_wallet

        # Per PRD: Manual allowances do not accumulate or carry over between days

        # Step 5: Calculate totals and intervention
        total_accepted = reserved_spend + sgm_spend
        total_rejected = requested_spend - total_accepted
        manual_allowances_used = sgm_from_manual

        # Determine intervention based on SGM rejection only (not reserved)
        intervention = "none"
        if remaining_spend > 0 and sgm_spend < remaining_spend:
            # Only count SGM rejections for intervention logic
            sgm_rejection_rate = (remaining_spend - sgm_spend) / remaining_spend
            if sgm_rejection_rate >= 0.9:  # 90% or more of SGM request rejected
                intervention = "shutdown"
            elif sgm_rejection_rate > 0:
                intervention = "throttle"

        # Calculate remaining reserved volume
        if reserved_config:
            reserved_remaining = max(
                0, reserved_config.monthly_volume - new_cumulative_reserved
            )

        result = DayResult(
            day_index=day_index,
            billing_day=billing_day,
            requested_spend=requested_spend,
            accepted_spend=total_accepted,
            rejected_spend=total_rejected,
            reserved_spend=reserved_spend,
            sgm_spend=sgm_spend,
            daily_spend_limit=daily_limit,
            wallet_balance_start=wallet_start,
            wallet_balance_end=wallet_end,
            wallet_max_capacity=max_wallet_capacity,
            intervention_type=intervention,
            reserved_remaining=reserved_remaining,
            cumulative_reserved_used=new_cumulative_reserved,
            manual_allowances_used=manual_allowances_used,
            expired_allowances=expired_allowances,
        )

        return result, updated_last_recalc_day, updated_baseline


# =============================================================================
# SCENARIO GENERATION
# =============================================================================


def create_usage_scenarios() -> Dict[str, List[float]]:
    """Create predefined usage scenarios"""
    return {
        "steady_growth": [50 + i * 2 for i in range(30)],
        "traffic_spike": [30] * 10 + [150] * 3 + [30] * 17,
        "gradual_ramp": [10 * (1.1**i) for i in range(30)],
        "weekend_spikes": [30 if i % 7 < 5 else 80 for i in range(30)],
        "developer_mistake": [20] * 9 + [500] + [20] * 20,
        "viral_moment": [50] * 14 + [200, 250, 300, 350, 400] + [100] * 11,
        "random_variation": [30 + (i * 17 + i * i * 3) % 40 for i in range(30)],
    }


# =============================================================================
# CLI INTERFACE
# =============================================================================


def run_cli():
    """CLI interface for the simulator"""
    parser = argparse.ArgumentParser(description="SGM Simulator CLI")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode")
    parser.add_argument(
        "--scenario",
        choices=list(create_usage_scenarios().keys()),
        default="developer_mistake",
        help="Scenario to run",
    )
    parser.add_argument(
        "--growth-pct", type=float, default=20.0, help="Growth percentage (5-50)"
    )
    parser.add_argument(
        "--min-dollars",
        type=float,
        default=20.0,
        help="Minimum growth dollars per week",
    )
    parser.add_argument(
        "--reserved-volume",
        type=float,
        default=0.0,
        help="Monthly reserved volume in dollars",
    )
    parser.add_argument(
        "--billing-day", type=int, default=1, help="Starting billing day (1-30)"
    )
    parser.add_argument(
        "--output",
        choices=["summary", "detailed", "json"],
        default="summary",
        help="Output format",
    )

    args = parser.parse_args()

    # Setup
    rule = SGMRule("CLI Rule", args.growth_pct, args.min_dollars)
    reserved = (
        ReservedVolumesConfig(args.reserved_volume, args.billing_day)
        if args.reserved_volume > 0
        else None
    )
    scenarios = create_usage_scenarios()
    daily_spends = scenarios[args.scenario]

    # Run simulation
    results = []
    wallet_balance = 0.0
    accepted_history = []
    billing_day = args.billing_day if reserved else 1
    cumulative_reserved = 0.0
    last_recalc_day = 0
    baseline_spend = None

    for day_index, spend in enumerate(daily_spends):
        result, last_recalc_day, baseline_spend = SGMEngine.simulate_day(
            day_index=day_index,
            billing_day=billing_day,
            requested_spend=spend,
            wallet_balance=wallet_balance,
            accepted_history=accepted_history,
            rule=rule,
            wallet_config=WalletConfig(),
            reserved_config=reserved,
            cumulative_reserved_used=cumulative_reserved,
            manual_allowances=[],
            last_recalc_day=last_recalc_day,
            baseline_spend=baseline_spend,
        )
        results.append(result)

        # Update state for next iteration
        wallet_balance = result.wallet_balance_end
        accepted_history.append(
            result.accepted_spend
        )  # CRITICAL FIX: Use total accepted spend
        cumulative_reserved = result.cumulative_reserved_used
        if reserved:
            billing_day = reserved.advance_billing_day(billing_day)
            # Reset cumulative on new billing cycle
            if billing_day == 1:
                cumulative_reserved = 0.0

    # Output results
    if args.output == "json":
        print(
            json.dumps(
                [
                    {
                        "day": r.day_index,
                        "billing_day": r.billing_day,
                        "requested": r.requested_spend,
                        "accepted": r.accepted_spend,
                        "rejected": r.rejected_spend,
                        "reserved": r.reserved_spend,
                        "sgm": r.sgm_spend,
                        "wallet": r.wallet_balance_end,
                        "limit": r.daily_spend_limit,
                        "intervention": r.intervention_type,
                        "reserved_remaining": r.reserved_remaining,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
    elif args.output == "detailed":
        print(f"SGM Simulation: {args.scenario}")
        print(f"Rule: {args.growth_pct}% growth, ${args.min_dollars}/week minimum")
        if reserved:
            print(
                f"Reserved: ${args.reserved_volume}/month starting day {args.billing_day}"
            )
        print("-" * 90)
        print(
            "Day | Requested | Accepted | Reserved | SGM | Rejected | Wallet | Limit  | Status"
        )
        print("-" * 90)
        for r in results:
            print(
                f"{r.day_index:3d} | ${r.requested_spend:8.2f} | ${r.accepted_spend:7.2f} | "
                f"${r.reserved_spend:7.2f} | ${r.sgm_spend:6.2f} | ${r.rejected_spend:7.2f} | "
                f"${r.wallet_balance_end:6.2f} | ${r.daily_spend_limit:6.2f} | {r.intervention_type}"
            )
    else:  # summary
        total_requested = sum(r.requested_spend for r in results)
        total_accepted = sum(r.accepted_spend for r in results)
        total_rejected = sum(r.rejected_spend for r in results)
        total_reserved = sum(r.reserved_spend for r in results)
        total_sgm = sum(r.sgm_spend for r in results)
        interventions = sum(1 for r in results if r.intervention_type != "none")

        print(f"SIMULATION SUMMARY: {args.scenario}")
        print(f"Total Days: {len(results)}")
        print(f"Total Requested: ${total_requested:.2f}")
        print(f"Total Accepted: ${total_accepted:.2f}")
        if reserved:
            print(f"  - From Reserved: ${total_reserved:.2f}")
            print(f"  - From SGM: ${total_sgm:.2f}")
        print(f"Total Rejected: ${total_rejected:.2f}")
        acceptance_rate = (total_accepted/total_requested)*100 if total_requested > 0 else 0
        print(f"Acceptance Rate: {acceptance_rate:.1f}%")
        print(f"Intervention Days: {interventions}")

        # Show first week detail to demonstrate bootstrap fix
        print("\nFirst Week Bootstrap Period:")
        week_requested = sum(r.requested_spend for r in results[:7])
        week_accepted = sum(r.accepted_spend for r in results[:7])
        week_sgm = sum(r.sgm_spend for r in results[:7])
        print(f"Week 1 Requested: ${week_requested:.2f}")
        print(f"Week 1 Accepted: ${week_accepted:.2f}")
        print(f"Week 1 SGM Accepted: ${week_sgm:.2f}")
        week_acceptance_rate = (week_accepted/week_requested)*100 if week_requested > 0 else 0
        print(f"Week 1 Acceptance Rate: {week_acceptance_rate:.1f}%")


# =============================================================================
# STREAMLIT UI
# =============================================================================

# Only import streamlit if not in CLI mode
if "--cli" not in sys.argv and len(sys.argv) <= 1:
    import streamlit as st

    # Page config
    st.set_page_config(
        page_title="SGM Simulator",
        page_icon="💰",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Initialize session state with proper structure
    if "simulation_days" not in st.session_state:
        st.session_state.simulation_days = []  # List of DayResult objects
        st.session_state.current_day_index = -1  # Which day we're viewing
        st.session_state.wallet_balance = 0.0
        st.session_state.accepted_history = []  # For SGM calculations
        st.session_state.billing_day = 1
        st.session_state.cumulative_reserved = 0.0
        st.session_state.baseline_spend = None  # For weekly recalculation
        st.session_state.invoices = []  # List of Invoice objects

    # Sidebar controls
    st.sidebar.title("🎛️ SGM Controls")

    # Rule configuration
    st.sidebar.subheader("📊 SGM Rule Configuration")
    with st.sidebar.expander("ℹ️ What is an SGM Rule?", expanded=False):
        st.write(
            """
        **SGM (Spend Growth Management)** rules control how much your daily spending limit can grow over time.
        
        **Key Concepts:**
        - **Growth Percentage**: How much your spending can increase per "week" (as a %)
        - **Minimum Growth**: The minimum dollar amount your spending can increase per "week"
        - **Daily Limit**: How much you can spend each day (calculated by the SGM algorithm)
        
        **⚠️ IMPORTANT:** "Weekly" means any rolling 7-day period, NOT calendar weeks!
        The algorithm looks at the last 7 days continuously - there are no "week boundary" loopholes.
        """
        )

    rule_name = st.sidebar.text_input("Rule Name", value="Default Rule")

    growth_pct = st.sidebar.slider(
        "Growth % (per rolling 7 days)",
        5.0,
        50.0,
        20.0,
        1.0,
        help="Maximum percentage your spending can grow over any 7-day rolling period. 20% means if you spent \\$100 in the last 7 days, you can spend up to \\$120 in the next 7 days.",
    )

    min_dollars = st.sidebar.number_input(
        "Min Growth ($/rolling 7 days)",
        20.0,
        100.0,
        20.0,
        5.0,
        help="Minimum dollar amount your spending can increase over any 7-day rolling period, regardless of percentage. Ensures meaningful growth even for small amounts.",
    )

    # Show current rule formula
    st.sidebar.info(
        f"**Current Rule:** {growth_pct}% per rolling 7 days OR ${min_dollars} per rolling 7 days (whichever is higher)"
    )

    rule = SGMRule(rule_name, growth_pct, min_dollars)

    # Wallet configuration
    st.sidebar.subheader("💼 Wallet Configuration")
    with st.sidebar.expander("ℹ️ What is the Wallet?", expanded=False):
        st.write(
            """
        **The Wallet** is your spending buffer that accumulates unused daily limits.
        
        **How it works:**
        1. Each day, your daily spending limit is added to your wallet
        2. When you spend money, it comes from your wallet first
        3. Your wallet has a maximum capacity to prevent unlimited accumulation
        
        **Example:** If your daily limit is $10 and you only spend $7, then $3 goes into your wallet for future use.
        """
        )

    wallet_model = st.sidebar.selectbox(
        "Wallet Capacity Model",
        options=["daily_limit_2x", "three_day_budget"],
        index=0,
        help="Choose how much your wallet can hold. This affects how much you can 'save up' for larger purchases.",
    )

    # Show detailed explanation of selected model behind expander
    with st.sidebar.expander("📘 Wallet Capacity Model Details", expanded=False):
        if wallet_model == "daily_limit_2x":
            st.success(
                """
            **PRFAQ Model Selected:** 2× Daily Limit
            
            **Formula:** `Wallet Capacity = Daily Limit × 2`
            
            **What this means:**
            - You can save up unused spending for up to 2 days
            - Prevents unlimited accumulation ("use it or lose it")
            - Allows moderate spending bursts when needed
            
            **Example:** If daily limit is \\$50, wallet caps at \\$100.
            - Day 1: Spend \\$20, save \\$30 → Wallet: \\$30
            - Day 2: Spend \\$10, save \\$40 → Wallet: \\$70  
            - Day 3: Can spend up to \\$120 (\\$50 daily + \\$70 saved)
            
            **Use Case:** Good for moderate bursts in spending.
            """
            )
        else:
            st.success(
                """
            **PRD Model Selected:** 3-Day Budget
            
            **Formula:** `Wallet Capacity = Daily Limit × 3`
            
            **What this means:**
            - You can save up unused spending for up to 3 days
            - Prevents unlimited accumulation ("use it or lose it")
            - Allows larger spending bursts when needed
            
            **Example:** If daily limit is \\$50, wallet caps at \\$150.
            - Day 1: Spend \\$20, save \\$30 → Wallet: \\$30
            - Day 2: Spend \\$10, save \\$40 → Wallet: \\$70
            - Day 3: Spend \\$5, save \\$45 → Wallet: \\$115
            - Day 4: Can spend up to \\$165 (\\$50 daily + \\$115 saved)
            
            **Use Case:** Better for larger planned purchases or weekend spending.
            """
            )

    wallet_config = WalletConfig(model=wallet_model)

    # Reserved volumes
    st.sidebar.subheader("📦 Reserved Volumes")
    with st.sidebar.expander("ℹ️ What are Reserved Volumes?", expanded=False):
        st.write(
            """
        **Reserved Volumes** are pre-paid monthly spending quotas that get used BEFORE your SGM wallet.
        
        **How it works:**
        1. Reserved volume is consumed first for any spending request
        2. Only after reserved volume is exhausted does SGM wallet kick in
        3. Reserved volume resets every billing cycle (30 days)
        
        **Use Case:** Think of this like a monthly allowance or pre-paid credit.
        """
        )

    monthly_volume = st.sidebar.number_input(
        "Monthly Volume ($)",
        0.0,
        10000.0,
        100.0,
        100.0,
        help="Total amount of pre-paid spending available each month. Set to 0 to disable.",
    )

    if monthly_volume > 0:
        billing_start = st.sidebar.number_input(
            "Starting Billing Day",
            1,
            30,
            1,
            help="Which day of the month your billing cycle starts (1-30). Reserved volume resets on this day.",
        )
        reserved = ReservedVolumesConfig(monthly_volume, billing_start)

        # Show current status with formula
        if st.session_state.simulation_days:
            last_day = st.session_state.simulation_days[-1]
            remaining = last_day.reserved_remaining
            used = last_day.cumulative_reserved_used
            st.sidebar.info(
                f"""
            **Reserved Volume Status:**
            - **Used:** \\${used:.2f} / \\${monthly_volume:.2f}
            - **Remaining:** \\${remaining:.2f}
            - **Formula:** `Remaining = Monthly Volume - Used`
            """
            )
        else:
            st.sidebar.info(f"**Available:** \\${monthly_volume:.2f} (unused)")
    else:
        reserved = None
        st.sidebar.info("**Reserved Volumes:** Disabled")

    # Manual daily input
    st.sidebar.subheader("💰 Daily Usage Settings")
    with st.sidebar.expander("ℹ️ Understanding Daily Settings", expanded=False):
        st.write(
            """
        **Daily Spend:** How much you want to spend today.
        
        **Manual Allowance:** Emergency budget that bypasses normal wallet caps.
        
        **Spending Priority Order:**
        1. **Reserved Volume** (if available)
        2. **SGM Wallet** (your normal daily limit + accumulated balance)
        3. **Manual Allowance** (emergency override)
        """
        )

    daily_spend = st.sidebar.number_input(
        "Daily Spend (\\$)",
        0.0,
        1000.0,
        5.0,
        5.0,
        help="Amount you want to spend today. This will be processed through Reserved Volume → SGM Wallet → Manual Allowance.",
    )

    manual_allowance = st.sidebar.number_input(
        "Manual Allowance (\\$)",
        0.0,
        1000.0,
        0.0,
        10.0,
        help="Emergency budget that bypasses normal SGM wallet limits. Applied ONLY to the next single day you simulate.",
    )

    # Show manual allowance explanation with current status
    if manual_allowance > 0:
        st.sidebar.success(
            f"""
        **🚨 Manual Override Set: \\${manual_allowance:.2f}**
        
        **What happens next:**
        - Your next simulated day will have \\${manual_allowance:.2f} emergency budget
        - This bypasses normal wallet capacity limits
        - Used AFTER Reserved Volume and SGM Wallet are exhausted
        - Applies to ONE day only, then resets to \\$0
        
        **Use case:** Emergency spending that can't wait for normal SGM growth
        """
        )
    else:
        st.sidebar.info("**Manual Allowance:** \\$0 (no emergency budget set)")

    # Add detailed manual allowance explainer
    with st.sidebar.expander("🚨 How Manual Allowance Works", expanded=False):
        st.markdown(
            """
        **Manual Allowance** is an emergency override system that bypasses normal SGM limits.
        
        ### 🔄 **Spending Priority Order:**
        When you request spending, it's processed in this order:
        1. **Reserved Volume** (if available)
        2. **SGM Wallet** (daily limit + accumulated balance, capped at wallet capacity)
        3. **Manual Allowance** (emergency override, no capacity limits)
        
        ### ⚡ **Key Features:**
        - **Bypasses Wallet Caps:** Ignores normal wallet capacity limits
        - **Single Use:** Applied only to the next day you simulate
        - **Emergency Purpose:** For urgent spending that can't wait for SGM growth
        - **No Accumulation:** Doesn't carry over between days
        
        ### 💡 **Example Scenario:**
        - Daily Limit: \\$50, Wallet: \\$30, Manual: \\$200
        - Available for spending: \\$50 + \\$30 + \\$200 = \\$280
        - Without manual: Only \\$80 would be available
        
        ### 🎯 **When to Use:**
        - Traffic spikes requiring immediate scaling
        - Emergency feature deployments
        - Critical incidents needing extra data collection
        - Testing high-spend scenarios
        """
        )

    # Show current manual allowance impact preview
    if manual_allowance > 0 and st.session_state.simulation_days:
        last_day = st.session_state.simulation_days[-1]
        current_capacity = min(
            st.session_state.wallet_balance + last_day.daily_spend_limit,
            last_day.wallet_max_capacity,
        )
        total_with_manual = current_capacity + manual_allowance

        st.sidebar.warning(
            f"""
        **💰 Next Day Spending Capacity Preview:**
        - Normal SGM capacity: ~\\${current_capacity:.2f}
        - With manual allowance: ~\\${total_with_manual:.2f}
        - **Boost:** +\\${manual_allowance:.2f} ({((manual_allowance/current_capacity)*100) if current_capacity > 0 else 0:.0f}% increase)
        """
        )

    # Scenarios
    st.sidebar.subheader("📊 Quick Scenarios")
    with st.sidebar.expander("ℹ️ What are Scenarios?", expanded=False):
        st.write(
            """
        **Pre-built spending patterns** to demonstrate how SGM responds to different situations:
        
        - **Steady Growth**: Consistent daily increases
        - **Traffic Spike**: Sudden usage bursts 
        - **Gradual Ramp**: Slow organic growth
        - **Weekend Spikes**: Weekly patterns
        - **Developer Mistake**: Accidental high usage
        - **Viral Moment**: Exponential growth event
        - **Random Variation**: Unpredictable usage
        """
        )

    scenarios = create_usage_scenarios()
    scenario_names = {
        "steady_growth": "📈 Steady Growth",
        "traffic_spike": "⚡ Traffic Spike",
        "gradual_ramp": "🚀 Gradual Ramp",
        "weekend_spikes": "📅 Weekend Spikes",
        "developer_mistake": "🐛 Developer Mistake",
        "viral_moment": "🔥 Viral Moment",
        "random_variation": "🎲 Random Variation",
    }

    scenario = st.sidebar.selectbox(
        "Load Scenario",
        ["🎛️ Custom"] + list(scenario_names.values()),
        help="Choose a pre-built scenario to see how SGM handles different spending patterns",
    )

    if scenario != "🎛️ Custom":
        scenario_key = {v: k for k, v in scenario_names.items()}[scenario]
        if st.sidebar.button(f"Load {scenario}", use_container_width=True):
            # Reset and run full scenario
            st.session_state.simulation_days = []
            st.session_state.wallet_balance = 0.0
            st.session_state.accepted_history = []
            st.session_state.billing_day = reserved.billing_day_start if reserved else 1
            st.session_state.invoices = []
            st.session_state.cumulative_reserved = 0.0
            st.session_state.baseline_spend = None

            # Convert manual allowance to ManualAllowance objects for scenario loading
            current_manual_allowances = []
            if manual_allowance > 0:
                current_manual_allowances.append(
                    ManualAllowance(
                        amount=manual_allowance,
                        created_day=0,
                        expiration_days=1,  # Expires after 1 day per PRD requirements
                        reason="Scenario loading manual allowance",
                    )
                )

            # Run entire scenario
            for day_index, spend in enumerate(scenarios[scenario_key]):
                # Advance billing day before simulation (except for day 0)
                if reserved and day_index > 0:
                    st.session_state.billing_day = reserved.advance_billing_day(
                        st.session_state.billing_day
                    )
                    if st.session_state.billing_day == 1:
                        st.session_state.cumulative_reserved = 0.0

                # Initialize session state for recalc tracking if needed
                if "last_recalc_day" not in st.session_state:
                    st.session_state.last_recalc_day = 0

                (
                    result,
                    st.session_state.last_recalc_day,
                    st.session_state.baseline_spend,
                ) = SGMEngine.simulate_day(
                    day_index=day_index,
                    billing_day=st.session_state.billing_day,
                    requested_spend=spend,
                    wallet_balance=st.session_state.wallet_balance,
                    accepted_history=st.session_state.accepted_history,
                    rule=rule,
                    wallet_config=wallet_config,
                    reserved_config=reserved,
                    cumulative_reserved_used=st.session_state.cumulative_reserved,
                    manual_allowances=current_manual_allowances,
                    last_recalc_day=st.session_state.last_recalc_day,
                    baseline_spend=st.session_state.baseline_spend,
                )
                st.session_state.simulation_days.append(result)
                st.session_state.wallet_balance = result.wallet_balance_end
                st.session_state.accepted_history.append(result.accepted_spend)
                st.session_state.cumulative_reserved = result.cumulative_reserved_used

            st.session_state.current_day_index = (
                len(st.session_state.simulation_days) - 1
            )
            st.rerun()

    # Main content area
    st.title("💰 Spend Growth Management (SGM) Simulator")

    # Add comprehensive explanation at the top
    with st.expander("📚 What is Spend Growth Management (SGM)?", expanded=False):
        st.markdown(
            """
        **SGM** is a system that controls how much you can spend each day, with built-in growth limits to prevent runaway costs.
        
        ### 🧠 **Core Algorithm (PRFAQ Formula)**
        The daily spending limit is calculated using this formula:
        
        ```
        Daily Limit = max(
            recent_7_days × (1 + growth_percentage/100)^(1/7) - recent_6_days,
            recent_7_days + min_growth_dollars/7 - recent_6_days,
            0
        )
        ```
        
        **In Plain English:**
        - Look at your spending over the last 7 days
        - Calculate two possible growth amounts (percentage-based and dollar-based)
        - Use whichever growth amount is higher
        - This becomes your daily spending limit
        
        ### 📊 **Key Components:**
        
        **1. Bootstrap Period (Days 0-6):** 
        - Uses simple logic to establish initial spending patterns
        - Formula: `Daily Limit = min_growth_dollars / 7`
        
        **2. PRFAQ Algorithm (Day 7+):**
        - Uses the sophisticated rolling-window formula above
        - Adapts to your actual spending patterns
        
        **3. Wallet System:**
        - Accumulates unused daily limits for future use
        - Has a maximum capacity to prevent unlimited hoarding
        
        **4. Spending Priority:**
        1. **Reserved Volume** (pre-paid monthly allowance)
        2. **SGM Wallet** (accumulated daily limits)
        3. **Manual Allowance** (emergency override)
        """
        )

    st.markdown(
        "*Interactive simulator to understand SGM behavior with detailed explanations*"
    )

    # Helper functions for simulation
    def generate_invoice_for_completed_cycle(current_day_index):
        """Generate invoice for the just-completed billing cycle"""
        if not reserved or not hasattr(st.session_state, "invoices"):
            return

        # Find the start of the completed billing cycle
        cycle_length = reserved.days_in_cycle
        cycle_start_day = max(0, current_day_index - cycle_length)

        # Calculate accumulated SGM spend during this cycle
        accumulated_sgm = 0.0
        cycle_days = []

        for day_idx in range(len(st.session_state.simulation_days)):
            day_result = st.session_state.simulation_days[day_idx]
            # Include days that were part of the completed billing cycle
            if cycle_start_day <= day_idx < current_day_index:
                accumulated_sgm += day_result.sgm_spend
                cycle_days.append(day_result)

        # Generate invoice
        billing_cycle_number = len(st.session_state.invoices) + 1
        invoice = Invoice(
            billing_cycle=billing_cycle_number,
            cycle_start_day=cycle_start_day,
            cycle_end_day=current_day_index - 1,
            prepaid_reserved=reserved.monthly_volume,
            accumulated_sgm=accumulated_sgm,
            total_amount=reserved.monthly_volume + accumulated_sgm,
            generated_on_day=current_day_index,
        )

        st.session_state.invoices.append(invoice)

    def get_current_billing_cycle_data():
        """Get data for the current (incomplete) billing cycle"""
        if not reserved or not st.session_state.simulation_days:
            return {
                "days": [],
                "accumulated_sgm": 0.0,
                "accumulated_reserved": 0.0,
                "accumulated_accepted": 0.0,
                "accumulated_rejected": 0.0,
                "days_in_cycle": 0,
                "cycle_start_day": 0,
            }

        # Find the start of current billing cycle
        cycle_length = reserved.days_in_cycle
        current_day = len(st.session_state.simulation_days)

        # Find when the current cycle started (last time billing_day was 1)
        cycle_start_day = 0
        for i in range(len(st.session_state.simulation_days) - 1, -1, -1):
            day_result = st.session_state.simulation_days[i]
            if day_result.billing_day == 1:
                cycle_start_day = i
                break

        # Collect days from current cycle
        current_cycle_days = []
        accumulated_sgm = 0.0
        accumulated_reserved = 0.0
        accumulated_accepted = 0.0
        accumulated_rejected = 0.0

        for i in range(cycle_start_day, len(st.session_state.simulation_days)):
            day_result = st.session_state.simulation_days[i]
            current_cycle_days.append(day_result)
            accumulated_sgm += day_result.sgm_spend
            accumulated_reserved += day_result.reserved_spend
            accumulated_accepted += day_result.accepted_spend
            accumulated_rejected += day_result.rejected_spend

        # Calculate forecasting data
        forecast_data = {}
        if len(current_cycle_days) >= 2:  # Need at least 2 days for trend analysis
            # Calculate daily averages for forecasting
            avg_daily_sgm = accumulated_sgm / len(current_cycle_days)
            avg_daily_reserved = accumulated_reserved / len(current_cycle_days)
            avg_daily_accepted = accumulated_accepted / len(current_cycle_days)

            # Calculate remaining days in cycle
            current_billing_day = st.session_state.billing_day
            days_remaining = reserved.days_in_cycle - current_billing_day

            # Forecast remaining spending
            forecast_sgm = avg_daily_sgm * days_remaining
            forecast_reserved = avg_daily_reserved * days_remaining
            forecast_accepted = avg_daily_accepted * days_remaining

            # Total projected spending for full cycle
            projected_total_sgm = accumulated_sgm + forecast_sgm
            projected_total_reserved = accumulated_reserved + forecast_reserved
            projected_total_accepted = accumulated_accepted + forecast_accepted

            # Projected invoice amount
            projected_invoice = reserved.monthly_volume + projected_total_sgm

            forecast_data = {
                "avg_daily_sgm": avg_daily_sgm,
                "avg_daily_reserved": avg_daily_reserved,
                "avg_daily_accepted": avg_daily_accepted,
                "days_remaining": days_remaining,
                "forecast_sgm": forecast_sgm,
                "forecast_reserved": forecast_reserved,
                "forecast_accepted": forecast_accepted,
                "projected_total_sgm": projected_total_sgm,
                "projected_total_reserved": projected_total_reserved,
                "projected_total_accepted": projected_total_accepted,
                "projected_invoice": projected_invoice,
                "has_forecast": True,
            }
        else:
            forecast_data = {"has_forecast": False}

        return {
            "days": current_cycle_days,
            "accumulated_sgm": accumulated_sgm,
            "accumulated_reserved": accumulated_reserved,
            "accumulated_accepted": accumulated_accepted,
            "accumulated_rejected": accumulated_rejected,
            "days_in_cycle": len(current_cycle_days),
            "cycle_start_day": cycle_start_day,
            "forecast": forecast_data,
        }

    def simulate_next_day(auto_advance=False):
        """Simulate next day"""
        day_index = len(st.session_state.simulation_days)

        # Advance billing day before simulation (except for day 0)
        if reserved and day_index > 0:
            st.session_state.billing_day = reserved.advance_billing_day(
                st.session_state.billing_day
            )
            if st.session_state.billing_day == 1:
                # Generate invoice for completed billing cycle before resetting
                if (
                    hasattr(st.session_state, "invoices")
                    and len(st.session_state.simulation_days) > 0
                ):
                    generate_invoice_for_completed_cycle(day_index)
                st.session_state.cumulative_reserved = 0.0

        # Initialize session state for recalc tracking if needed
        if "last_recalc_day" not in st.session_state:
            st.session_state.last_recalc_day = 0

        # Convert manual allowance to ManualAllowance object
        manual_allowances = []
        if manual_allowance > 0:
            manual_allowances.append(
                ManualAllowance(
                    amount=manual_allowance,
                    created_day=day_index,
                    expiration_days=1,  # Expires after 1 day per PRD requirements
                    reason="Single day manual allowance",
                )
            )

        result, st.session_state.last_recalc_day, st.session_state.baseline_spend = (
            SGMEngine.simulate_day(
                day_index=day_index,
                billing_day=st.session_state.billing_day,
                requested_spend=daily_spend,
                wallet_balance=st.session_state.wallet_balance,
                accepted_history=st.session_state.accepted_history,
                rule=rule,
                wallet_config=wallet_config,
                reserved_config=reserved,
                cumulative_reserved_used=st.session_state.cumulative_reserved,
                manual_allowances=manual_allowances,
                last_recalc_day=st.session_state.last_recalc_day,
                baseline_spend=st.session_state.baseline_spend,
            )
        )

        # Update state
        st.session_state.simulation_days.append(result)
        st.session_state.wallet_balance = result.wallet_balance_end
        st.session_state.accepted_history.append(result.accepted_spend)
        st.session_state.cumulative_reserved = result.cumulative_reserved_used
        st.session_state.current_day_index = len(st.session_state.simulation_days) - 1
        st.rerun()

    def undo_last_day():
        """Undo last day"""
        if st.session_state.simulation_days:
            # Check if undoing this day should also remove an invoice
            last_day = st.session_state.simulation_days[-1]
            should_remove_invoice = False

            # If this day caused a billing cycle reset, we need to remove the latest invoice
            if hasattr(st.session_state, "invoices") and st.session_state.invoices:
                latest_invoice = st.session_state.invoices[-1]
                # Check if the latest invoice was generated on this day
                if latest_invoice.generated_on_day == len(
                    st.session_state.simulation_days
                ):
                    should_remove_invoice = True

            # Remove last day
            st.session_state.simulation_days.pop()
            st.session_state.accepted_history.pop()

            # Remove invoice if it was generated by this day
            if should_remove_invoice:
                st.session_state.invoices.pop()

            # Restore previous state
            if st.session_state.simulation_days:
                last_day = st.session_state.simulation_days[-1]
                st.session_state.wallet_balance = last_day.wallet_balance_end
                st.session_state.billing_day = last_day.billing_day
                st.session_state.cumulative_reserved = last_day.cumulative_reserved_used
                st.session_state.current_day_index = (
                    len(st.session_state.simulation_days) - 1
                )
            else:
                st.session_state.wallet_balance = 0.0
                st.session_state.billing_day = (
                    reserved.billing_day_start if reserved else 1
                )
                st.session_state.cumulative_reserved = 0.0
                st.session_state.current_day_index = -1
            st.rerun()

    def simulate_next_week():
        """Simulate next 7 days"""
        # Convert manual allowance to ManualAllowance objects for bulk simulation
        current_manual_allowances = []
        if manual_allowance > 0:
            current_manual_allowances.append(
                ManualAllowance(
                    amount=manual_allowance,
                    created_day=len(st.session_state.simulation_days),
                    expiration_days=1,  # Expires after 1 day per PRD requirements
                    reason="Bulk simulation manual allowance",
                )
            )

        for _ in range(7):
            day_index = len(st.session_state.simulation_days)

            # Advance billing day before simulation
            if reserved and day_index > 0:
                st.session_state.billing_day = reserved.advance_billing_day(
                    st.session_state.billing_day
                )
                if st.session_state.billing_day == 1:
                    # Generate invoice for completed billing cycle before resetting
                    if (
                        hasattr(st.session_state, "invoices")
                        and len(st.session_state.simulation_days) > 0
                    ):
                        generate_invoice_for_completed_cycle(day_index)
                    st.session_state.cumulative_reserved = 0.0

            # Initialize session state for recalc tracking if needed
            if "last_recalc_day" not in st.session_state:
                st.session_state.last_recalc_day = 0

            (
                result,
                st.session_state.last_recalc_day,
                st.session_state.baseline_spend,
            ) = SGMEngine.simulate_day(
                day_index=day_index,
                billing_day=st.session_state.billing_day,
                requested_spend=daily_spend,
                wallet_balance=st.session_state.wallet_balance,
                accepted_history=st.session_state.accepted_history,
                rule=rule,
                wallet_config=wallet_config,  # Use the actual wallet_config, not WalletConfig()
                reserved_config=reserved,
                cumulative_reserved_used=st.session_state.cumulative_reserved,
                manual_allowances=current_manual_allowances,
                last_recalc_day=st.session_state.last_recalc_day,
                baseline_spend=st.session_state.baseline_spend,
            )

            # Update state
            st.session_state.simulation_days.append(result)
            st.session_state.wallet_balance = result.wallet_balance_end
            st.session_state.accepted_history.append(result.accepted_spend)
            st.session_state.cumulative_reserved = result.cumulative_reserved_used

        st.session_state.current_day_index = len(st.session_state.simulation_days) - 1
        st.rerun()

    def simulate_next_month():
        """Simulate next 30 days"""
        # Convert manual allowance to ManualAllowance objects for bulk simulation
        current_manual_allowances = []
        if manual_allowance > 0:
            current_manual_allowances.append(
                ManualAllowance(
                    amount=manual_allowance,
                    created_day=len(st.session_state.simulation_days),
                    expiration_days=1,  # Expires after 1 day per PRD requirements
                    reason="Bulk simulation manual allowance",
                )
            )

        for _ in range(30):
            day_index = len(st.session_state.simulation_days)

            # Advance billing day before simulation
            if reserved and day_index > 0:
                st.session_state.billing_day = reserved.advance_billing_day(
                    st.session_state.billing_day
                )
                if st.session_state.billing_day == 1:
                    # Generate invoice for completed billing cycle before resetting
                    if (
                        hasattr(st.session_state, "invoices")
                        and len(st.session_state.simulation_days) > 0
                    ):
                        generate_invoice_for_completed_cycle(day_index)
                    st.session_state.cumulative_reserved = 0.0

            # Initialize session state for recalc tracking if needed
            if "last_recalc_day" not in st.session_state:
                st.session_state.last_recalc_day = 0

            (
                result,
                st.session_state.last_recalc_day,
                st.session_state.baseline_spend,
            ) = SGMEngine.simulate_day(
                day_index=day_index,
                billing_day=st.session_state.billing_day,
                requested_spend=daily_spend,
                wallet_balance=st.session_state.wallet_balance,
                accepted_history=st.session_state.accepted_history,
                rule=rule,
                wallet_config=wallet_config,
                reserved_config=reserved,
                cumulative_reserved_used=st.session_state.cumulative_reserved,
                manual_allowances=current_manual_allowances,
                last_recalc_day=st.session_state.last_recalc_day,
                baseline_spend=st.session_state.baseline_spend,
            )

            # Update state
            st.session_state.simulation_days.append(result)
            st.session_state.wallet_balance = result.wallet_balance_end
            st.session_state.accepted_history.append(result.accepted_spend)
            st.session_state.cumulative_reserved = result.cumulative_reserved_used

        st.session_state.current_day_index = len(st.session_state.simulation_days) - 1
        st.rerun()

    # Unified Controls Section
    st.markdown("**🎮 Controls**")

    if st.session_state.simulation_days:
        # Navigation controls in a tight row
        nav_col1, nav_col2, nav_col3, nav_col4, nav_col5 = st.columns([1, 1, 3, 1, 1])

        with nav_col1:
            if st.button(
                "⏮️ Back 7 Days",
                help="Jump back 7 days",
                use_container_width=True,
                key="nav_back_week",
            ):
                new_index = max(0, st.session_state.current_day_index - 7)
                if new_index != st.session_state.current_day_index:
                    st.session_state.current_day_index = new_index
                    st.rerun()

        with nav_col2:
            if st.button(
                "⬅️ Previous Day",
                help="Go to previous day",
                use_container_width=True,
                key="nav_back_day",
            ):
                if st.session_state.current_day_index > 0:
                    st.session_state.current_day_index -= 1
                    st.rerun()

        with nav_col3:
            if len(st.session_state.simulation_days) > 1:
                day_to_show = st.selectbox(
                    "Navigate to Day",
                    range(len(st.session_state.simulation_days)),
                    index=st.session_state.current_day_index,
                    format_func=lambda x: f"Day {x + 1}",
                    key="unified_day_selector",
                )
                if day_to_show != st.session_state.current_day_index:
                    st.session_state.current_day_index = day_to_show
                    st.rerun()
            else:
                st.info("📍 Day 1 of 1")

        with nav_col4:
            if st.button(
                "Next Day ➡️",
                help="Go to next day",
                use_container_width=True,
                key="nav_forward_day",
            ):
                max_index = len(st.session_state.simulation_days) - 1
                if st.session_state.current_day_index < max_index:
                    st.session_state.current_day_index += 1
                    st.rerun()

        with nav_col5:
            if st.button(
                "Forward 7 Days ⏭️",
                help="Jump forward 7 days",
                use_container_width=True,
                key="nav_forward_week",
            ):
                max_index = len(st.session_state.simulation_days) - 1
                new_index = min(max_index, st.session_state.current_day_index + 7)
                if new_index != st.session_state.current_day_index:
                    st.session_state.current_day_index = new_index
                    st.rerun()

    # Simulation controls in a tight row
    sim_col1, sim_col2, sim_col3, sim_col4, sim_col5 = st.columns(5)

    with sim_col1:
        if st.button(
            "➕ Add Day",
            type="primary",
            help=f"Simulate 1 day (${daily_spend:.2f})",
            use_container_width=True,
            key="sim_day",
        ):
            simulate_next_day()

    with sim_col2:
        if st.button(
            "📅 Add Week",
            help=f"Simulate 7 days (${daily_spend:.2f}/day)",
            use_container_width=True,
            key="sim_week",
        ):
            simulate_next_week()

    with sim_col3:
        if st.button(
            "🗓️ Add Month",
            help=f"Simulate 30 days (${daily_spend:.2f}/day)",
            use_container_width=True,
            key="sim_month",
        ):
            simulate_next_month()

    with sim_col4:
        if st.button(
            "⏪ Undo", help="Undo last day", use_container_width=True, key="sim_undo"
        ):
            undo_last_day()

    with sim_col5:
        if st.button(
            "🔄 Reset",
            help="Reset simulation",
            use_container_width=True,
            key="sim_reset",
        ):
            st.session_state.simulation_days = []
            st.session_state.current_day_index = -1
            st.session_state.wallet_balance = 0.0
            st.session_state.accepted_history = []
            st.session_state.billing_day = reserved.billing_day_start if reserved else 1
            st.session_state.cumulative_reserved = 0.0
            st.session_state.invoices = []
            st.rerun()

        st.divider()

    if not st.session_state.simulation_days:
        # Initial state - show instructions
        st.info("👆 Use the simulation controls above to start simulating!")

        # Quick start buttons
        st.markdown("### 🏁 Quick Start")
        qs_col1, qs_col2, qs_col3, qs_col4 = st.columns(4)

        with qs_col1:
            if st.button("▶️ Start with 1 Day", use_container_width=True):
                simulate_next_day()

        with qs_col2:
            if st.button("▶️ Start with 1 Week", use_container_width=True):
                simulate_next_week()

        with qs_col3:
            if st.button("▶️ Start with 1 Month", use_container_width=True):
                simulate_next_month()

        with qs_col4:
            if st.button("▶️ Load a Scenario", use_container_width=True):
                st.info("👈 Choose a scenario from the sidebar")

        # Show algorithm explanation
        with st.expander("🧮 SGM Algorithm Details"):
            st.markdown(
                """
            **Key Features:**
            - ✅ **Improved Navigation**: Controls now at the top of the main area
            - ✅ **Quick Access**: All navigation buttons easily reachable
            - ✅ **Better UX**: Clear separation between navigation and simulation
            
            **SGM Algorithm Improvements:**
            - Fixed bootstrap period (first 7 days) for proper growth
            - Reserved volumes work as true prepaid bucket
            - Intervention logic based only on SGM rejections
            
            **Navigation Features:**
            - **Week Jump**: Quickly move ±7 days in the timeline
            - **Day Navigation**: Step through individual days
            - **Time Travel**: Jump to any specific day
            - **Position Indicator**: Always know where you are
            """
            )
    else:
        # Display current day
        current_day = st.session_state.simulation_days[
            st.session_state.current_day_index
        ]

        # Detailed day overview with explanations
        st.subheader(f"📊 Day {current_day.day_index + 1} Results")

        # Key metrics with explanations
        col1, col2, col3, col4, col5 = st.columns(5)

        with col1:
            st.metric(
                "Current Day",
                f"Day {current_day.day_index + 1}",
                help="Simulation day number (starts from 1)",
            )

        with col2:
            week_num = (current_day.day_index // 7) + 1
            day_in_week = (current_day.day_index % 7) + 1
            st.metric(
                "Week",
                f"W{week_num}D{day_in_week}",
                help="Which week and day within that week",
            )

        with col3:
            st.metric(
                "Billing Day",
                f"Day {current_day.billing_day}/30",
                help="Day in monthly billing cycle (resets reserved volumes)",
            )

        with col4:
            wallet_cap = current_day.wallet_max_capacity
            st.metric(
                "SGM Wallet",
                f"${current_day.wallet_balance_end:.2f}",
                delta=f"Cap: ${wallet_cap:.2f}",
                help=f"Remaining wallet balance. Maximum capacity: ${wallet_cap:.2f}",
            )

        with col5:
            status = (
                current_day.intervention_type.title()
                if current_day.intervention_type != "none"
                else "Normal"
            )
            st.metric(
                "Status", status, help="SGM intervention status based on rejection rate"
            )

        # Spending breakdown with formulas
        st.subheader("💰 Spending Breakdown")
        spend_col1, spend_col2 = st.columns(2)

        with spend_col1:
            st.markdown("**📊 Spending Flow:**")
            st.markdown(
                f"""
            - **Requested:** ${current_day.requested_spend:.2f}
            - **Reserved Used:** ${current_day.reserved_spend:.2f}
            - **SGM Used:** ${current_day.sgm_spend:.2f}
            - **Manual Used:** ${current_day.manual_allowances_used:.2f}
            - **✅ Total Accepted:** ${current_day.accepted_spend:.2f}
            - **❌ Rejected:** ${current_day.rejected_spend:.2f}
            """
            )

        with spend_col2:
            st.markdown("**🧮 Spending Flow Formulas:**")

            # Calculate spending priority breakdown
            remaining_after_reserved = max(
                0, current_day.requested_spend - current_day.reserved_spend
            )
            remaining_after_sgm = max(
                0, remaining_after_reserved - current_day.sgm_spend
            )

            st.markdown(
                f"""
            **Priority Order Calculation:**
            ```
            1. Reserved Volume Used:
               = min(Requested, Available Reserved)
               = min(${current_day.requested_spend:.2f}, ${current_day.reserved_remaining + current_day.reserved_spend:.2f})
               = ${current_day.reserved_spend:.2f}
            
            2. Remaining after Reserved:
               = requested_spend - reserved_spend
               = ${current_day.requested_spend:.2f} - ${current_day.reserved_spend:.2f}
               = ${remaining_after_reserved:.2f}
            
            3. SGM Wallet Used:
               Available SGM = min(Wallet + Daily Limit, Max Capacity)
               = min(${current_day.wallet_balance_start:.2f} + ${current_day.daily_spend_limit:.2f}, ${current_day.wallet_max_capacity:.2f})
               = ${min(current_day.wallet_balance_start + current_day.daily_spend_limit, current_day.wallet_max_capacity):.2f}
               
               SGM Used = min(Remaining, Available SGM)
               = min(${remaining_after_reserved:.2f}, ${min(current_day.wallet_balance_start + current_day.daily_spend_limit, current_day.wallet_max_capacity):.2f})
               = ${current_day.sgm_spend:.2f}
            
            4. Manual Allowance Used:
               Remaining after SGM = ${remaining_after_sgm:.2f}
               Manual Used = ${current_day.manual_allowances_used:.2f}
            
            5. Final Results:
               Total Accepted = reserved + sgm_spend + manual_allowances_used
                              = ${current_day.reserved_spend:.2f} + ${current_day.sgm_spend:.2f} + ${current_day.manual_allowances_used:.2f}
                              = ${current_day.accepted_spend:.2f}
               Rejected = requested - accepted
                        = ${current_day.requested_spend:.2f} - ${current_day.accepted_spend:.2f}
                        = ${current_day.rejected_spend:.2f}
            ```
            """
            )

        # Daily Limit calculation - prominent display
        st.subheader("🧮 Daily Limit Calculation")

        # Clear explanation of rolling window vs calendar weeks
        st.warning(
            """
            **⚠️ IMPORTANT: "Weekly" Growth = Rolling 7-Day Window**
            
            SGM doesn't use calendar weeks (Mon-Sun). Instead, it uses a **rolling 7-day window** that updates every day:
            
            • **Today (Day {})**: Looks at spending from Days {} to {}
            • **Tomorrow (Day {})**: Will look at spending from Days {} to {}
            • **Continuous protection**: No "week boundary" loopholes - growth is controlled every single day!
            """.format(
                current_day.day_index + 1,
                max(1, current_day.day_index + 1 - 6),
                current_day.day_index + 1,
                current_day.day_index + 2,
                max(1, current_day.day_index + 2 - 6),
                current_day.day_index + 2,
            )
        )

        # Make the daily limit very prominent
        limit_col1, limit_col2 = st.columns([1, 2])

        with limit_col1:
            st.markdown(
                f"""
            ### 🎯 **${current_day.daily_spend_limit:.2f}**
            **Today's Daily Spending Limit**
            """
            )

        with limit_col2:
            # Algorithm explanation for current day
            if current_day.day_index < 7:
                st.info(
                    f"""
                **🌱 Bootstrap Period (Day {current_day.day_index + 1}/7)**
                
                **Formula:** `Daily Limit = Min Growth ÷ 7`
                
                **Calculation:**
                ```
                Daily Limit = ${min_dollars:.2f} ÷ 7 = ${current_day.daily_spend_limit:.2f}
                ```
                
                **Purpose:** Build initial spending history before PRFAQ algorithm activates
                """
                )
            else:
                recent_7 = (
                    sum(st.session_state.accepted_history[-7:])
                    if len(st.session_state.accepted_history) >= 7
                    else 0
                )
                recent_6 = (
                    sum(st.session_state.accepted_history[-7:-1])
                    if len(st.session_state.accepted_history) >= 7
                    else 0
                )
                growth_factor = (1 + growth_pct / 100) ** (1.0 / 7)
                exponential_growth = recent_7 * growth_factor - recent_6
                linear_growth = recent_7 + min_dollars / 7 - recent_6

                st.success(
                    f"""
                **🎯 PRFAQ Rolling 7-Day Window Algorithm (Day {current_day.day_index + 1})**
                
                **📊 Rolling Window (Days {max(1, current_day.day_index + 1 - 6)} to {current_day.day_index + 1}):**
                The algorithm looks at the last 7 days of spending, NOT calendar weeks!
                
                **Step-by-Step Calculation:**
                
                1. **Recent 7 days total:** ${recent_7:.2f}
                2. **Recent 6 days total:** ${recent_6:.2f}
                3. **Growth factor:** (1 + {growth_pct}%/100)^(1/7) = {growth_factor:.4f}
                
                **Two Growth Options:**
                - **Exponential:**  Recent 7 days total x Growth factor - Recent 6 days total
                                    = \\${recent_7:.2f} × {growth_factor:.4f} - \\${recent_6:.2f} = \\${exponential_growth:.2f}
                - **Linear:** Recent 7 days total + Min Growth / 7 - Recent 6 days total
                                    = \\${recent_7:.2f} + \\${min_dollars/7:.2f} - \\${recent_6:.2f} = \\${linear_growth:.2f}
                
                **Final Result:**
                ```
                Daily Limit = max({exponential_growth:.2f}, {linear_growth:.2f}, 0)
                            = ${current_day.daily_spend_limit:.2f}
                ```
                """
                )

        # Show spending history that influenced this calculation
        if current_day.day_index >= 7:
            with st.expander(
                f"📊 Rolling 7-Day Window: Days {max(1, current_day.day_index + 1 - 6)} to {current_day.day_index + 1}",
                expanded=False,
            ):
                st.markdown("**🔄 How the Rolling Window Works:**")

                # Visual representation of rolling window
                st.markdown(
                    f"""
                    **Current Window (Day {current_day.day_index + 1}):**
                    ```
                    Days: [{max(1, current_day.day_index + 1 - 6)} {max(1, current_day.day_index + 1 - 5)} {max(1, current_day.day_index + 1 - 4)} {max(1, current_day.day_index + 1 - 3)} {max(1, current_day.day_index + 1 - 2)} {max(1, current_day.day_index + 1 - 1)} {current_day.day_index + 1}]
                          [------------ 7-day window -----------]
                    ```
                    
                    **Tomorrow's Window (Day {current_day.day_index + 2}):**
                    ```
                    Days: [{max(1, current_day.day_index + 2 - 6)} {max(1, current_day.day_index + 2 - 5)} {max(1, current_day.day_index + 2 - 4)} {max(1, current_day.day_index + 2 - 3)} {max(1, current_day.day_index + 2 - 2)} {max(1, current_day.day_index + 2 - 1)} {current_day.day_index + 2}]
                          [------------ 7-day window -----------]
                    ```
                    
                    **📌 Key Point:** The window "slides" every day - it's NOT calendar weeks!
                    """
                )

                history_data = []
                for i in range(7):
                    day_num = current_day.day_index - 6 + i
                    if day_num >= 0 and day_num < len(
                        st.session_state.accepted_history
                    ):
                        spend = st.session_state.accepted_history[day_num]
                        is_newest = i == 6  # Last day in the window
                        history_data.append(
                            {
                                "Day": f"Day {day_num + 1}",
                                "Spending": f"${spend:.2f}",
                                "Used in": "Recent 7" if i < 7 else "",
                                "Role": (
                                    "🆕 Newest (Recent 7 - Recent 6)"
                                    if is_newest
                                    else "Recent 6"
                                ),
                            }
                        )

                if history_data:
                    st.table(history_data)
                    st.info(
                        """
                        **💡 Algorithm Insight:** 
                        `recent_7 * growth_factor - recent_6` effectively isolates the contribution of the newest day.
                        This ensures growth is controlled based on the most recent spending pattern, not old data!
                        """
                    )

        # Wallet mechanics explanation
        st.subheader("💼 Wallet Mechanics")

        # Calculate current day values for the summary
        daily_limit = current_day.daily_spend_limit
        sgm_spent = current_day.sgm_spend
        unused_today = daily_limit - sgm_spent if sgm_spent < daily_limit else 0

        # Quick summary box
        summary_col1, summary_col2, summary_col3 = st.columns(3)
        with summary_col1:
            st.metric(
                "Daily Allowance",
                f"${daily_limit:.2f}",
                help="Your spending allowance for today (gets added to wallet automatically)",
            )
        with summary_col2:
            st.metric(
                "Actually Spent",
                f"${sgm_spent:.2f}",
                help="How much you actually spent from SGM wallet today",
            )
        with summary_col3:
            if unused_today > 0:
                st.metric(
                    "Saved for Later",
                    f"${unused_today:.2f}",
                    delta=f"+{unused_today:.2f}",
                    help="Unused allowance that stays in your wallet",
                )
            else:
                overspend = sgm_spent - daily_limit
                st.metric(
                    "Used from Savings",
                    f"${overspend:.2f}",
                    delta=f"-{overspend:.2f}",
                    help="Amount taken from your saved wallet balance",
                )

        # Key insight about unused spend
        st.info(
            f"""
            **🔑 KEY INSIGHT: How "Unused Spend" Funds the Wallet**
            
            Every day, your **daily allowance (\\${daily_limit:.2f})** gets added to your wallet automatically.
            {"Today you saved " + f"\\${unused_today:.2f}" + " by spending less than your allowance!" if unused_today > 0 else "Today you used " + f"\\${sgm_spent - daily_limit:.2f}" + " from your saved balance."}
            
            **It's like a daily allowance that accumulates when you don't spend it all!**
            """
        )
        
        # Code-level clarification
        with st.expander("🔍 Code-Level: How This Actually Works", expanded=False):
            wallet_plus_limit = current_day.wallet_balance_start + daily_limit
            st.markdown(
                f"""
                **There's NO explicit "add unused spend to wallet" code!** Instead:
                
                **Step 1: Daily Allowance Added (Automatic)**
                ```python
                wallet_plus_limit = wallet_start + daily_limit
                # ${current_day.wallet_balance_start:.2f} + ${daily_limit:.2f} = ${wallet_plus_limit:.2f}
                ```
                
                **Step 2: Capacity Enforcement (Critical!)**
                ```python
                available_capacity = min(wallet_plus_limit, max_capacity)
                # min(${wallet_plus_limit:.2f}, ${current_day.wallet_max_capacity:.2f}) = ${min(wallet_plus_limit, current_day.wallet_max_capacity):.2f}
                ```
                {"⚠️ **Capacity cap applied!** " + f"${wallet_plus_limit - current_day.wallet_max_capacity:.2f} lost due to cap." if wallet_plus_limit > current_day.wallet_max_capacity else "✅ **Under capacity** - no funds lost to cap."}
                
                **Step 3: Actual Spending Subtracted**  
                ```python
                wallet_end = available_capacity - actual_spending
                # ${min(wallet_plus_limit, current_day.wallet_max_capacity):.2f} - ${sgm_spent:.2f} = ${current_day.wallet_balance_end:.2f}
                ```
                
                **Step 4: "Unused Spend" = What's Left Over**
                ```python
                unused_amount = daily_limit - actual_spending
                # ${daily_limit:.2f} - ${sgm_spent:.2f} = ${unused_today:.2f}
                ```
                
                **💡 The Key Point:**
                "Unused spend funding the wallet" happens through **the absence of spending**, not explicit addition.
                Your daily allowance gets added whether you use it or not. What you don't spend naturally remains!
                
                **Complete Mathematical Reality:**
                ```
                Final Wallet = min(Previous Wallet + Daily Allowance, Capacity) - Actual Spending
                            = min(${current_day.wallet_balance_start:.2f} + ${daily_limit:.2f}, ${current_day.wallet_max_capacity:.2f}) - ${sgm_spent:.2f}
                            = ${min(wallet_plus_limit, current_day.wallet_max_capacity):.2f} - ${sgm_spent:.2f}
                            = ${current_day.wallet_balance_end:.2f}
                ```
                
                **🔧 Actual Code (sgm_simulator.py lines 339 & 352):**
                ```python
                base_sgm_capacity = min(wallet_start + daily_limit, max_wallet_capacity)
                wallet_end = base_sgm_capacity - sgm_from_wallet
                ```
                """
            )

        wallet_col1, wallet_col2 = st.columns(2)

        with wallet_col1:
            st.markdown("**🔄 Daily Wallet Update:**")

            # Calculate key values with detailed explanations
            prev_wallet = current_day.wallet_balance_start
            daily_limit = current_day.daily_spend_limit
            max_capacity = current_day.wallet_max_capacity
            wallet_plus_limit = prev_wallet + daily_limit
            available_capacity = min(wallet_plus_limit, max_capacity)
            sgm_spent = current_day.sgm_spend
            final_wallet = current_day.wallet_balance_end

            # Calculate unused spend for clarity
            unused_spend = daily_limit - sgm_spent if sgm_spent < daily_limit else 0

            st.markdown(
                f"""
            **Step-by-Step Wallet Calculation:**
            
            **1. Starting Position:**
            ```
            Previous Wallet Balance = ${prev_wallet:.2f}
            Today's Daily Limit = ${daily_limit:.2f}
            Wallet Capacity = ${max_capacity:.2f}
            ```
            
            **2. Daily Allowance Addition (Automatic):**
            ```
            Available = Wallet + Daily Limit = ${prev_wallet:.2f} + ${daily_limit:.2f} = ${wallet_plus_limit:.2f}
            (Your daily spending allowance gets added automatically)
            ```
            
            **3. Capacity Enforcement:**
            ```
            Available (capped) = min(${wallet_plus_limit:.2f}, ${max_capacity:.2f}) = ${available_capacity:.2f}
            ```
            
            **4. Actual Spending:**
            ```
            SGM Spending = ${sgm_spent:.2f}
            Daily Limit  = ${daily_limit:.2f}
            {"✅ Unused Amount = " + f"${unused_spend:.2f}" if unused_spend > 0 else "❌ Over-spent by = " + f"${sgm_spent - daily_limit:.2f}"}
            ```
            
            **5. Final Wallet Balance:**
            ```
            Final Wallet = Available - SGM Spending
            Final Wallet = ${available_capacity:.2f} - ${sgm_spent:.2f} = ${final_wallet:.2f}
            ```
            
            **💡 Key Point:** {"You saved " + f"${unused_spend:.2f}" + " for future use!" if unused_spend > 0 else "You used " + f"${sgm_spent - daily_limit:.2f}" + " from your saved balance."}
            """
            )

            # Show where each number comes from
            with st.expander("🔍 Source of Each Number + Code References", expanded=False):
                st.markdown(
                    f"""
                **Where these numbers come from:**
                
                **Previous Wallet Balance (\\${prev_wallet:.2f}):**
                - Yesterday's ending wallet balance
                - Carried forward from previous day
                
                **Today's Daily Limit (\\${daily_limit:.2f}):**
                - Calculated by SGM algorithm (see Daily Limit Calculation section above)
                - Bootstrap period: Min Growth ÷ 7
                - PRFAQ period: Complex rolling-window formula
                
                **Wallet Capacity (\\${max_capacity:.2f}):**
                - Formula: Daily Limit × Multiplier
                - Current model: {wallet_model.replace('_', ' ').title()}
                - Multiplier: {2 if wallet_model == "daily_limit_2x" else 3}
                - Calculation: \\${daily_limit:.2f} × {2 if wallet_model == "daily_limit_2x" else 3} = \\${max_capacity:.2f}
                
                **SGM Spending (\\${sgm_spent:.2f}):**
                - Amount actually spent from SGM wallet today
                - After Reserved Volume was used first
                - Before Manual Allowance (if any)
                
                **Final Wallet (\\${final_wallet:.2f}):**
                - Remaining balance for tomorrow
                - Will be "Previous Wallet Balance" for next day
                
                **🔧 Code References (sgm_simulator.py):**
                - **Line 339:** `base_sgm_capacity = min(wallet_start + daily_limit, max_wallet_capacity)`
                - **Line 352:** `wallet_end = base_sgm_capacity - sgm_from_wallet`
                - **No explicit "add unused spend" code** - it's what remains after subtraction!
                """
                )

            # Show capacity impact
            if wallet_plus_limit > max_capacity:
                excess = wallet_plus_limit - max_capacity
                st.warning(
                    f"""
                **⚠️ Capacity Limit Applied:**
                - Without capacity limit, wallet would be ${wallet_plus_limit:.2f}
                - Capacity limit enforced: ${max_capacity:.2f}
                - **Lost due to cap:** ${excess:.2f} ("use it or lose it")
                """
                )
            else:
                st.info(
                    f"""
                **✅ Under Capacity:**
                - Wallet + Daily Limit = ${wallet_plus_limit:.2f}
                - Capacity allows: ${max_capacity:.2f}
                - **Room remaining:** ${max_capacity - wallet_plus_limit:.2f}
                """
                )

        with wallet_col2:
            st.markdown("**🧠 Conceptual Understanding:**")

            # Simple analogy explanation
            st.markdown(
                f"""
                **Think of the wallet like a daily allowance bank account:**
                
                🏦 **Every day you get an "allowance" of ${daily_limit:.2f}**
                - This gets deposited automatically
                - Whether you spend it or not
                
                💰 **What you don't spend stays in your account**
                - Today's unused: ${unused_spend:.2f}
                - This builds up your balance for busy days
                
                🧢 **But there's a maximum balance (capacity): ${max_capacity:.2f}**
                - Prevents unlimited accumulation
                - "Use it or lose it" beyond the cap
                
                📈 **Future spike protection:**
                - If tomorrow you need ${daily_limit + 10:.2f}, you can use:
                - Tomorrow's allowance: ${daily_limit:.2f}
                - Plus saved balance: ${final_wallet:.2f}
                - **Total available: ${daily_limit + final_wallet:.2f}**
                """
            )

            # Calculate wallet status metrics with detailed explanations
            current_balance = current_day.wallet_balance_end
            max_capacity = current_day.wallet_max_capacity
            capacity_pct = (current_balance / max_capacity) * 100
            available_space = max_capacity - current_balance

            st.markdown("**📊 Wallet Status:**")
            st.progress(capacity_pct / 100)

            # Show calculations with formulas
            st.markdown(
                f"""
            **📐 Status Calculations:**
            
            **Current Wallet Balance:** \\${current_balance:.2f}
            **Maximum Capacity:** \\${max_capacity:.2f}
            
            **Utilization Formula:**
            ```
            Utilization = (Current Balance ÷ Max Capacity) × 100%
            Utilization = (${current_balance:.2f} ÷ ${max_capacity:.2f}) × 100%
            Utilization = {capacity_pct:.1f}%
            ```
            
            **Available Space Formula:**
            ```
            Available Space = Max Capacity - Current Balance
            Available Space = ${max_capacity:.2f} - ${current_balance:.2f}
            Available Space = ${available_space:.2f}
            ```
            
            **Model:** {wallet_model.replace('_', ' ').title()}
            """
            )

            # Show what these numbers mean
            with st.expander("🔍 What These Numbers Mean", expanded=False):
                st.markdown(
                    f"""
                **Current Wallet Balance (${current_balance:.2f}):**
                - This is your remaining SGM wallet balance after today's spending
                - Calculated as: Available Capacity - SGM Spending
                - This balance carries forward to tomorrow
                
                **Maximum Capacity (${max_capacity:.2f}):**
                - This is the maximum your wallet can ever hold
                - Formula: Daily Limit × {2 if wallet_model == "daily_limit_2x" else 3}
                - Prevents unlimited accumulation of unused daily limits
                
                **Utilization ({capacity_pct:.1f}%):**
                - Shows how "full" your wallet currently is
                - 0% = empty wallet, 100% = completely full
                - Higher utilization = more spending power available
                
                **Available Space (${available_space:.2f}):**
                - How much more your wallet can hold before hitting the cap
                - **"Wallet Full" means Available Space = $0.00**
                - Tomorrow's daily limit will fill this space (up to the limit amount)
                - If space < tomorrow's daily limit, some daily limit will be lost
                - **When wallet is full:** New daily limits are completely wasted/lost
                
                **Practical Example:**
                - If tomorrow's daily limit is ${current_day.daily_spend_limit:.2f}
                - And available space is ${available_space:.2f}
                - Then tomorrow you'll {"get the full daily limit" if available_space >= current_day.daily_spend_limit else f"only get \\${available_space:.2f} (losing \\${current_day.daily_spend_limit - available_space:.2f})"}
                """
                )

            # Show capacity impact for next day
            next_day_limit = (
                current_day.daily_spend_limit
            )  # Assuming same limit tomorrow
            if available_space < next_day_limit:
                loss = next_day_limit - available_space
                wallet_full_msg = (
                    " (WALLET IS FULL - NO SPACE LEFT)" if available_space == 0 else ""
                )
                st.warning(
                    f"""
                **⚠️ Tomorrow's Impact:**
                - Next daily limit: ~${next_day_limit:.2f}
                - Available space: ${available_space:.2f}{wallet_full_msg}
                - **Will lose:** ${loss:.2f} due to capacity cap
                - **Explanation:** Your wallet can't hold more than ${current_day.wallet_max_capacity:.2f} total
                """
                )
            else:
                st.info(
                    f"""
                **✅ Tomorrow's Impact:**
                - Next daily limit: ~${next_day_limit:.2f}
                - Available space: ${available_space:.2f}
                - **Will fit:** Full daily limit can be added
                """
                )

            # Detailed capacity explanation
            multiplier = 2 if wallet_model == "daily_limit_2x" else 3
            st.markdown(
                f"""
            **🎯 Capacity Purpose:**
            
            **Formula:** `Capacity = Daily Limit × {multiplier}`
            
            **What it does:**
            - **Prevents Hoarding:** Stops unlimited accumulation of unused daily limits
            - **Enables Bursts:** Allows spending more than daily limit when needed
            - **Controls Growth:** Limits how much you can "save up" for future spending
            - **When Full:** Wallet balance = Max capacity, available space = $0.00, new daily limits are lost
            
            **Example:**
            - Daily Limit: ${current_day.daily_spend_limit:.2f}
            - Max Capacity: ${current_day.wallet_max_capacity:.2f}
            - You can save up to {multiplier} days worth of unused spending
            - Once full, excess daily limits are lost (use it or lose it)
            """
            )

        # Intervention alert with detailed explanation
        if current_day.intervention_type == "shutdown":
            st.error(
                """
            🚨 **SGM Shutdown Active** - Data collection paused
            
            **What this means:** 90%+ of SGM spending requests were rejected today.
            **Impact:** Data collection and certain features are disabled for the rest of the day.
            **Recovery:** SGM will automatically restore normal operation tomorrow.
            """
            )
        elif current_day.intervention_type == "throttle":
            st.warning(
                """
            ⚠️ **SGM Throttling Active** - Data collection reduced
            
            **What this means:** Some SGM spending requests were rejected (but less than 90%).
            **Impact:** Data collection continues but at a reduced rate.
            **Note:** This helps prevent reaching the shutdown threshold.
            """
            )
        elif current_day.rejected_spend > 0:
            # Show intervention explanation even when no intervention is active
            rejection_rate = (
                current_day.rejected_spend / current_day.requested_spend
            ) * 100
            st.info(
                f"""
            ℹ️ **No Intervention Active** - Normal operation
            
            **Current rejection rate:** {rejection_rate:.1f}%
            **Intervention thresholds:** 
            - **Throttle:** >0% rejections (reduced data collection)
            - **Shutdown:** ≥90% rejections (data collection paused)
            
            **Note:** These intervention types are implementation details, not explicitly defined in the original SGM requirements documents.
            """
            )

        # Add general intervention explanation
        with st.expander("ℹ️ Understanding SGM Interventions", expanded=False):
            st.markdown(
                """
            **SGM Intervention System Overview:**
            
            SGM interventions are protective measures that activate when spending limits are consistently exceeded.
            
            ### 🎯 **Intervention Types (Implementation-Defined)**
            
            **1. 🟡 Throttle (>0% rejection rate)**
            - Data collection continues but at reduced rate
            - Some spending requests are rejected
            - Helps prevent reaching shutdown threshold
            - Normal service continues with limitations
            
            **2. 🔴 Shutdown (≥90% rejection rate)**
            - Data collection paused for the rest of the day
            - 90% or more of spending requests were rejected
            - Sentry interface remains available
            - Automatically restores tomorrow
            
            ### 📋 **Important Notes:**
            
            **Source of Intervention Logic:**
            - These specific intervention types (throttle vs shutdown) are **implementation details**
            - The original SGM requirements documents mention general "intervention" concepts
            - The 90% threshold and throttle/shutdown distinction were defined during development
            
            **Manual Override:**
            - Manual allowances can bypass interventions
            - Emergency spending can be authorized anytime
            - Interventions reset automatically each day
            
            **Calculation:**
            ```
            rejection_rate = rejected_spend / requested_spend
            if rejection_rate >= 0.9:
                intervention = "shutdown" 
            elif rejection_rate > 0:
                intervention = "throttle"
            else:
                intervention = "none"
            ```
            """
            )

        st.divider()

        # Reserved volumes status - more prominent
        if reserved and reserved.monthly_volume > 0:
            st.subheader("📦 Reserved Volumes Status")

            # Main reserved volume display
            used_pct = (
                current_day.cumulative_reserved_used / reserved.monthly_volume
            ) * 100
            remaining_pct = 100 - used_pct

            # Large remaining amount display
            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown(
                    f"""
                ### 💰 **\\${current_day.reserved_remaining:.2f}** Remaining
                **Out of \\${reserved.monthly_volume:.2f} monthly allocation**
                """
                )

                # Progress bar
                st.progress(used_pct / 100)
                st.caption(
                    f"Used: \\${current_day.cumulative_reserved_used:.2f} ({used_pct:.1f}%) | Remaining: \\${current_day.reserved_remaining:.2f} ({remaining_pct:.1f}%)"
                )

            with col2:
                # Usage summary
                st.metric(
                    label="Days in Cycle",
                    value=f"{current_day.billing_day}/30",
                    help="Current day in the monthly billing cycle",
                )

                if current_day.billing_day > 1:
                    daily_burn_rate = (
                        current_day.cumulative_reserved_used / current_day.billing_day
                    )
                    projected_monthly_usage = daily_burn_rate * 30
                    st.metric(
                        label="Daily Burn Rate",
                        value=f"${daily_burn_rate:.2f}",
                        delta=(
                            f"Projected: ${projected_monthly_usage:.2f}"
                            if projected_monthly_usage > reserved.monthly_volume
                            else None
                        ),
                        help="Average daily reserved volume usage",
                    )

            # Detailed breakdown in smaller metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Monthly Allocation", f"${reserved.monthly_volume:.2f}")
            with col2:
                st.metric(
                    "Used This Month", f"${current_day.cumulative_reserved_used:.2f}"
                )
            with col3:
                st.metric(
                    "Today's Reserved Usage", f"${current_day.reserved_spend:.2f}"
                )

        # Day details
        st.subheader("📊 Day Details")

        col1, col2 = st.columns(2)

        with col1:
            st.write("**Spend Breakdown:**")
            st.write(f"• Requested: \\${current_day.requested_spend:.2f}")
            st.write(f"• Accepted: \\${current_day.accepted_spend:.2f}")
            if current_day.reserved_spend > 0:
                st.write(f"  ◦ From Reserved: \\${current_day.reserved_spend:.2f}")
            if current_day.sgm_spend > 0:
                st.write(f"  ◦ From SGM: ${current_day.sgm_spend:.2f}")

            # Show manual allowance usage if any
            manual_used = max(
                0,
                current_day.accepted_spend
                - current_day.reserved_spend
                - current_day.sgm_spend,
            )
            if manual_used > 0:
                st.write(f"  ◦ From Manual Allowance: \\${manual_used:.2f}")
                st.success(
                    f"🚨 **Manual Override Used:** \\${manual_used:.2f} emergency spending bypassed normal limits"
                )

            st.write(f"• Rejected: ${current_day.rejected_spend:.2f}")
            if current_day.rejected_spend > 0:
                rejection_rate = (
                    current_day.rejected_spend / current_day.requested_spend
                ) * 100
                st.write(f"• Rejection Rate: {rejection_rate:.1f}%")

            # Add diagnostic information for debugging SGM wallet issues
            if current_day.rejected_spend > 0 or (
                current_day.reserved_spend == 0
                and current_day.sgm_spend == 0
                and current_day.requested_spend > 0
            ):
                with st.expander("🔧 Debug Information", expanded=True):
                    remaining_after_reserved = (
                        current_day.requested_spend - current_day.reserved_spend
                    )
                    available_sgm = min(
                        current_day.wallet_balance_start
                        + current_day.daily_spend_limit,
                        current_day.wallet_max_capacity,
                    )

                    st.write("**SGM Wallet Debug:**")
                    st.write(
                        f"• Remaining after Reserved: \\${remaining_after_reserved:.2f}"
                    )
                    st.write(
                        f"• Wallet Start: \\${current_day.wallet_balance_start:.2f}"
                    )
                    st.write(f"• Daily Limit: \\${current_day.daily_spend_limit:.2f}")
                    st.write(
                        f"• Wallet Capacity: \\${current_day.wallet_max_capacity:.2f}"
                    )
                    st.write(f"• Available SGM: \\${available_sgm:.2f}")
                    st.write(f"• SGM Actually Used: \\${current_day.sgm_spend:.2f}")

                    if remaining_after_reserved > 0 and current_day.sgm_spend == 0:
                        st.error(
                            f"""
                        **🚨 Potential Issue Detected:**
                        - Need to spend: \\${remaining_after_reserved:.2f} from SGM
                        - SGM Available: \\${available_sgm:.2f}
                        - But SGM Used: \\${current_day.sgm_spend:.2f}
                        
                        This suggests a problem with SGM wallet logic.
                        """
                        )
                    elif available_sgm < remaining_after_reserved:
                        st.warning(
                            f"""
                        **ℹ️ SGM Capacity Insufficient:**
                        - Need: \\${remaining_after_reserved:.2f}
                        - Available: \\${available_sgm:.2f}
                        - Shortfall: \\${remaining_after_reserved - available_sgm:.2f}
                        """
                        )

            # Show spending priority explanation when there are multiple sources
            sources_used = sum(
                [
                    1 if current_day.reserved_spend > 0 else 0,
                    1 if current_day.sgm_spend > 0 else 0,
                    1 if manual_used > 0 else 0,
                ]
            )
            if sources_used > 1:
                st.caption(
                    "💡 Spending sources used in priority order: Reserved → SGM → Manual"
                )

        with col2:
            st.write("**Wallet & Limits:**")
            st.write(f"• Wallet Start: \\${current_day.wallet_balance_start:.2f}")
            st.write("  _(Balance at beginning of day, after cap applied)_")
            st.write(f"• Daily Limit: \\${current_day.daily_spend_limit:.2f}")
            st.write("  _(Today's SGM spending allowance)_")
            st.write(f"• Wallet End: \\${current_day.wallet_balance_end:.2f}")
            st.write("  _(Balance after today's spending)_")
            capacity = current_day.daily_spend_limit * 2
            if capacity > 0:
                utilization = (current_day.wallet_balance_end / capacity) * 100
                st.write(f"• Capacity: ${capacity:.2f} ({utilization:.1f}% used)")
                st.write("  _(Max wallet = 2× daily limit)_")

        st.divider()

        # Charts
        days_to_show = st.session_state.simulation_days[
            : st.session_state.current_day_index + 1
        ]

        # Spend chart with intervention backgrounds
        st.subheader("📈 Daily Spend Analysis")

        if PLOTLY_AVAILABLE:
            # Create Plotly chart with intervention backgrounds
            fig = go.Figure()

            # Add intervention background regions first (so they appear behind the lines)
            intervention_regions = []
            for i, day in enumerate(days_to_show):
                if day.intervention_type != "none":
                    intervention_regions.append(
                        {
                            "day": i,
                            "type": day.intervention_type,
                            "rejection_rate": (
                                (day.rejected_spend / day.requested_spend * 100)
                                if day.requested_spend > 0
                                else 0
                            ),
                        }
                    )

            # Group consecutive intervention days
            if intervention_regions:
                current_start = None
                current_type = None

                for region in intervention_regions:
                    if current_start is None:
                        current_start = region["day"]
                        current_type = region["type"]
                        current_end = region["day"]
                    elif (
                        region["type"] == current_type
                        and region["day"] == current_end + 1
                    ):
                        current_end = region["day"]
                    else:
                        # Add the completed region
                        color = (
                            "rgba(255, 0, 0, 0.2)"
                            if current_type == "shutdown"
                            else "rgba(255, 165, 0, 0.2)"
                        )
                        fig.add_vrect(
                            x0=current_start - 0.4,
                            x1=current_end + 0.4,
                            fillcolor=color,
                            line_width=0,
                            annotation_text=f"{current_type.title()}",
                            annotation_position="top left",
                            annotation_font_size=10,
                        )
                        # Start new region
                        current_start = region["day"]
                        current_type = region["type"]
                        current_end = region["day"]

                # Add the final region
                if current_start is not None:
                    color = (
                        "rgba(255, 0, 0, 0.2)"
                        if current_type == "shutdown"
                        else "rgba(255, 165, 0, 0.2)"
                    )
                    fig.add_vrect(
                        x0=current_start - 0.4,
                        x1=current_end + 0.4,
                        fillcolor=color,
                        line_width=0,
                        annotation_text=f"{current_type.title()}",
                        annotation_position="top left",
                        annotation_font_size=10,
                    )

            # Add the main data lines
            days_range = list(range(len(days_to_show)))

            fig.add_trace(
                go.Scatter(
                    x=days_range,
                    y=[d.requested_spend for d in days_to_show],
                    mode="lines+markers",
                    name="Requested",
                    line=dict(color="#1f77b4", width=2),
                    marker=dict(size=4),
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=days_range,
                    y=[d.accepted_spend for d in days_to_show],
                    mode="lines+markers",
                    name="Accepted",
                    line=dict(color="#2ca02c", width=2),
                    marker=dict(size=4),
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=days_range,
                    y=[d.daily_spend_limit for d in days_to_show],
                    mode="lines",
                    name="Daily Limit",
                    line=dict(color="#ff7f0e", width=2, dash="dash"),
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=days_range,
                    y=[d.manual_allowances_used for d in days_to_show],
                    mode="lines+markers",
                    name="Manual Used",
                    line=dict(color="#d62728", width=2),
                    marker=dict(size=4),
                )
            )

            fig.update_layout(
                title="Daily Spend Analysis",
                xaxis_title="Day",
                yaxis_title="Amount ($)",
                hovermode="x unified",
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
                ),
                margin=dict(t=80),
            )

            st.plotly_chart(fig, use_container_width=True)

            # Add intervention legend
            if intervention_regions:
                st.caption(
                    "🎨 **Chart Legend:** Orange background = Throttle intervention, Red background = Shutdown intervention"
                )
        else:
            # Fallback to basic Streamlit chart if Plotly not available
            spend_data = {
                "Requested": [d.requested_spend for d in days_to_show],
                "Accepted": [d.accepted_spend for d in days_to_show],
                "Daily Limit": [d.daily_spend_limit for d in days_to_show],
                "Manual Used": [d.manual_allowances_used for d in days_to_show],
            }
            st.line_chart(spend_data)
            st.warning(
                "📦 Install plotly for enhanced charts with intervention backgrounds: `pip install plotly`"
            )

        # SGM Wallet chart with intervention backgrounds
        st.subheader("💰 SGM Wallet Balance")

        if PLOTLY_AVAILABLE:
            # Create Plotly wallet chart with intervention backgrounds
            fig_wallet = go.Figure()

            # Add intervention background regions first
            if intervention_regions:
                current_start = None
                current_type = None

                for region in intervention_regions:
                    if current_start is None:
                        current_start = region["day"]
                        current_type = region["type"]
                        current_end = region["day"]
                    elif (
                        region["type"] == current_type
                        and region["day"] == current_end + 1
                    ):
                        current_end = region["day"]
                    else:
                        # Add the completed region
                        color = (
                            "rgba(255, 0, 0, 0.2)"
                            if current_type == "shutdown"
                            else "rgba(255, 165, 0, 0.2)"
                        )
                        fig_wallet.add_vrect(
                            x0=current_start - 0.4,
                            x1=current_end + 0.4,
                            fillcolor=color,
                            line_width=0,
                            annotation_text=f"{current_type.title()}",
                            annotation_position="top left",
                            annotation_font_size=10,
                        )
                        # Start new region
                        current_start = region["day"]
                        current_type = region["type"]
                        current_end = region["day"]

                # Add the final region
                if current_start is not None:
                    color = (
                        "rgba(255, 0, 0, 0.2)"
                        if current_type == "shutdown"
                        else "rgba(255, 165, 0, 0.2)"
                    )
                    fig_wallet.add_vrect(
                        x0=current_start - 0.4,
                        x1=current_end + 0.4,
                        fillcolor=color,
                        line_width=0,
                        annotation_text=f"{current_type.title()}",
                        annotation_position="top left",
                        annotation_font_size=10,
                    )

            # Add wallet data lines
            fig_wallet.add_trace(
                go.Scatter(
                    x=days_range,
                    y=[d.wallet_balance_end for d in days_to_show],
                    mode="lines+markers",
                    name="Wallet Balance",
                    line=dict(color="#2ca02c", width=3),
                    marker=dict(size=4),
                    fill="tonexty" if len(days_to_show) > 1 else None,
                    fillcolor="rgba(44, 160, 44, 0.1)",
                )
            )

            fig_wallet.add_trace(
                go.Scatter(
                    x=days_range,
                    y=[d.daily_spend_limit * 2 for d in days_to_show],
                    mode="lines",
                    name="Wallet Capacity",
                    line=dict(color="#ff7f0e", width=2, dash="dash"),
                )
            )

            fig_wallet.update_layout(
                title="SGM Wallet Balance",
                xaxis_title="Day",
                yaxis_title="Balance ($)",
                hovermode="x unified",
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
                ),
                margin=dict(t=80),
            )

            st.plotly_chart(fig_wallet, use_container_width=True)

            # Add intervention legend
            if intervention_regions:
                st.caption(
                    "🎨 **Chart Legend:** Orange background = Throttle intervention, Red background = Shutdown intervention"
                )
        else:
            # Fallback to basic Streamlit chart if Plotly not available
            wallet_data = {
                "Wallet Balance": [d.wallet_balance_end for d in days_to_show],
                "Wallet Capacity": [d.daily_spend_limit * 2 for d in days_to_show],
            }
            st.line_chart(wallet_data)
            st.warning(
                "📦 Install plotly for enhanced charts with intervention backgrounds: `pip install plotly`"
            )

        # Add explanation for wallet drops
        if len(days_to_show) >= 8:
            # Check if there's a significant wallet drop around day 7
            if (
                days_to_show[6].wallet_balance_end
                > days_to_show[7].wallet_balance_start * 1.5
            ):
                st.info(
                    """
                **💡 Why did the wallet drop after day 7?**
                
                The wallet dropped because the SGM algorithm switched from "bootstrap mode" to "normal mode":
                - Days 0-6: Bootstrap period with generous limits to reach weekly minimum
                - Day 7+: PRFAQ algorithm based on actual spending history
                - Wallet is capped at 2× daily limit, so when the daily limit drops, the wallet cap drops too
                
                This prevents gaming the system by accumulating excessive credit during the bootstrap period.
                """
                )

        # Reserved vs SGM chart
        if any(d.reserved_spend > 0 for d in days_to_show):
            st.subheader("📦 Reserved vs SGM Usage")
            usage_data = {
                "Reserved": [d.reserved_spend for d in days_to_show],
                "SGM": [d.sgm_spend for d in days_to_show],
            }
            st.area_chart(usage_data)

        # Rejection chart
        if any(d.rejected_spend > 0 for d in days_to_show):
            st.subheader("🚫 Rejected Spend")
            rejection_data = {"Rejected": [d.rejected_spend for d in days_to_show]}
            st.area_chart(rejection_data, color="#ff0000")

        # Summary stats
        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("📊 Simulation Summary")
            total_requested = sum(d.requested_spend for d in days_to_show)
            total_accepted = sum(d.accepted_spend for d in days_to_show)
            total_rejected = sum(d.rejected_spend for d in days_to_show)
            total_reserved = sum(d.reserved_spend for d in days_to_show)
            total_sgm = sum(d.sgm_spend for d in days_to_show)
            interventions = sum(
                1 for d in days_to_show if d.intervention_type != "none"
            )

            st.write(f"• Total Days: {len(days_to_show)}")
            st.write(f"• Total Requested: ${total_requested:.2f}")
            st.write(f"• Total Accepted: ${total_accepted:.2f}")
            if total_reserved > 0:
                st.write(f"  ◦ From Reserved: ${total_reserved:.2f}")
                st.write(f"  ◦ From SGM: ${total_sgm:.2f}")
            st.write(f"• Total Rejected: ${total_rejected:.2f}")
            if total_requested > 0:
                st.write(
                    f"• Acceptance Rate: {(total_accepted/total_requested)*100:.1f}%"
                )
            st.write(f"• Intervention Days: {interventions}")

            # Show first week performance
            if len(days_to_show) >= 7:
                st.write("\n**First Week Performance:**")
                week1_days = days_to_show[:7]
                week1_requested = sum(d.requested_spend for d in week1_days)
                week1_accepted = sum(d.accepted_spend for d in week1_days)
                week1_sgm = sum(d.sgm_spend for d in week1_days)
                st.write(f"• Week 1 Requested: ${week1_requested:.2f}")
                st.write(f"• Week 1 SGM Accepted: ${week1_sgm:.2f}")
                week1_accept_rate = (week1_accepted/week1_requested)*100 if week1_requested > 0 else 0
                st.write(
                    f"• Week 1 Accept Rate: {week1_accept_rate:.1f}%"
                )

        with col2:
            st.subheader("🎯 Current Configuration")
            st.write(f"• Rule: {rule.name}")
            st.write(f"• Growth: {rule.growth_percentage}%/week")
            st.write(f"• Min Growth: ${rule.min_growth_dollars}/week")
            if reserved:
                st.write(f"• Reserved: ${reserved.monthly_volume}/month")

        # Current billing cycle section
        if reserved and st.session_state.simulation_days:
            st.divider()
            st.subheader("🔄 Current Billing Cycle")

            cycle_data = get_current_billing_cycle_data()

            cycle_col1, cycle_col2 = st.columns(2)

            with cycle_col1:
                st.markdown("**📊 Cycle Summary**")
                if cycle_data["days_in_cycle"] > 0:
                    current_billing_day = st.session_state.billing_day
                    days_remaining = reserved.days_in_cycle - current_billing_day + 1

                    st.write(
                        f"• Current Billing Day: {current_billing_day}/{reserved.days_in_cycle}"
                    )
                    st.write(f"• Days in Current Cycle: {cycle_data['days_in_cycle']}")
                    st.write(f"• Days Remaining: {days_remaining}")
                    st.write(f"• Cycle Start: Day {cycle_data['cycle_start_day'] + 1}")

                    # Progress bar for billing cycle
                    cycle_progress = current_billing_day / reserved.days_in_cycle
                    st.progress(
                        cycle_progress, f"Cycle Progress: {cycle_progress*100:.1f}%"
                    )
                else:
                    st.write("• No billing cycle data yet")

            with cycle_col2:
                st.markdown("**💸 Accumulated Usage**")
                if cycle_data["days_in_cycle"] > 0:
                    st.metric("SGM Spend", f"${cycle_data['accumulated_sgm']:.2f}")
                    st.metric(
                        "Reserved Spend", f"${cycle_data['accumulated_reserved']:.2f}"
                    )
                    st.metric(
                        "Total Accepted", f"${cycle_data['accumulated_accepted']:.2f}"
                    )
                    st.metric(
                        "Total Rejected", f"${cycle_data['accumulated_rejected']:.2f}"
                    )
                else:
                    st.write("• No usage data yet")

            # Forecast section
            if (
                cycle_data["days_in_cycle"] > 0
                and cycle_data["forecast"]["has_forecast"]
            ):
                st.divider()
                forecast_col1, forecast_col2, forecast_col3 = st.columns(3)

                with forecast_col1:
                    st.markdown("**📊 Daily Averages**")
                    st.write(
                        f"• Avg SGM/day: ${cycle_data['forecast']['avg_daily_sgm']:.2f}"
                    )
                    st.write(
                        f"• Avg Reserved/day: ${cycle_data['forecast']['avg_daily_reserved']:.2f}"
                    )
                    st.write(
                        f"• Avg Total/day: ${cycle_data['forecast']['avg_daily_accepted']:.2f}"
                    )

                with forecast_col2:
                    st.markdown("**🔮 Remaining Forecast**")
                    st.write(f"• Days left: {cycle_data['forecast']['days_remaining']}")
                    st.write(
                        f"• Forecast SGM: ${cycle_data['forecast']['forecast_sgm']:.2f}"
                    )
                    st.write(
                        f"• Forecast Reserved: ${cycle_data['forecast']['forecast_reserved']:.2f}"
                    )

                with forecast_col3:
                    st.markdown("**🎯 Projected Totals**")
                    st.write(
                        f"• Total SGM: ${cycle_data['forecast']['projected_total_sgm']:.2f}"
                    )
                    st.write(
                        f"• Total Reserved: ${cycle_data['forecast']['projected_total_reserved']:.2f}"
                    )

                    # Highlight projected invoice
                    st.success(
                        f"💰 **Projected Invoice: ${cycle_data['forecast']['projected_invoice']:.2f}**"
                    )

                    # Compare with current incomplete invoice
                    current_incomplete = (
                        reserved.monthly_volume + cycle_data["accumulated_sgm"]
                    )
                    forecast_increase = (
                        cycle_data["forecast"]["projected_invoice"] - current_incomplete
                    )
                    if forecast_increase > 0:
                        st.info(f"📈 Expected increase: +${forecast_increase:.2f}")

            elif cycle_data["days_in_cycle"] > 0:
                # Show simple projection for first day
                st.info(
                    f"💡 **Current Invoice**: ${reserved.monthly_volume + cycle_data['accumulated_sgm']:.2f}"
                )
                st.caption("📊 Forecast available after 2+ days of data")

            # Line chart for current billing cycle
            if cycle_data["days_in_cycle"] >= 2 and PLOTLY_AVAILABLE:
                st.markdown("**📈 Current Cycle Trends**")

                # Prepare data for charts
                cycle_days = cycle_data["days"]
                billing_days = [day.billing_day for day in cycle_days]
                cumulative_sgm = []
                cumulative_reserved = []
                cumulative_accepted = []
                daily_sgm = [day.sgm_spend for day in cycle_days]
                daily_reserved = [day.reserved_spend for day in cycle_days]

                # Calculate cumulative values
                sgm_sum = 0
                reserved_sum = 0
                accepted_sum = 0

                for day in cycle_days:
                    sgm_sum += day.sgm_spend
                    reserved_sum += day.reserved_spend
                    accepted_sum += day.accepted_spend
                    cumulative_sgm.append(sgm_sum)
                    cumulative_reserved.append(reserved_sum)
                    cumulative_accepted.append(accepted_sum)

                chart_col1, chart_col2 = st.columns(2)

                with chart_col1:
                    # Cumulative spending chart with forecast
                    fig_cumulative = go.Figure()

                    # Historical data
                    fig_cumulative.add_trace(
                        go.Scatter(
                            x=billing_days,
                            y=cumulative_sgm,
                            mode="lines+markers",
                            name="Cumulative SGM",
                            line=dict(color="orange", width=3),
                        )
                    )
                    fig_cumulative.add_trace(
                        go.Scatter(
                            x=billing_days,
                            y=cumulative_reserved,
                            mode="lines+markers",
                            name="Cumulative Reserved",
                            line=dict(color="blue", width=2),
                        )
                    )
                    fig_cumulative.add_trace(
                        go.Scatter(
                            x=billing_days,
                            y=cumulative_accepted,
                            mode="lines+markers",
                            name="Cumulative Total",
                            line=dict(color="green", width=2, dash="dash"),
                        )
                    )

                    # Add forecast if available
                    if (
                        cycle_data["forecast"]["has_forecast"]
                        and cycle_data["forecast"]["days_remaining"] > 0
                    ):
                        # Create forecast points
                        last_billing_day = billing_days[-1]
                        forecast_days = list(
                            range(last_billing_day + 1, reserved.days_in_cycle + 1)
                        )

                        # Forecast cumulative values
                        last_sgm = cumulative_sgm[-1]
                        last_reserved = cumulative_reserved[-1]
                        last_accepted = cumulative_accepted[-1]

                        forecast_cumulative_sgm = []
                        forecast_cumulative_reserved = []
                        forecast_cumulative_accepted = []

                        for i, day in enumerate(forecast_days):
                            days_ahead = i + 1
                            forecast_cumulative_sgm.append(
                                last_sgm
                                + (cycle_data["forecast"]["avg_daily_sgm"] * days_ahead)
                            )
                            forecast_cumulative_reserved.append(
                                last_reserved
                                + (
                                    cycle_data["forecast"]["avg_daily_reserved"]
                                    * days_ahead
                                )
                            )
                            forecast_cumulative_accepted.append(
                                last_accepted
                                + (
                                    cycle_data["forecast"]["avg_daily_accepted"]
                                    * days_ahead
                                )
                            )

                        # Add forecast lines
                        fig_cumulative.add_trace(
                            go.Scatter(
                                x=[last_billing_day] + forecast_days,
                                y=[last_sgm] + forecast_cumulative_sgm,
                                mode="lines",
                                name="Forecast SGM",
                                line=dict(color="orange", width=2, dash="dot"),
                                opacity=0.7,
                            )
                        )
                        fig_cumulative.add_trace(
                            go.Scatter(
                                x=[last_billing_day] + forecast_days,
                                y=[last_reserved] + forecast_cumulative_reserved,
                                mode="lines",
                                name="Forecast Reserved",
                                line=dict(color="blue", width=2, dash="dot"),
                                opacity=0.7,
                            )
                        )
                        fig_cumulative.add_trace(
                            go.Scatter(
                                x=[last_billing_day] + forecast_days,
                                y=[last_accepted] + forecast_cumulative_accepted,
                                mode="lines",
                                name="Forecast Total",
                                line=dict(color="green", width=2, dash="dot"),
                                opacity=0.7,
                            )
                        )

                    fig_cumulative.update_layout(
                        title="Cumulative Spending - Current Cycle (with Forecast)",
                        xaxis_title="Billing Day",
                        yaxis_title="Amount ($)",
                        height=400,
                        showlegend=True,
                    )
                    st.plotly_chart(fig_cumulative, use_container_width=True)

                with chart_col2:
                    # Daily spending chart
                    fig_daily = go.Figure()
                    fig_daily.add_trace(
                        go.Bar(
                            x=billing_days,
                            y=daily_sgm,
                            name="Daily SGM",
                            marker_color="orange",
                            opacity=0.7,
                        )
                    )
                    fig_daily.add_trace(
                        go.Bar(
                            x=billing_days,
                            y=daily_reserved,
                            name="Daily Reserved",
                            marker_color="blue",
                            opacity=0.7,
                        )
                    )

                    fig_daily.update_layout(
                        title="Daily Spending - Current Cycle",
                        xaxis_title="Billing Day",
                        yaxis_title="Amount ($)",
                        height=400,
                        barmode="stack",
                        showlegend=True,
                    )
                    st.plotly_chart(fig_daily, use_container_width=True)

        # Invoice tracking and ARR section
        if st.session_state.invoices and PLOTLY_AVAILABLE:
            st.divider()
            st.subheader("💰 Invoice Tracking & ARR")

            invoice_col1, invoice_col2 = st.columns(2)

            with invoice_col1:
                st.markdown("**📋 Recent Invoices**")
                # Show last 5 invoices
                recent_invoices = st.session_state.invoices[-5:]
                for invoice in reversed(recent_invoices):
                    with st.expander(
                        f"Invoice #{invoice.billing_cycle} - ${invoice.total_amount:.2f}"
                    ):
                        st.write(f"• Billing Cycle: {invoice.billing_cycle}")
                        st.write(
                            f"• Period: Day {invoice.cycle_start_day+1} - Day {invoice.cycle_end_day+1}"
                        )
                        st.write(f"• Prepaid Reserved: ${invoice.prepaid_reserved:.2f}")
                        st.write(f"• Accumulated SGM: ${invoice.accumulated_sgm:.2f}")
                        st.write(f"• **Total Amount: ${invoice.total_amount:.2f}**")
                        st.write(f"• Generated on Day: {invoice.generated_on_day+1}")

            with invoice_col2:
                # Calculate ARR
                if len(st.session_state.invoices) > 0:
                    latest_monthly_revenue = st.session_state.invoices[
                        -1
                    ].monthly_revenue
                    arr = latest_monthly_revenue * 12
                    total_revenue = sum(
                        inv.total_amount for inv in st.session_state.invoices
                    )
                    avg_monthly_revenue = total_revenue / len(st.session_state.invoices)
                    avg_arr = avg_monthly_revenue * 12

                    st.markdown("**📈 Revenue Metrics**")
                    st.metric("Current ARR", f"${arr:,.2f}", f"{arr-avg_arr:+.2f}")
                    st.metric("Average ARR", f"${avg_arr:,.2f}")
                    st.metric("Total Revenue", f"${total_revenue:,.2f}")
                    st.metric("Invoice Count", len(st.session_state.invoices))

            # Line charts for invoice trends
            if len(st.session_state.invoices) >= 2:
                chart_col1, chart_col2 = st.columns(2)

                with chart_col1:
                    # Invoice amounts over time
                    invoice_data = {
                        "Billing_Cycle": [
                            inv.billing_cycle for inv in st.session_state.invoices
                        ],
                        "Prepaid_Reserved": [
                            inv.prepaid_reserved for inv in st.session_state.invoices
                        ],
                        "Accumulated_SGM": [
                            inv.accumulated_sgm for inv in st.session_state.invoices
                        ],
                        "Total_Amount": [
                            inv.total_amount for inv in st.session_state.invoices
                        ],
                    }

                    fig_invoices = go.Figure()
                    fig_invoices.add_trace(
                        go.Scatter(
                            x=invoice_data["Billing_Cycle"],
                            y=invoice_data["Prepaid_Reserved"],
                            mode="lines+markers",
                            name="Prepaid Reserved",
                            line=dict(color="blue"),
                        )
                    )
                    fig_invoices.add_trace(
                        go.Scatter(
                            x=invoice_data["Billing_Cycle"],
                            y=invoice_data["Accumulated_SGM"],
                            mode="lines+markers",
                            name="Accumulated SGM",
                            line=dict(color="orange"),
                        )
                    )
                    fig_invoices.add_trace(
                        go.Scatter(
                            x=invoice_data["Billing_Cycle"],
                            y=invoice_data["Total_Amount"],
                            mode="lines+markers",
                            name="Total Invoice",
                            line=dict(color="green", width=3),
                        )
                    )

                    fig_invoices.update_layout(
                        title="Invoice Amounts by Billing Cycle",
                        xaxis_title="Billing Cycle",
                        yaxis_title="Amount ($)",
                        height=400,
                        showlegend=True,
                    )
                    st.plotly_chart(fig_invoices, use_container_width=True)

                with chart_col2:
                    # ARR trend over time
                    arr_data = {
                        "Billing_Cycle": [
                            inv.billing_cycle for inv in st.session_state.invoices
                        ],
                        "ARR": [
                            inv.monthly_revenue * 12
                            for inv in st.session_state.invoices
                        ],
                    }

                    fig_arr = go.Figure()
                    fig_arr.add_trace(
                        go.Scatter(
                            x=arr_data["Billing_Cycle"],
                            y=arr_data["ARR"],
                            mode="lines+markers",
                            name="ARR",
                            line=dict(color="purple", width=3),
                            fill="tozeroy",
                            fillcolor="rgba(128,0,128,0.1)",
                        )
                    )

                    fig_arr.update_layout(
                        title="Annual Recurring Revenue (ARR) Trend",
                        xaxis_title="Billing Cycle",
                        yaxis_title="ARR ($)",
                        height=400,
                        showlegend=False,
                    )
                    st.plotly_chart(fig_arr, use_container_width=True)

        # Export
        if st.button("📊 Export Data"):
            csv_lines = [
                "Day,Requested,Accepted,Reserved,SGM,Rejected,Wallet,Limit,Intervention,ReservedRemaining,BillingDay"
            ]
            for d in days_to_show:
                csv_lines.append(
                    f"{d.day_index},{d.requested_spend},{d.accepted_spend},"
                    f"{d.reserved_spend},{d.sgm_spend},{d.rejected_spend},"
                    f"{d.wallet_balance_end},{d.daily_spend_limit},"
                    f"{d.intervention_type},{d.reserved_remaining},{d.billing_day}"
                )
            csv_data = "\n".join(csv_lines)
            st.download_button(
                "Download CSV", csv_data, "sgm_simulation.csv", "text/csv"
            )

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    if "--cli" in sys.argv:
        run_cli()
    # Streamlit will handle the UI case automatically
