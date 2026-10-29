"""The pay line on a job card (SC-71): "$150,000 - $188,000", "$45 - $60 / hour", "£55,000 - £65,000". A year is the
default and says nothing; any other period says so. No pay gives an empty string, so the card keeps a blank line."""

_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$"}
_PERIOD_WORDS = {"month": "month", "week": "week", "day": "day", "hour": "hour"}


def _amount(value, currency):
    symbol = _SYMBOLS.get(currency)
    number = float(value)
    text = f"{int(number):,}" if number == int(number) else f"{number:,.2f}"
    return f"{symbol}{text}" if symbol else f"{currency} {text}"


def pay_line(row):
    """The pay line for a row that has pay_min, pay_max, pay_currency and pay_period (any of them may be None)."""
    low, high = row.get("pay_min"), row.get("pay_max")
    currency, period = row.get("pay_currency"), row.get("pay_period")
    if (low is None and high is None) or not currency:
        return ""
    if low is not None and high is not None and float(low) != float(high):
        text = f"{_amount(low, currency)} - {_amount(high, currency)}"
    elif low is not None and high is not None:
        text = _amount(low, currency)
    elif low is not None:
        text = f"{_amount(low, currency)}+"
    else:
        text = f"Up to {_amount(high, currency)}"
    word = _PERIOD_WORDS.get(period)
    return f"{text} / {word}" if word else text
