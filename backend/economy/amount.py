"""Exact credit amount conversion for the v2 economy ledger.

All authoritative economy values are signed 64-bit integers measured in
microcredits. This module is deliberately independent of Redis, PostgreSQL, and
API frameworks so every boundary uses the same conversion rules.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, localcontext
from typing import TypeAlias


MICROCREDITS_PER_CREDIT = 1_000_000
MIN_AMOUNT_MICROCREDITS = 1
MIN_INT64 = -9_223_372_036_854_775_808
MAX_INT64 = 9_223_372_036_854_775_807

CreditInput: TypeAlias = Decimal | int | str

_CREDIT_TEXT_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?\Z")
_MICROCREDIT_QUANTUM = Decimal("0.000001")
_MICROCREDITS_PER_CREDIT_DECIMAL = Decimal(MICROCREDITS_PER_CREDIT)
_MAX_CREDIT_TEXT_LENGTH = 20
_MAX_CREDITS_DECIMAL = Decimal(MAX_INT64) / _MICROCREDITS_PER_CREDIT_DECIMAL


class InvalidCreditAmount(ValueError):
    """Raised when a credit amount is inexact, unsupported, or out of range."""


def _parse_decimal(value: CreditInput) -> Decimal:
    """Parse a supported credit input without binary floating-point arithmetic."""
    if isinstance(value, bool):
        raise InvalidCreditAmount("boolean values are not valid credit amounts")

    if isinstance(value, int):
        return Decimal(value)

    if isinstance(value, Decimal):
        return value

    if isinstance(value, str):
        if len(value) > _MAX_CREDIT_TEXT_LENGTH:
            raise InvalidCreditAmount("credit text exceeds the signed 64-bit range")
        if not _CREDIT_TEXT_PATTERN.fullmatch(value):
            raise InvalidCreditAmount("credit text must use plain positive decimal syntax")
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise InvalidCreditAmount("credit text is not a valid decimal") from exc

    raise InvalidCreditAmount(f"unsupported credit amount type: {type(value).__name__}")


def parse_credit_amount(value: CreditInput) -> int:
    """Return an exact positive number of microcredits.

    Strings use strict plain-decimal syntax: no signs, exponent notation,
    separators, surrounding whitespace, or more than six fractional digits.
    ``Decimal`` and integer inputs must still be finite, positive, exact to a
    microcredit, and within the positive signed 64-bit range.
    """
    decimal_value = _parse_decimal(value)
    if not decimal_value.is_finite():
        raise InvalidCreditAmount("credit amount must be finite")
    if decimal_value <= 0:
        raise InvalidCreditAmount("credit amount must be greater than zero")
    exponent = decimal_value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -6:
        raise InvalidCreditAmount("credit amount cannot exceed six fractional digits")
    if decimal_value > _MAX_CREDITS_DECIMAL:
        raise InvalidCreditAmount("credit amount exceeds signed 64-bit microcredits")

    with localcontext() as context:
        context.prec = 32
        microcredits = decimal_value * _MICROCREDITS_PER_CREDIT_DECIMAL
    if microcredits != microcredits.to_integral_value():
        raise InvalidCreditAmount("credit amount cannot exceed six fractional digits")

    amount_microcredits = int(microcredits)
    if amount_microcredits < MIN_AMOUNT_MICROCREDITS:
        raise InvalidCreditAmount("credit amount is below one microcredit")
    if amount_microcredits > MAX_INT64:
        raise InvalidCreditAmount("credit amount exceeds signed 64-bit microcredits")
    return amount_microcredits


def format_credit_amount(amount_microcredits: int) -> Decimal:
    """Return a signed 64-bit microcredit value as a six-place credit Decimal."""
    if isinstance(amount_microcredits, bool) or not isinstance(amount_microcredits, int):
        raise InvalidCreditAmount("microcredits must be an integer")
    if not MIN_INT64 <= amount_microcredits <= MAX_INT64:
        raise InvalidCreditAmount("microcredits exceed the signed 64-bit range")

    with localcontext() as context:
        context.prec = 32
        return (Decimal(amount_microcredits) / _MICROCREDITS_PER_CREDIT_DECIMAL).quantize(
            _MICROCREDIT_QUANTUM
        )
