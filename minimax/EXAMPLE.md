# 验证样例：订单查询增加状态筛选

## 要改什么

原始 orders.before.py 接受 status 参数但忽略它。新增按 pending / paid / cancelled 精确筛选；None 不筛选；空字符串、未知值和大小写不符均抛 ValueError，即使输入订单为空。status 与 customer 组合时必须同时满足。

## 必须保持什么

customer 默认返回所有客户，指定时按完全相等筛选；按整数 id 升序；返回订单的浅拷贝，不修改输入；空输入正常返回空列表。ORD-OLD 的“未知状态返回空列表”已被 ORD-002 替代，不能作为有效规格。

## 必须出现的上下文来源

| 来源 | 必要内容 |
| --- | --- |
| sample/spec.json#ORD-001 | 现有客户筛选、排序、浅拷贝契约 |
| sample/spec.json#ORD-002 | 当前 status 筛选与错误输入契约 |
| sample/orders.py:list_orders | 修改的函数全文 |
| sample/test_orders.py:Target | test_status / test_combined / test_invalid |
| sample/test_orders.py:Regression | test_default_sorted / test_customer / test_no_mutation / test_empty |
| PowerContext Atomic Memory | 已完成、待办、验证及对应资料指纹 |

## 数据与预期

测试固定 4 条订单：id=3 alice pending、id=1 alice paid、id=2 bob paid、id=4 bob cancelled。默认返回 [1,2,3,4]；status=paid 返回 [1,2]；customer=alice 且 status=paid 返回 [1]；alice + cancelled 返回 []；status=PAID、unknown、空字符串抛 ValueError。

同一 test_orders.py 在 before 快照上：4 个回归通过，3 个目标方法失败；当前代码：7 个方法全通过。子测试包含所有允许状态和空输入的非法状态。具体命令、退出码和代码 SHA256 见 evidence/*tests.json。

## 操作

按 README 初始化，执行 python acceptance.py。脚本在独立临时工程内导入资料、读取空任务、保存记录、退出进程、重启 context，并断言已有记录影响 next_action。工程副本、数据库、PowerContext 状态均由脚本创建，不依赖开发者本地数据。

规格刷新同样在临时副本内真实执行：revision 改为 2，ORD-002 增加 refunded 状态，再运行 import、context。输出必须包含 refunded，原验证 validation_current 必须变为 false。此演示没有声称业务代码已实现 v2；因此必须重新开发与测试。evidence/context-updated.json 保存结果。

手工演示可备份 sample/spec.json，修改 ORD-002.text 与 revision，再 import / context；恢复原文件后再次 import 即重置。未 import 直接读取被修改的来源会报 index stale，而不会静默使用旧资料。

MiniMax 生成按 README 先恢复 before 快照，然后调用 agent.py。完整证据应包含当次任务与上下文、真实模型 ID 与最终源码、同测试集前后对比、diff；缺少任一记录不能当作模型验收通过。