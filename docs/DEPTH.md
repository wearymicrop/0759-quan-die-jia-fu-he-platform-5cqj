# 深度登记 · 券叠加复核台

- 规模：中型
- 使用者：门店收银复核员追查促销实付和退单偏差。
- 核心规则：券组合先按资格筛查，再以兼容图枚举，按原分摊冲回退货。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 小票采信 | 隔离冲突流水并核对原行 | `GET /api/samples/{sample}/receipts` | `app/modules/receipts/intake.py` | `tests/test_receipts.py` |
| M2 券规则版本 | 检查有效档期与版本重叠 | `GET /api/samples/{sample}/versions` | `app/modules/versions/effective.py` | `tests/test_versions.py` |
| M3 叠用资格 | 查门槛并核对许可方阵 | `GET /api/samples/{sample}/eligibility` | `app/modules/eligibility/qualification.py` | `tests/test_eligibility.py` |
| M4 优惠求解 | 比较合法券组合并分摊 | `GET /api/samples/{sample}/optimizer` | `app/modules/optimizer/solver.py` | `tests/test_optimizer.py` |
| M5 退单冲回 | 沿原行分摊计算应退 | `GET /api/samples/{sample}/refunds` | `app/modules/refunds/replay.py` | `tests/test_refunds.py` |
| M6 金额对账 | 核对净额、实收及剩余 | `GET /api/samples/{sample}/reconcile` | `app/modules/reconcile/balance.py` | `tests/test_reconcile.py` |
| M7 追溯联单 | 串联原票、用券、退单与归档 | `GET /api/samples/{sample}/trace` | `app/modules/trace/register.py` | `tests/test_trace.py` |
| M8 预算裁决 | 同店当日店券争用预算并裁决正式批次 | `POST /api/budgets/batches` | `app/modules/budget/adjudicate.py` | `tests/test_budget.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | 首行 BOM | 首行漏采 | 去除文件头并保留原行号 | `app/modules/receipts/intake.py` | `tests/test_receipts.py::test_bom_and_leading_zero` |
| TRAP-06 | 全角及千分位金额 | 单价不可解析 | 按精确十进制转换为分 | `app/modules/receipts/money.py` | `tests/test_receipts.py::test_cents_normalized` |
| TRAP-07 | 实付为未知 | 误采成零元 | 隔离异常源行并保留原值 | `app/modules/receipts/money.py` | `tests/test_receipts.py::test_ambiguous_sentinel_isolated` |
| TRAP-08 | 小票号带前导零 | 原单键对不上 | 字符串保形 | `app/modules/receipts/contract.py` | `tests/test_receipts.py::test_bom_and_leading_zero` |
| TRAP-10 | 两位金额与过精输入 | 隐式进位或丢分 | 强校验后用十进制转整数 | `app/modules/receipts/money.py` | `tests/test_receipts.py::test_no_float_or_overprecision` |
| TRAP-11 | 两时区写法指向同一瞬间 | 误报同键冲突 | 规范 UTC 后再比较 | `app/modules/receipts/contract.py` | `tests/test_receipts.py::test_duplicate_normalized_clock` |
| TRAP-13 | 退货采集乱序 | 先退后到的件被多扣尾差 | 按时间和退单号审理原分摊 | `app/modules/refunds/replay.py` | `tests/test_refunds.py::test_out_of_order_uses_timestamp` |
| TRAP-14 | 同小票两条实付冲突 | 后者覆盖前者 | 隔离冲突组 | `app/modules/receipts/intake.py` | `tests/test_receipts.py::test_conflicting_sale_not_last_write` |
| TRAP-16 | 半条流水 | 让整个批次失败 | 保留截断证据与原因码 | `app/modules/receipts/intake.py` | `tests/test_receipts.py::test_truncation_not_counted` |
| TRAP-18 | 空记录与无记录 | 混成一条采信 | 分别统计物理行和坏记录 | `app/modules/receipts/intake.py` | `tests/test_receipts.py::test_empty_and_malformed_are_distinct` |
| TRAP-19 | 同组券既有兼容边又互斥 | 误用冲突券组 | 组互斥优先 | `app/modules/eligibility/compatibility.py` | `tests/test_eligibility.py::test_same_group_overrides_edge` |
| TRAP-23 | 规则首尾相邻 | 切换时命中两版 | 版本有效期半开 | `app/modules/versions/effective.py` | `tests/test_versions.py::test_boundary_half_open` |
| TRAP-24 | 平分一分钱 | 合计丢尾差 | 最大余数与明细键稳定裁决 | `app/modules/optimizer/allocation.py` | `tests/test_optimizer.py::test_rounding_tail_stable_id` |
| TRAP-25 | 两个券版本同期 | 随意选择一个版本 | 保留两个版本并隔离销售 | `app/modules/versions/effective.py` | `tests/test_versions.py::test_overlap_evidence_points_to_both_versions` |
| TRAP-26 | 晚到小票争用剩余额度 | 超发或一刀切停券 | 按到达顺序选能容纳的最优组合，容不下撤店券重算 | `app/modules/budget/adjudicate.py` | `tests/test_budget.py::test_first_come_keeps_discount_and_latecomer_fits` |
| TRAP-27 | 同一批次重复投递或并发重试 | 额度被重复占用 | 批次内容哈希主键加单事务，重投只读档案 | `app/modules/budget/ledger.py` | `tests/test_budget.py::test_parallel_posts_occupy_once` |
| TRAP-28 | 跨批次同店同单再投递 | 一单占两份额度 | `(门店, 单号)` 持有中占用唯一，后记 DUPLICATE_DELIVERY | `app/modules/budget/ledger.py` | `tests/test_budget.py::test_cross_batch_duplicate_not_double_occupied` |
| TRAP-29 | 提交中断后恢复 | 半写入的池账或丢记录 | `BEGIN IMMEDIATE` 单事务，失败整体回滚 | `app/modules/budget/ledger.py` | `tests/test_budget.py::test_interrupted_commit_leaves_no_trace` |
| TRAP-30 | 作废后重复作废 | 额度被重复释放 | VOIDED 幂等回执，释放额只减一次 | `app/modules/budget/ledger.py` | `tests/test_budget.py::test_void_releases_quota_and_is_idempotent` |

版本陷阱见 `sample_data/dirty/rules.json` 及 `sample_data/clean/rules.json`。流水用例见对应的 `events.jsonl`，精度超限与纯空输入由测试直接构造。

## 对账

- 商品毛额 − 实摊优惠 = 复算实付。记录实收与复算的逐笔差额。
- 复算实付 = 应退 + 未退净额。实退金额的差额按退单列示。
- 流水行 = 正常及业务隔离 + 来源隔离 + 重传 + 空行。

## 预算裁决

- 池余量 = 池额度 − 已占用；已占用 = 该池全部 HELD 占用之和，作废（VOIDED）即刻回补。
- 单票：最终组合减免合计 = 毛额 − 复算实付；出资券减免合计 = 实际占用 + 已释放。
- 三道闸门 `COMBO_BALANCE`、`POOL_BALANCE`、`PAID_MATCH` 全过批次方为 `formal`，否则保持 `pending`。作废与重复投递的小票不参与闸门。
