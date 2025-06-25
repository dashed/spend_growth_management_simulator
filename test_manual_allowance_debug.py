#!/usr/bin/env python3
"""
Debug script to test manual allowance functionality specifically
"""

from sgm_simulator import ManualAllowance, SGMEngine, SGMRule


def test_manual_allowance_new_api():
    """Test manual allowance using the new ManualAllowance API"""
    rule = SGMRule(
        name="Test Rule", growth_percentage=20.0, min_growth_dollars=20.0, enabled=True
    )

    # Build some history first (day 7+ for PRFAQ algorithm)
    accepted_history = [10.0] * 10

    print("=== Testing Manual Allowance with New API ===")

    # Test without manual allowance
    print("\n1. Testing WITHOUT manual allowance:")
    result_without, _, _ = SGMEngine.simulate_day(
        day_index=10,
        billing_day=11,
        requested_spend=100.0,
        wallet_balance=15.0,
        accepted_history=accepted_history,
        rule=rule,
        manual_allowances=[],
    )

    print(f"  Requested: ${result_without.requested_spend:.2f}")
    print(f"  Accepted: ${result_without.accepted_spend:.2f}")
    print(f"  Rejected: ${result_without.rejected_spend:.2f}")
    print(f"  Daily Limit: ${result_without.daily_spend_limit:.2f}")
    print(f"  Wallet Start: ${result_without.wallet_balance_start:.2f}")
    print(f"  Wallet End: ${result_without.wallet_balance_end:.2f}")
    print(f"  Manual Used: ${result_without.manual_allowances_used:.2f}")

    # Test with manual allowance using new API
    print("\n2. Testing WITH manual allowance (new API):")
    manual_allowances = [
        ManualAllowance(
            amount=50.0,
            created_day=10,
            expiration_days=None,
            reason="Emergency testing budget",
        )
    ]

    result_with, _, _ = SGMEngine.simulate_day(
        day_index=10,
        billing_day=11,
        requested_spend=100.0,
        wallet_balance=15.0,
        accepted_history=accepted_history,
        rule=rule,
        manual_allowances=manual_allowances,
    )

    print(f"  Requested: ${result_with.requested_spend:.2f}")
    print(f"  Accepted: ${result_with.accepted_spend:.2f}")
    print(f"  Rejected: ${result_with.rejected_spend:.2f}")
    print(f"  Daily Limit: ${result_with.daily_spend_limit:.2f}")
    print(f"  Wallet Start: ${result_with.wallet_balance_start:.2f}")
    print(f"  Wallet End: ${result_with.wallet_balance_end:.2f}")
    print(f"  Manual Used: ${result_with.manual_allowances_used:.2f}")

    # Test with legacy API for comparison
    print("\n3. Testing WITH manual allowance (legacy API):")
    result_legacy, _, _ = SGMEngine.simulate_day(
        day_index=10,
        billing_day=11,
        requested_spend=100.0,
        wallet_balance=15.0,
        accepted_history=accepted_history,
        rule=rule,
        manual_allowance=50.0,  # Using legacy parameter
    )

    print(f"  Requested: ${result_legacy.requested_spend:.2f}")
    print(f"  Accepted: ${result_legacy.accepted_spend:.2f}")
    print(f"  Rejected: ${result_legacy.rejected_spend:.2f}")
    print(f"  Daily Limit: ${result_legacy.daily_spend_limit:.2f}")
    print(f"  Wallet Start: ${result_legacy.wallet_balance_start:.2f}")
    print(f"  Wallet End: ${result_legacy.wallet_balance_end:.2f}")
    print(f"  Manual Used: ${result_legacy.manual_allowances_used:.2f}")

    # Compare results
    print("\n=== ANALYSIS ===")
    print(f"Improvement with manual allowance:")
    print(
        f"  Additional accepted: ${result_with.accepted_spend - result_without.accepted_spend:.2f}"
    )
    print(
        f"  Reduction in rejected: ${result_without.rejected_spend - result_with.rejected_spend:.2f}"
    )
    print(f"  Manual allowance used: ${result_with.manual_allowances_used:.2f}")

    # Verify the results are consistent
    assert (
        result_with.accepted_spend >= result_without.accepted_spend
    ), "Manual allowance should increase accepted spend"
    assert (
        result_with.rejected_spend <= result_without.rejected_spend
    ), "Manual allowance should decrease rejected spend"
    assert result_with.manual_allowances_used > 0, "Manual allowance should be used"

    # Verify legacy and new API give same results
    assert (
        abs(result_with.accepted_spend - result_legacy.accepted_spend) < 0.01
    ), "Legacy and new API should give same results"
    assert (
        abs(result_with.manual_allowances_used - result_legacy.manual_allowances_used)
        < 0.01
    ), "Manual usage should be same"

    print("\n✅ Manual allowance functionality is working correctly!")


def test_manual_allowance_expiration():
    """Test manual allowance expiration functionality"""
    rule = SGMRule(
        name="Test Rule", growth_percentage=20.0, min_growth_dollars=20.0, enabled=True
    )

    accepted_history = [10.0] * 10

    print("\n=== Testing Manual Allowance Expiration ===")

    # Test with expired manual allowance
    print("\n1. Testing with EXPIRED manual allowance:")
    expired_allowances = [
        ManualAllowance(
            amount=50.0,
            created_day=5,
            expiration_days=3,  # Expires after 3 days
            reason="Expired emergency budget",
        )
    ]

    result_expired, _, _ = SGMEngine.simulate_day(
        day_index=10,  # Day 10, allowance created on day 5, expires on day 8
        billing_day=11,
        requested_spend=100.0,
        wallet_balance=15.0,
        accepted_history=accepted_history,
        rule=rule,
        manual_allowances=expired_allowances,
    )

    print(f"  Manual Used: ${result_expired.manual_allowances_used:.2f}")
    print(f"  Expired Allowances: ${result_expired.expired_allowances:.2f}")

    # Test with active manual allowance
    print("\n2. Testing with ACTIVE manual allowance:")
    active_allowances = [
        ManualAllowance(
            amount=50.0,
            created_day=9,
            expiration_days=5,  # Expires after 5 days
            reason="Active emergency budget",
        )
    ]

    result_active, _, _ = SGMEngine.simulate_day(
        day_index=10,  # Day 10, allowance created on day 9, expires on day 14
        billing_day=11,
        requested_spend=100.0,
        wallet_balance=15.0,
        accepted_history=accepted_history,
        rule=rule,
        manual_allowances=active_allowances,
    )

    print(f"  Manual Used: ${result_active.manual_allowances_used:.2f}")
    print(f"  Expired Allowances: ${result_active.expired_allowances:.2f}")

    print("\n=== EXPIRATION ANALYSIS ===")
    print(
        f"Expired allowance should not be usable: {result_expired.manual_allowances_used == 0}"
    )
    print(
        f"Active allowance should be usable: {result_active.manual_allowances_used > 0}"
    )

    assert (
        result_expired.manual_allowances_used == 0
    ), "Expired manual allowance should not be used"
    assert (
        result_expired.expired_allowances == 50.0
    ), "Expired allowance amount should be tracked"
    assert (
        result_active.manual_allowances_used > 0
    ), "Active manual allowance should be used"
    assert (
        result_active.expired_allowances == 0
    ), "No expired allowances when using active"

    print("\n✅ Manual allowance expiration is working correctly!")


if __name__ == "__main__":
    test_manual_allowance_new_api()
    test_manual_allowance_expiration()
    print("\n🎉 All manual allowance tests passed!")
