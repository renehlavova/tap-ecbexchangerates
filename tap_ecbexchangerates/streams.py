"""Stream type classes for tap-ecbexchangerates."""

import datetime
import logging
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any, ClassVar, Dict, Optional, Tuple

from singer_sdk import typing as th  # JSON Schema typing helpers
from singer_sdk.streams import Stream

from tap_ecbexchangerates.client import ECBClient, ExchangeRate, RateCalculator

logger = logging.getLogger(__name__)


class ExchangeRatesStream(Stream):
    """Define custom stream."""

    name = "exchange_rates"
    replication_key = None
    primary_keys: ClassVar[list[str]] = ["date", "base_currency", "target_currency"]

    schema = th.PropertiesList(
        th.Property("date", th.DateTimeType),
        th.Property(
            "base_currency",
            th.StringType,
            description="The curency 'from'",
        ),
        th.Property(
            "target_currency",
            th.StringType,
            description="The currency 'to",
        ),
        th.Property(
            "exchange_rate",
            th.NumberType,
            description="The exchange rate",
        ),
    ).to_dict()

    def get_records(
        self, context: Optional[Dict[str, Any]]
    ) -> Iterable[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
        state = self.get_context_state(context)
        previous_end_date = state.get("end_date")
        start_date: datetime.date
        end_date: datetime.date

        if previous_end_date:
            logger.info(f"Resuming download using {previous_end_date=}")
            start_date = datetime.datetime.strptime(
                previous_end_date, "%Y-%m-%d"
            ).date() - datetime.timedelta(days=7)
            logger.info(
                f"New start date: {start_date} to cover missing weekend and holiday data"
            )
        else:
            start_date = datetime.datetime.strptime(
                self.config.get("start_date", "2000-01-01"), "%Y-%m-%d"
            ).date()
        end_date_str = self.config.get("end_date")
        end_date = (
            datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if end_date_str
            else datetime.date.today()
        )
        client = ECBClient()
        rates: list[ExchangeRate] = []
        for currency in self.config["currencies"]:
            rates.extend(
                client.get_exchange_rates(
                    currency, start_date=start_date, end_date=end_date
                )
            )
        state["end_date"] = end_date.strftime("%Y-%m-%d")
        calculator = RateCalculator(rates)
        all_rates = calculator.calculate_rebased_rates(self.config["currencies"])
        yield from map(asdict, all_rates)
