"""ERP placeholders cannot fabricate facts or enable write operations."""

import pytest

from app.jiahui.business import UnconnectedERP


@pytest.mark.parametrize("kind", ["prices", "inventory", "orders"])
async def test_unconnected_query_does_not_return_demo_business_data(kind):
    result = await UnconnectedERP().query(kind, "00001", {kind})
    assert result["connected"] is False
    assert result["identifier"] == "00001"
    assert result["message"] == "尚未接入业务数据"
    assert result["permission_denied"] is False
    assert not {"price", "stock", "quantity", "amount", "order"} & result.keys()


@pytest.mark.parametrize("kind", ["prices", "inventory", "orders"])
async def test_unconnected_query_still_enforces_scope(kind):
    result = await UnconnectedERP().query(kind, "00001", {"products", "knowledge"})
    assert result["permission_denied"] is True
    assert result["message"] == "尚未接入业务数据"
    assert "无权" in result["detail"]
    assert "identifier" not in result


async def test_explicit_all_scope_can_query_read_only_boundary():
    result = await UnconnectedERP().query("inventory", "00001", {"*"})
    assert result["permission_denied"] is False
    assert result["connected"] is False


@pytest.mark.parametrize("operation", ["cancel_order", "refund", "deduct_stock", "update_price"])
async def test_write_operations_are_outside_interface(operation):
    with pytest.raises(ValueError, match="只读"):
        await UnconnectedERP().query(operation, "00001", {"*"})
