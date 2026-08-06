import os

# 产品处理测试走本地透传，不调用真实 AI 中转（避免外部网络依赖与长耗时）。
os.environ["WH_PRODUCT_AI_ENABLED"] = "0"
