def error_text(exc: BaseException) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
