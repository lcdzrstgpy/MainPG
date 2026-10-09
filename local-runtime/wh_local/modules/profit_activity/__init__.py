"""利润活动模块：利润试算、归档和活动筛选。"""

from .api.router import create_profit_activity_router
from .service import ProfitActivityService, create_profit_activity_service

__all__ = [
    "ProfitActivityService",
    "create_profit_activity_router",
    "create_profit_activity_service",
]
