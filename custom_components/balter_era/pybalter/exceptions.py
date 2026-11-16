"""Exceptions raised by pybalter."""


class BalterError(Exception):
    """Base class for all pybalter errors."""


class BalterConnectionError(BalterError):
    """The server could not be reached or closed the connection."""


class BalterAuthError(BalterError):
    """The server rejected the credentials or a session token."""


class BalterProtocolError(BalterError):
    """The server sent something we do not understand."""
