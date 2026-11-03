"""Order service layer: all persistence goes through here."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import Order, User


class OrderService:
    """Create and read orders for a single user."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_orders(self, user_id: int) -> list[Order]:
        """All orders owned by ``user_id``, newest first."""
        return (
            self.session.query(Order)
            .filter(Order.user_id == user_id)
            .order_by(Order.id.desc())
            .all()
        )

    def create_order(self, user_id: int, items: list[str]) -> Order:
        """Persist a new pending order."""
        user = User.query.get(user_id)
        if user is None:
            raise ValueError(f"unknown user {user_id}")
        order = Order(user_id=user_id, total=float(len(items)) * 9.99)
        self.session.add(order)
        self.session.commit()
        order.save()
        return order

    def cancel_order(self, order_id: int) -> None:
        """Mark an order cancelled and flush the change."""
        order = self.session.query(Order).get(order_id)
        if order is None:
            raise LookupError(order_id)
        order.status = "cancelled"
        self.session.commit()
