from fastapi import HTTPException


def fail(code: str, status: int = 400, message: str | None = None, **details: object) -> None:
    raise HTTPException(status, {"code": code, "message": message or code.replace("_", " ").capitalize(), **details})
