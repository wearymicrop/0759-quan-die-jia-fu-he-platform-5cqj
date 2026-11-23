# 数据字典

## 流水 `events.jsonl`

文件编码为 UTF-8，可带 BOM，每个物理行一个对象。行号从 1 开始。销售与退货的业务主键各为 `type + id`，编号保留前导零，同键规范化后完全相同为重传，内容冲突时隔离全部版本。时间输入需有显式偏移，UTC 排序，交易日按 UTC+08:00 划分。

| 类型 | 字段 | 规则 |
|---|---|---|
| `sale` | `id`、`shop`、`at` | 销售编号、门店、交易时点 |
| `sale` | `lines` | 键为 `id`，包含 `category`、正整数 `qty` 和单价 `price` |
| `sale` | `coupons`、`paid` | 候选券号列表和小票实付 |
| `return` | `id`、`at`、`order`、`line` | 退单编号、退货时点、原单号、原明细号 |
| `return` | `qty`、`cash` | 退货件数和实际退款 |

金额输入为十进制字符串，支持人民币符号、规范千分位与全角数码，最多两位小数。输出金额均为整数分。

## 规则集 `rules.json`

`versions` 含 `code`、`version`、`start`、`end`、`group`、`kind`、`value`。生效区间采用 `[start, end)`。`fixed` 的 `value` 是金额串，`percent` 的 `value` 是减免比例基点，1000 表示减 10%。`minimum` 默认为 0，空 `shops` 和 `categories` 对所有门店或品类生效。`rank` 越小越先用券，同位按券号。`pairs` 是明确允许的无序券对。同组互斥优先。券号和版本号联合唯一，购买时命中多个版本则隔离该单。

门槛使用合格明细的原价小计。百分比用当时合格余额并四舍五入到分，任何券减免最多为当时余额。券内按比例用最大余数法分摊，余数并列按明细号。组合按实付、券数、字典序比较。

## 冲回和归档

同一原单明细按退货 UTC、退单号排序。把原行减免先均分到件，余分给前几件，按累计已退件数获取本次减免。跨日时沿用同一原单明细。现金不一致保留双方金额及差额。`source_line` 是流水行号，`rule_index` 是规则列表序号，`original_line` 是原购流水行号。清单保存规则版本与规范化输入摘要，归档附件包含全部模块证据。

## 预算池 `budgets.json`

JSON 数组，每项一个门店当日池。`shop`、`day`（`YYYY-MM-DD`，UTC+08:00 交易日）、`amount`（金额串，规则同流水金额）、`groups`（出资券组，缺省 `["店券"]`）。同 `(shop, day)` 重复声明拒绝整批。池随首个引用它的批次建立，后续批次共用其余量。

## 预算批次

批次主键是 `events`、`rules`、`budgets` 规范化后的内容哈希，同内容重投返回原档案。`receipts` 逐单给出 `status`（`funded` 已出资 / `unfunded` 未获出资 / `free` 无需出资 / `duplicate` 重复投递 / `isolated` 已隔离）、最终 `combo`、`occupied` 实际占用、`due` 复算实付、`paid` 实收、`gap` 差额，未获优惠时 `reason` 为 `BUDGET_EXHAUSTED` 或 `POOL_MISSING`，`dropped` 列出被撤下的店券，`best_combo`/`best_due` 保留不计预算时的最优证据。作废后 `voided` 为真、`released` 记录释放额。`pools` 给出各池额度、已占用、剩余与本批持有；`gates` 是三道闸门及未过明细，`status` 为 `formal` 或 `pending`。
