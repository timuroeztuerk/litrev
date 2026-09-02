from dataclasses import dataclass


class IsbnValidationError(ValueError):
    pass


class EmptyIsbnError(IsbnValidationError):
    pass


class MalformedIsbnError(IsbnValidationError):
    pass


class UnsupportedIsbnPrefixError(IsbnValidationError):
    pass


class IsbnChecksumError(IsbnValidationError):
    pass


@dataclass(frozen=True, slots=True)
class IsbnIdentity:
    display_value: str
    normalized_isbn: str
    canonical_isbn13: str


def isbn_identity(value: str) -> IsbnIdentity:
    stripped = value.strip()
    if not stripped:
        raise EmptyIsbnError("Enter an ISBN-10 or ISBN-13.")

    allowed_characters = set("0123456789Xx- ")
    if any(character not in allowed_characters for character in stripped):
        raise _malformed_isbn_error()

    normalized = stripped.replace("-", "").replace(" ", "").upper()
    if len(normalized) == 10:
        if not normalized[:9].isdigit() or not (normalized[-1].isdigit() or normalized[-1] == "X"):
            raise _malformed_isbn_error()
        if not _has_valid_isbn10_checksum(normalized):
            raise IsbnChecksumError("This ISBN has an invalid check digit.")
        canonical_isbn13 = _isbn13_from_isbn10(normalized)
    elif len(normalized) == 13:
        if not normalized.isdigit():
            raise _malformed_isbn_error()
        if not normalized.startswith(("978", "979")):
            raise UnsupportedIsbnPrefixError("ISBN-13 numbers must start with 978 or 979.")
        if int(normalized[-1]) != _isbn13_check_digit(normalized[:12]):
            raise IsbnChecksumError("This ISBN has an invalid check digit.")
        canonical_isbn13 = normalized
    else:
        raise _malformed_isbn_error()

    return IsbnIdentity(
        display_value=value,
        normalized_isbn=normalized,
        canonical_isbn13=canonical_isbn13,
    )


def _malformed_isbn_error() -> MalformedIsbnError:
    return MalformedIsbnError(
        "Enter 10 or 13 ISBN digits separated only by spaces or hyphens; ISBN-10 may end in X."
    )


def _has_valid_isbn10_checksum(isbn: str) -> bool:
    digits = [int(character) for character in isbn[:9]]
    check_digit = 10 if isbn[-1] == "X" else int(isbn[-1])
    weighted_sum = sum((10 - index) * digit for index, digit in enumerate(digits))
    return (weighted_sum + check_digit) % 11 == 0


def _isbn13_from_isbn10(isbn: str) -> str:
    body = f"978{isbn[:9]}"
    return f"{body}{_isbn13_check_digit(body)}"


def _isbn13_check_digit(body: str) -> int:
    weighted_sum = sum(
        int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(body)
    )
    return (10 - weighted_sum % 10) % 10
