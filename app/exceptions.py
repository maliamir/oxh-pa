"""Domain exception types shared across auth, services, and error handlers."""

from pydantic import BaseModel


class ProblemFieldError(BaseModel):
    code: str
    field: str
    message: str


class DomainError(Exception):
    def __init__(
        self,
        *,
        status: int,
        title: str,
        detail: str,
        code: str,
        errors: list[ProblemFieldError] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.code = code
        self.errors = errors or []
        self.headers = headers or {}