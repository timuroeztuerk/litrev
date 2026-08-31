from enum import StrEnum


class ConversionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    OVERSIZED = "oversized"
    NEEDS_OCR = "needs_ocr"
    ENCRYPTED = "encrypted"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    RESOURCE_LIMIT = "resource_limit"
    MISSING_PART = "missing_part"


REMOVABLE_CONVERSION_STATUSES: frozenset[ConversionStatus] = frozenset(
    {
        ConversionStatus.EMPTY,
        ConversionStatus.OVERSIZED,
        ConversionStatus.NEEDS_OCR,
        ConversionStatus.ENCRYPTED,
        ConversionStatus.UNSUPPORTED,
        ConversionStatus.MALFORMED,
        ConversionStatus.RESOURCE_LIMIT,
        ConversionStatus.MISSING_PART,
    }
)
