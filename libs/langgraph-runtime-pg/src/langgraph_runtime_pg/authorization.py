"""Application-owned Auth callbacks and database-enforced metadata filters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from langgraph_sdk import Auth
from sqlalchemy import and_, false, func, or_, true, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from starlette.exceptions import HTTPException


class AuthUser(dict):
    """Expose custom user fields through both SDK attribute and mapping access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @property
    def is_authenticated(self) -> bool:
        return self.get("is_authenticated", True)

    @property
    def display_name(self) -> str:
        return self.get("display_name", self["identity"])


async def authorize(
    auth: Auth | None,
    user: Mapping[str, Any] | None,
    resource: str,
    action: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Call the most specific handler, preserving mutations to its value."""
    if auth is None or user is None:
        return {}
    if not isinstance(auth, Auth):
        raise HTTPException(500, "Configured authorization must be an SDK Auth instance")
    handler = None
    # The SDK exposes registration but no public dispatch operation.
    for key in ((resource, action), (resource, "*"), ("*", action), ("*", "*")):
        handlers = auth._handlers.get(key)
        if handlers:
            handler = handlers[-1]
            break
    if handler is None and auth._global_handlers:
        handler = auth._global_handlers[-1]
    if handler is None:
        return {}
    ctx = Auth.types.AuthContext(
        user=cast(Any, AuthUser(user)),
        permissions=user.get("permissions", []),
        resource=cast(Any, resource),
        action=cast(Any, action),
    )
    try:
        result = await handler(ctx=ctx, value=value)
    except Auth.exceptions.HTTPException as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=exc.headers) from exc
    except AssertionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if result is None or result is True:
        return {}
    if result is False:
        raise HTTPException(403, "Forbidden")
    if not isinstance(result, dict):
        raise HTTPException(500, "Auth handler must return a filter dict, None, or boolean")
    return result


def metadata_predicate(column: Any, filters: dict[str, Any], depth: int = 0) -> Any:
    """Compile authorization filters before pagination, counts and mutations."""
    if not isinstance(filters, dict) or depth > 2:
        raise HTTPException(500, "Invalid authorization filter")
    clauses = []
    for key, value in filters.items():
        if key in ("$and", "$or"):
            if not isinstance(value, list):
                raise HTTPException(500, "Invalid authorization filter group")
            nested = [metadata_predicate(column, item, depth + 1) for item in value]
            clauses.append(and_(true(), *nested) if key == "$and" else or_(false(), *nested))
        elif not isinstance(key, str) or key.startswith("$"):
            raise HTTPException(500, "Unsupported authorization filter")
        elif isinstance(value, dict):
            if len(value) != 1:
                raise HTTPException(500, "Invalid authorization filter operator")
            operator, operand = next(iter(value.items()))
            if operator == "$eq":
                clauses.append(column[key] == type_coerce(operand, JSONB))
            elif operator == "$contains":
                members = operand if isinstance(operand, list) else [operand]
                clauses.append(
                    and_(
                        column[key].is_not(None),
                        # JSONB containment alone also accepts a scalar; require an array.
                        func.jsonb_typeof(column[key]) == "array",
                        column[key].contains(members),
                    )
                )
            else:
                raise HTTPException(500, "Unsupported authorization filter operator")
        else:
            clauses.append(column[key] == type_coerce(value, JSONB))
    return and_(true(), *clauses)
