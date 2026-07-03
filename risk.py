"""
risk.py
Risk calculator identical in behaviour to the Basilisk "RISK CALCULATOR" panel.
"""


def calculate(balance: float, risk_pct: float, payout_pct: float) -> dict:
    stake = round(balance * (risk_pct / 100), 2)
    potential_profit = round(stake * (payout_pct / 100), 2)
    potential_loss = stake
    # Break-even win rate: 1 / (1 + payout_fraction)
    payout_fraction = payout_pct / 100
    break_even = round((1 / (1 + payout_fraction)) * 100, 1)

    return {
        "stake": stake,
        "potential_profit": potential_profit,
        "potential_loss": potential_loss,
        "break_even_win_rate": break_even,
    }
