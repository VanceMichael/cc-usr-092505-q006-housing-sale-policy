# 商品住房销售政策裁定

依据项目时态事实裁定商品住房预售、现售及资金监管动作。

## 领域资料

`contracts/domain.schema.json` 记录共享资料结构，`fixtures/domain.json` 是不含真实个人信息的示例。参与方包括住房管理受理人员、房地产开发企业、主办银行、项目复核人员。

当前资料依据以下业务事实维护：

- 新政策按土地公告和许可取得时间区分新项目与在途项目
- 新项目预售要求主体封顶并实施资金全过程监管
- 现房销售、主办银行和按揭放款均有独立前置条件

## 开发命令

- 运行测试：`python3 -m unittest discover -s tests -v`
- 编译或构建：`python3 -m compileall -q src/housing_sale_policy tests`

上述命令只读取仓库内文件，不连接外部业务服务。
