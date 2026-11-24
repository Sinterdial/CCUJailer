"""
数据访问层（Repository）包。

本层仅接收调用方传入的 AsyncSession，只负责 SQLAlchemy ORM 查询、新增、删除与 flush，
严禁在 Repository 内部创建 Session 或自行调用 commit()/rollback()。
"""
