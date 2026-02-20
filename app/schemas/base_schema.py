from typing import Generic, TypeVar, Optional
from pydantic import BaseModel

from uuid import uuid4

T = TypeVar("T")

class Meta(BaseModel):
    page: Optional[int] = None
    page_size: Optional[int] = None
    total: Optional[int] = None

class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T]
    meta: Optional[Meta] = None
    message: Optional[str] = None
    errors: Optional[list] = None
    trace_id: str