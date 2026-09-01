"""Historical backtest of the live screener methodology.

PURPOSE — this is a *plumbing and behavior* test, not a performance promise:
  * exercise the data pipeline against thousands of historical edge cases,
  * observe how the composite behaves across market regimes,
  * quantify (not eliminate) the survivorship bias free data imposes.

HARD RULES:
  * Backtest results NEVER set the live factor weights.  The live record is
    the experiment; tuning to the past would manufacture overfitting.
  * Fundamentals are point-in-time: a simulated date only sees facts whose
    EDGAR `filed` date precedes it.
  * The known biases are stated in the generated report, with numbers.
"""
