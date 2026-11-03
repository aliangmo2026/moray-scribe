"""HTTP layer for the shop: thin views delegating to OrderService."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .models import Order
from .schemas import OrderForm
from .services import OrderService

bp = Blueprint("orders", __name__, url_prefix="/api")


@bp.route("/orders", methods=["GET"])
def list_orders():
    """List the current user's orders."""
    service = OrderService(request.db_session)
    return jsonify([order.id for order in service.list_orders(request.user_id)])


@bp.route("/orders", methods=["POST"])
def create_order():
    """Create an order from a JSON payload."""
    form = OrderForm.model_validate(request.get_json())
    service = OrderService(request.db_session)
    order = service.create_order(form.user_id, form.items)
    return jsonify({"id": order.id, "total": order.total}), 201


@bp.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id: int):
    """Fetch one order by id."""
    order = Order.query.get(order_id)
    if order is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": order.id, "status": order.status})


@bp.route("/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id: int):
    """Cancel an order."""
    OrderService(request.db_session).cancel_order(order_id)
    return jsonify({"ok": True})
