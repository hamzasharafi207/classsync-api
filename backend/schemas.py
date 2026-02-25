from pydantic import BaseModel, EmailStr
from datetime import datetime


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AssignmentCreate(BaseModel):
    course_name: str
    title: str
    due_date: datetime
    weight: float
    description: str


class AssignmentResponse(BaseModel):
    id: int
    course_name: str
    title: str
    due_date: datetime
    weight: float
    description: str
    priority_score: float

    class Config:
        from_attributes = True