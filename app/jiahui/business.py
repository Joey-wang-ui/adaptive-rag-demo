"""Read-only boundary for a future authorized 管家婆 ERP integration.

Implementations should query a vendor-supported API or read-only middleware for
the installed ERP version. Historical quotes are not current transaction prices.
No write operation belongs to this first-stage interface.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Literal, Protocol

BusinessKind = Literal["prices", "inventory", "orders"]
READ_ONLY_KINDS = frozenset({"prices", "inventory", "orders"})


class ReadOnlyERP(Protocol):
    """Query authorized business data; never mutate orders, stock or payments."""

    async def query(
        self, kind: BusinessKind, identifier: str, scopes: Collection[str]
    ) -> dict[str, Any]: ...


class UnconnectedERP:
    """An explicit unconnected state, not demo stock or fabricated prices."""

    async def query(
        self, kind: BusinessKind, identifier: str, scopes: Collection[str]
    ) -> dict[str, Any]:
        if kind not in READ_ONLY_KINDS:
            raise ValueError("第一版只提供价格、库存和订单的只读查询接口")
        if kind not in scopes and "*" not in scopes:
            return {
                "connected": False,
                "kind": kind,
                "message": "尚未接入业务数据",
                "permission_denied": True,
                "detail": "当前账号无权访问此类资料",
            }
        return {
            "connected": False,
            "kind": kind,
            "identifier": identifier,
            "message": "尚未接入业务数据",
            "permission_denied": False,
        }
