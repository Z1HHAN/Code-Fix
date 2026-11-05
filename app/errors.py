"""Errors that may be safely translated at the HTTP input boundary."""


class InputValidationError(ValueError):
    pass


class RunNotFound(KeyError):
    pass
