#!/usr/bin/env python3
"""
Test script to check if the Streamlit UI properly handles manual allowances
This script simulates the UI interactions for testing manual allowance
"""

from sgm_simulator import ManualAllowance, SGMEngine, SGMRule


def test_streamlit_manual_allowance_logic():
    """Test the manual allowance logic as used in the Streamlit UI"""
    print("=== Testing Streamlit Manual Allowance Logic ===")

    # This simulates the logic used in the Streamlit simulate_next_day() function
    rule = SGMRule("Test Rule", 20.0, 20.0, enabled=True)

    # Simulate state as it would be in Streamlit session
    accepted_history = [10.0] * 10
    wallet_balance = 15.0
    manual_allowance_input = 50.0  # This comes from the Streamlit UI input

    print(f"User input manual allowance: ${manual_allowance_input:.2f}")

    # This is the exact logic from the simulate_next_day() function in sgm_simulator_v5.py
    day_index = 10
    manual_allowances = []
    if manual_allowance_input > 0:
        manual_allowances.append(
            ManualAllowance(
                amount=manual_allowance_input,
                created_day=day_index,
                expiration_days=None,
                reason="Single day manual allowance",
            )
        )

    print(f"Manual allowances created: {len(manual_allowances)} items")
    for i, allowance in enumerate(manual_allowances):
        print(
            f"  Allowance {i+1}: ${allowance.amount:.2f}, expires: {allowance.expiration_days}"
        )

    # Simulate the day
    result, _, _ = SGMEngine.simulate_day(
        day_index=day_index,
        billing_day=11,
        requested_spend=100.0,
        wallet_balance=wallet_balance,
        accepted_history=accepted_history,
        rule=rule,
        wallet_config=None,
        reserved_config=None,
        cumulative_reserved_used=0,
        manual_allowances=manual_allowances,
        last_recalc_day=0,
        baseline_spend=None,
    )

    print("\n=== SIMULATION RESULTS ===")
    print(f"Requested: ${result.requested_spend:.2f}")
    print(f"Accepted: ${result.accepted_spend:.2f}")
    print(f"Rejected: ${result.rejected_spend:.2f}")
    print(f"SGM Spend: ${result.sgm_spend:.2f}")
    print(f"Manual Allowances Used: ${result.manual_allowances_used:.2f}")
    print(f"Daily Limit: ${result.daily_spend_limit:.2f}")
    print(f"Wallet Start: ${result.wallet_balance_start:.2f}")
    print(f"Wallet End: ${result.wallet_balance_end:.2f}")

    # Check if manual allowance was actually used
    if result.manual_allowances_used > 0:
        print(
            f"\n✅ Manual allowance WORKING: ${result.manual_allowances_used:.2f} was used"
        )
    else:
        print(
            f"\n❌ Manual allowance NOT WORKING: No manual allowance was used despite setting ${manual_allowance_input:.2f}"
        )

    # Test without manual allowance for comparison
    print("\n=== COMPARISON WITHOUT MANUAL ALLOWANCE ===")
    result_without, _, _ = SGMEngine.simulate_day(
        day_index=day_index,
        billing_day=11,
        requested_spend=100.0,
        wallet_balance=wallet_balance,
        accepted_history=accepted_history,
        rule=rule,
        wallet_config=None,
        reserved_config=None,
        cumulative_reserved_used=0,
        manual_allowances=[],  # No manual allowances
        last_recalc_day=0,
        baseline_spend=None,
    )

    print(f"Without manual - Accepted: ${result_without.accepted_spend:.2f}")
    print(f"With manual - Accepted: ${result.accepted_spend:.2f}")
    improvement = result.accepted_spend - result_without.accepted_spend
    print(f"Improvement: ${improvement:.2f}")

    if improvement > 0:
        print("✅ Manual allowance is providing the expected benefit")
    else:
        print("❌ Manual allowance is not providing any benefit")

    # Verify manual allowance was used
    assert result.manual_allowances_used > 0


def test_ui_edge_cases():
    """Test edge cases that might occur in the UI"""
    print("\n=== Testing UI Edge Cases ===")

    # Test case 1: Zero manual allowance
    print("\n1. Testing zero manual allowance:")
    rule = SGMRule("Test Rule", 20.0, 20.0, enabled=True)
    manual_allowances = []
    if 0.0 > 0:  # This is the condition in the UI
        manual_allowances.append(
            ManualAllowance(
                amount=0.0, created_day=0, expiration_days=None, reason="Test"
            )
        )

    result, _, _ = SGMEngine.simulate_day(
        day_index=0,
        billing_day=1,
        requested_spend=50.0,
        wallet_balance=5.0,
        accepted_history=[],
        rule=rule,
        manual_allowances=manual_allowances,
    )
    print(f"Zero manual allowance used: ${result.manual_allowances_used:.2f}")

    # Test case 2: Very small manual allowance
    print("\n2. Testing very small manual allowance:")
    manual_allowances = [
        ManualAllowance(amount=0.01, created_day=0, expiration_days=None, reason="Tiny")
    ]

    result, _, _ = SGMEngine.simulate_day(
        day_index=0,
        billing_day=1,
        requested_spend=50.0,
        wallet_balance=5.0,
        accepted_history=[],
        rule=rule,
        manual_allowances=manual_allowances,
    )
    print(f"Small manual allowance used: ${result.manual_allowances_used:.2f}")

    # Test case 3: Manual allowance exceeds request
    print("\n3. Testing manual allowance exceeding request:")
    manual_allowances = [
        ManualAllowance(
            amount=100.0, created_day=0, expiration_days=None, reason="Large"
        )
    ]

    result, _, _ = SGMEngine.simulate_day(
        day_index=0,
        billing_day=1,
        requested_spend=10.0,  # Small request
        wallet_balance=5.0,
        accepted_history=[],
        rule=rule,
        manual_allowances=manual_allowances,
    )
    print(f"Large manual allowance - Requested: ${result.requested_spend:.2f}")
    print(f"Large manual allowance - Accepted: ${result.accepted_spend:.2f}")
    print(f"Large manual allowance - Used: ${result.manual_allowances_used:.2f}")


if __name__ == "__main__":
    # Test the main logic
    working = test_streamlit_manual_allowance_logic()

    # Test edge cases
    test_ui_edge_cases()

    print(f"\n=== SUMMARY ===")
    if working:
        print(
            "✅ Manual allowance functionality is working correctly in the Streamlit UI logic"
        )
        print("🔍 If users report issues, the problem might be:")
        print("   - User interface not updating correctly")
        print("   - Session state management issues")
        print("   - User workflow misunderstanding")
        print("   - Browser caching issues")
    else:
        print("❌ Manual allowance functionality has issues in the Streamlit UI logic")
        print("🔧 This requires immediate investigation and fixing")
