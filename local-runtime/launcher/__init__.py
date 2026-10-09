"""界野电商平台 - 本地环境预检启动器。

独立于产品主程序（MainPG.exe），用户先运行本启动器做三层体检：
  1. 依赖/运行时     —— 由启动后产品自身 /engine/status 上报
  2. 配置对齐         —— 比对本机 workbench.sqlite3 与 golden 基准（limits/cos/updates）
  3. 连通性           —— 探测客户认证网关等关键可达性

本期 golden 来源：优先服务器下载，失败回退内置 default_golden.json。
"""
