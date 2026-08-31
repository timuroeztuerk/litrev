from enum import StrEnum


class SourceType(StrEnum):
    PAPER = "paper"
    BOOK = "book"
    OTHER = "other"


class ReadingStatus(StrEnum):
    UNREAD = "unread"
    READING = "reading"
    READ = "read"
