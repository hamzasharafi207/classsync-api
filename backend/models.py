from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from backend.database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_verified = Column(Boolean, default=False)
    assignments = relationship("Assignment", back_populates="user", cascade="all, delete-orphan")


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    course_name = Column(String, index=True)
    title = Column(String, index=True)
    due_date = Column(DateTime)
    weight = Column(Float)
    description = Column(String)
    priority_score = Column(Float)

    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="assignments")