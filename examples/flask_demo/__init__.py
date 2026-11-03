"""A small, realistic Flask + SQLAlchemy app used as the moray-scribe demo.

It is deliberately shaped like a legacy codebase: module-level blueprints,
a hand-rolled service layer, ORM models declared with ``__tablename__`` and
``Column``, and pydantic request schemas (which must NOT be mistaken for ORM
models by the domain recognizers).
"""
