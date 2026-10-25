from safenet.contracts import ErrorCode


class ProcessingError(Exception):
    """Safe public error: never includes input, filenames, or native exception details."""

    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(code.value)
