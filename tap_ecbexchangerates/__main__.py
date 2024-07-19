"""ECB entry point."""

from __future__ import annotations

from tap_ecbexchangerates.tap import TapECBEchangeRates

if __name__ == "__main__":
    TapECBEchangeRates.cli()
