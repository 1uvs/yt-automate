#!/usr/bin/env python3
"""Run the strategy brain now and print the resulting strategy.

Also runs automatically once a day inside autopilot.py. Needs OPENAI_API_KEY (with
billing) and a few published clips with analytics before it has anything to learn.

    python evolve.py
"""
import json

from clipfactory.strategy import evolve_strategy


if __name__ == "__main__":
    strat = evolve_strategy()
    if strat:
        print("\nUpdated strategy:\n")
        print(json.dumps(strat, indent=2))
    else:
        print("No update. Needs OpenAI billing active + published clips with "
              "analytics (give it a few posts and a day or two of data).")
