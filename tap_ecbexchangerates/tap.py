"""ECB tap class."""

from __future__ import annotations

from typing import Any

from singer_sdk import Tap
from singer_sdk import typing as th  # JSON schema typing helpers

from tap_ecbexchangerates import streams


class TapECBEchangeRates(Tap):
    """ECB exchange rates tap class."""

    name = "tap-ecbexchangerates"

    config_jsonschema = th.PropertiesList(
        th.Property(
            "currencies",
            th.ArrayType(th.StringType),
            required=True,
            description="Array of currencies in ISO 4217 format (must be supported by ECB)",
        ),
        th.Property(
            "start_date",
            th.DateType,
            description="Date to start from (defaults to 2020-01-01)",
        ),
        th.Property(
            "end_date",
            th.DateType,
            description="End date (defaults to today)",
        ),
    ).to_dict()

    def discover_streams(self) -> list[Any]:
        """Return a list of discovered streams.

        Returns:
            A list of discovered streams.
        """
        return [streams.ExchangeRatesStream(tap=self)]


if __name__ == "__main__":
    TapECBEchangeRates.cli()
