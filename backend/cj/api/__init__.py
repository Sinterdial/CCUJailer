"""
RESTful API 接口层包。

仅负责路由分发、Pydantic 参数校验、权限依赖与响应组装，
严禁在此包内直接导入 ORM 模型或操作 SQLAlchemy Session。
"""
