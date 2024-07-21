"""REST client handling, including ECBStream base class."""

import dataclasses
import datetime
import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import Optional
from urllib.parse import urljoin

import backoff
import requests

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, eq=True)
class ExchangeRate:
    date: datetime.date
    base_currency: str
    target_currency: str
    exchange_rate: float

    def invert(self) -> "ExchangeRate":
        """Returns the rate inverted"""
        return ExchangeRate(
            date=self.date,
            base_currency=self.target_currency,
            target_currency=self.base_currency,
            exchange_rate=1 / self.exchange_rate,
        )

    def rebase(
        self, new_base_currency: str, lookup_rate: "ExchangeRate"
    ) -> "ExchangeRate":
        """Return the exchange rate with base currency changed"""
        if new_base_currency == self.base_currency:
            return self
        if new_base_currency != lookup_rate.target_currency:
            raise ValueError(
                f"Cannot convert to {new_base_currency} using {lookup_rate.target_currency}"
            )
        if self.base_currency != lookup_rate.base_currency:
            raise ValueError(
                f"Cannot convert rate based on {self.base_currency} using rate based on {lookup_rate.base_currency}"
            )
        return ExchangeRate(
            date=self.date,
            base_currency=new_base_currency,
            target_currency=self.target_currency,
            exchange_rate=1 / lookup_rate.exchange_rate * self.exchange_rate,
        )

    def __repr__(self) -> str:
        return f"<{self.base_currency} => {self.target_currency} @{self.date.strftime('%Y-%m-%d')} ({self.exchange_rate})>"


class ECBClient:
    def __init__(self) -> None:
        self.base_url = "https://data-api.ecb.europa.eu/service/data/EXR/"
        self.session = requests.Session()

    def _resource(self, target_currency: str, granularity: str = "D") -> str:
        # As explained in the docs, it is used to uniquely identify exchange rates.
        # - the frequency at which they are measured (e.g. daily - code D);
        # - the currency being measured (e.g. US dollar - code USD);
        # - the currency against which the above currency is being measured (e.g. euro - code EUR);
        # - the type of exchange rates (e.g. foreign exchange reference rates - code SP00);
        # - the Time series variation (e.g. average or standardised measure for a given frequency - code A).

        resource = f"{granularity}.{target_currency}.EUR.SP00.A"

        return resource

    def _fill_missing_dates(
        self, data: list[ExchangeRate], end_date: Optional[datetime.date] = None
    ) -> list[ExchangeRate]:
        """Fill missing conversions with the last known, typically during weekends and holidays"""

        end_date = end_date or datetime.date.today()

        all_dates = {entry.date: entry for entry in data}
        complete_data: list[ExchangeRate] = []
        current_date = min(all_dates)
        last_conversion = None

        while current_date <= end_date:
            if current_date in all_dates:
                last_conversion = all_dates[current_date]

            if last_conversion is None:
                logger.warning(
                    f"Missing exchange rates for {current_date} with no usable history, try using older start period"
                )
            else:
                complete_data.append(
                    ExchangeRate(
                        date=current_date,
                        base_currency=last_conversion.base_currency,
                        target_currency=last_conversion.target_currency,
                        exchange_rate=last_conversion.exchange_rate,
                    )
                )

            current_date += datetime.timedelta(days=1)

        return complete_data

    @backoff.on_exception(backoff.expo, requests.HTTPError, max_tries=5, max_time=60)
    def get_exchange_rates(
        self,
        target_currency: str,
        start_date: datetime.date,
        end_date: Optional[datetime.date] = None,
    ) -> list[ExchangeRate]:
        end_date = end_date or datetime.date.today()
        resource = self._resource(target_currency)
        url = urljoin(self.base_url, resource)

        response = self.session.get(
            url,
            params={
                "startPeriod": start_date.strftime("%Y-%m-%d"),
                "endPeriod": end_date.strftime("%Y-%m-%d"),
                "detail": "dataonly",
                "includeHistory": "false",
                "format": "jsondata",
            },
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            if response.status_code >= 500 or response.status_code == 429:
                raise
            logger.error(response.text)
            raise ValueError(f"Bad status code: {response.status_code}") from error
        content = response.json()
        dates = (
            date["id"]
            for date in content["structure"]["dimensions"]["observation"][0]["values"]
        )
        rates = (
            rate[0]
            for rate in content["dataSets"][0]["series"]["0:0:0:0:0"][
                "observations"
            ].values()
        )
        return self._fill_missing_dates(
            [
                ExchangeRate(
                    date=datetime.datetime.strptime(key, "%Y-%m-%d").date(),
                    base_currency="EUR",
                    target_currency=target_currency,
                    exchange_rate=value,
                )
                for key, value in dict(zip(dates, rates, strict=False)).items()
            ],
            end_date=end_date,
        )


class RateCalculator:
    def __init__(
        self,
        rates: list[ExchangeRate],
        base_currency: str = "EUR",
    ) -> None:
        self.rates = rates.copy()
        self.base_currency = base_currency
        for rate in self.rates:
            if rate.base_currency == self.base_currency:
                self.rates.append(rate.invert())

        self.by_date: dict[datetime.date, list[ExchangeRate]] = defaultdict(list)

        for rate in self.rates:
            self.by_date[rate.date].append(rate)

    def calculate_rebased_rates(self, currencies: Iterable[str]) -> list[ExchangeRate]:
        exchange_rates: list[ExchangeRate] = self.rates.copy()
        for date, rates in self.by_date.items():
            base_rates = {
                rate.target_currency: rate
                for rate in rates
                if self.base_currency == rate.base_currency
            }
            for new_base in currencies:
                if new_base == self.base_currency:
                    logger.debug(f"Skipping {new_base} => it is already a base")
                    continue
                logger.debug(f"Converting rates for {date} into {new_base}")
                for rate in rates:
                    if rate.base_currency != self.base_currency:
                        logger.debug(f"Skipping {rate} => already rebased as inversion")
                        continue
                    if rate.target_currency == new_base:
                        logger.debug(
                            f"Skipping {rate} => it is inverted and will be processed in a different batch"
                        )
                        continue
                    try:
                        lookup_rate = base_rates[new_base]
                    except KeyError:
                        logger.warning(
                            f"No lookup rate was found for {new_base} on {date}, cannot create conversion"
                        )
                        continue
                    logger.debug(f"Rebasing {rate} for {new_base} using {lookup_rate}")
                    exchange_rates.append(rate.rebase(new_base, lookup_rate))
        return exchange_rates
