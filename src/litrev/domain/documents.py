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
