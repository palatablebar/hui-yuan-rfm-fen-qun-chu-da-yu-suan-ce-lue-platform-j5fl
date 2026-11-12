# Migrations

当前演示栈在启动时用 SQLAlchemy `Base.metadata.create_all()` 建表（见
`app/config/database.py`），保证 docker compose 一键起来即可用。

- `20261112_0001_initial.sql` 是等价的 PostgreSQL 16 初始 DDL，供正式环境
  用 Alembic/Flyway 管理迁移时参考；演示环境不自动执行该文件。
- `20261112_0002_order_corrections.sql` 把 `order_facts` 从「只存销售单」扩展为
  事件溯源事实表，新增 `order_type / original_order_no / correction_no / received_at`
  四列与幂等唯一索引。DDL 全部 `IF NOT EXISTS`，可在已存在的库上重复执行；
  历史销售行自动取默认值 `order_type='SALE'`，不改变既有分群口径。

## POS 补传更正的口径（与 `app/services/order_ledger.py` 对齐）

| 事件 | order_type | 金额 | 对 as-of 分群的影响 |
|---|---|---|---|
| 销售单 | `SALE` | 正 | `ordered_at <= as_of` 才计入 F/M/R |
| 迟到补传销售单 | `LATE_SALE` | 正 | 同上，业务发生在统计日之后则不采纳 |
| 退款单 | `REFUND` | 负 | 只冲减 M（不冲减 F），按 `original_order_no` 关联原单 |
| 撤销单 | `VOID` | 负 | 原单整笔作废，F 与 M 同时剔除 |

`correction_no` 是门店 POS 侧的更正幂等键（部分唯一索引）：同一笔更正重复投递
只落一条、只生效一次。更正单必须引用已存在的销售单，且不能跨会员冲减。
