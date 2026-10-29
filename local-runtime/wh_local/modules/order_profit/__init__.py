"""订单利润模块：Temu 订单采集、列表/详情合并与利润聚合。"""

from __future__ import annotations

from .repository import OrderProfitRepository
from .routes import create_order_profit_router
from .service import OrderProfitService

__all__ = [
    "OrderProfitRepository",
    "OrderProfitService",
    "create_order_profit_router",
]
