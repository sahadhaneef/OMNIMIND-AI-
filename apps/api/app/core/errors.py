from fastapi import HTTPException


def fail(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail=code)
