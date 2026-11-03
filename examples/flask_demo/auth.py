"""Authentication blueprint: register, login and logout."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from .models import User
from .schemas import LoginForm

bp = Blueprint("auth", __name__, url_prefix="/auth")


def hash_password(password: str) -> str:
    """Deterministic demo hash (never do this in production)."""
    return f"hashed::{password}"


@bp.route("/register", methods=["POST"])
def register():
    """Create a user account."""
    form = LoginForm.model_validate(request.get_json())
    existing = User.query.filter_by(email=form.email).first()
    if existing is not None:
        return jsonify({"error": "email taken"}), 409
    user = User(email=form.email, password_hash=hash_password(form.password))
    session.add(user)
    session.commit()
    return jsonify({"id": user.id, "email": user.email}), 201


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate a user and open a session."""
    if request.method == "GET":
        return jsonify({"form": "login"})
    form = LoginForm.model_validate(request.get_json())
    user = User.query.filter_by(email=form.email).first()
    if user is None or not user.check_password(form.password):
        return jsonify({"error": "invalid credentials"}), 401
    session["user_id"] = user.id
    return jsonify({"ok": True})


@bp.route("/logout", methods=["POST"])
def logout():
    """Drop the current session."""
    session.pop("user_id", None)
    return jsonify({"ok": True})
