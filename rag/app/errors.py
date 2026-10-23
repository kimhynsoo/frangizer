class ServiceError(Exception):
    """Only safe, prewritten messages cross the API boundary."""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


def upstream_error():
    return ServiceError(502, "UPSTREAM_ERROR", "검색 또는 모델 응답을 처리하지 못했습니다.", retryable=True)


def unavailable():
    return ServiceError(503, "SERVICE_UNAVAILABLE", "문서 검색 서비스가 아직 준비되지 않았습니다.", retryable=True)
