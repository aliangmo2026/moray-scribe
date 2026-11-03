"""ORM models for the demo shop."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    """A customer account."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    orders = relationship("Order", back_populates="user")

    def check_password(self, password: str) -> bool:
        """Compare a plaintext password against the stored hash."""
        return self.password_hash == f"hashed::{password}"


class Order(Base):
    """A placed order with one or more line items."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    total = Column(Float, default=0.0)
    status = Column(String(32), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="orders")

    def mark_paid(self) -> None:
        """Transition the order to the paid state."""
        self.status = "paid"

    def save(self) -> "Order":
        """Persist this order through the session."""
        return self
