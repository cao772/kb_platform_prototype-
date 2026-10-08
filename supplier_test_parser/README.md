# 供应商标准本地解析

仅负责两份GB/T标准的工程解析，不改写认证规则结果。全流程使用本地Apple Vision、PDFKit和确定性文本提取，没有模型API调用，也不读取平台模型配置或密钥。

## 运行

macOS需安装Swift命令行工具，Python需PyMuPDF。新扫描件：

```sh
python3 -m supplier_test_parser.standard_parser.pipeline \
  --pdf /资料/标准1.pdf /资料/标准2.pdf \
  --output /交付/标准解析
```

可通过 `--ocr-cache /已有vision_pages --cache-manifest /源文件清单.json` 复用已完成的本地OCR。缓存必须包含原PDF文件名及SHA256，并与当前文件一致；仅名称相同不复用。

可选 `--corrections /原图核对修订.json` 只应用已明确核对的字符修订。每项包含source_sha256、page、before、after、basis，可设expected_matches；原文件哈希或匹配次数不符时不套用。原OCR文字保留在ocr_text中，不无痕改写。空白页可用blank_page和basis记录原图核验。

Excel生成使用Codex bundled Node和`@oai/artifact-tool`。将运行时node_modules链接到本目录或其祖先（不提交），然后：

```sh
node supplier_test_parser/standard_parser/excel_generator/build.mjs /交付/标准解析
python3 -m unittest discover -s supplier_test_parser/tests -v
```

两份Excel生成后，`python3 -m supplier_test_parser.package /交付/标准解析 --source-commit <commit>` 用只读openpyxl校验实际导出的工作表、冻结窗格、筛选、页码链接和200页文件覆盖，再生成SHA256交接清单及ZIP。只打包白名单文件，不夹带运行时、密钥、代码缓存或日志。

## 输出

- `01_标准全文解析结果.xlsx`：标准信息、章节目录、条款解析、测试要求、引用标准、图表索引、证据定位、解析统计。
- `standard_validation_report.xlsx`：检查汇总、问题明细、OCR页检查、逐页处理记录、Excel行数。
- `ocr/`：全部页面文字、识别块和坐标。
- `clause_tree/`：标准、章节、条款、附录节点及父子ID。
- `extract/standard_records.json`：完整条文、字段及证据。
- `原文对照.html`及`原页/`：离线逐页查看。
- `manifest.json`：来源SHA256、页数、节点数及处理方式。

字段采用原句摘录，不用常识补数值。试验时间/次数不等同实验室检测周期或认证周期。章节条款号发生跳跃只报告候选问题；特殊要求101起编号单独标记，不擅自补号。OCR成功率不作为准确率。

长字段在Excel中按“内容分段”拆成连续行，拼接可恢复原字段，不截掉尾部。JSON保留完整单条记录。编号、原页和坐标不依赖Excel显示行号。

## 边界

### 三文件客户包

客户精简版固定为4个Sheet：01仅“条款解析”（744条结构记录），02仅“认证实施规则字段”（33列×2条完整记录），03保留“证据索引”（2681条）与“抽样核验”（25条）。客户31个标准字段不改；目录、图表、引用、测试分段等独有记录归并到对应条款补充列，来源指纹与认证审核边界归并到证据索引。`compact_delivery`生成逐记录映射和无损检查日志，解析统计留内部，不夹进客户ZIP。

```bash
python3 -m supplier_test_parser.compact_delivery /旧输出/customer_specs.json /新输出
node supplier_test_parser/customer_workbooks.mjs /新输出 --compact
python3 -m supplier_test_parser.customer_package /新输出 --compact --source-commit <SHA>
```

原页链接存储为可读URL，并通过原生OpenXML外部关系跳转，不使用HYPERLINK公式。导出检查同时扫描工作表XML的`t=e`缓存错误，防止公式模式回读漏检。原PDF分享权限不自动扩大；收件人也可按来源文件名和物理PDF页码定位。完整长文存储不截断，Excel行高上限导致未全部显示的内容可在公式栏阅读。最终放行仍由业务审核负责，不把工程检查与局部抽样当全文准确率。

`customer_workbooks.mjs <输出目录>`读取本地customer_specs.json、engineering_specs.json及已审认证工作簿，生成三个客户Excel和独立验证报告。`python3 -m supplier_test_parser.customer_package <输出目录> --source-commit <SHA>`对持久化Excel逐格回读、扫描全部工作表已知OCR坏串和过程性措辞，再输出固定名称ZIP；包内严格只有三个Excel。认证主表及完整字段值以已审输入为准，不重新推断。

源OCR的二次修订使用review中的post_edits，仍按PDF哈希、物理页码和原行精确匹配。页脚排除限定已核源文件的页码偏移和页边坐标；原始块保留。工程断言、页覆盖与局部抽核均不构成业务签审。缺失附件和源文件访问权限应保持明确，不能为客户包净化而删掉真实边界。

### 终审修订模式

`--review execution_review.json`读取外部提供的源哈希绑定修订，不调用模型。`edits`逐行精确匹配，匹配失败立即中止；保留原识别文字、坐标和修订依据。`expected_gaps`及`duplicate_references`仅按标准、页码、条款白名单降为信息提示，不自动补号。源文件/修订全文只归档Drive，不提交Git。

终审模式下，客户条款主表一条款一行；稳定键包含标准、章节或附录、节点类型和编号。经验测试名按给定规则回填，附录上下文优先，未提供主题不猜测。单元格超过Excel容量时中止而非截断；长文本可通过公式栏或原页对照阅读。

使用`python3 -m supplier_test_parser.standard_parser.review_validation <新目录> <旧目录> <原修复清单.json> <执行修订.json>`生成19条工程断言和稳定键差异，随后运行Excel生成器与打包器。语义终审仍需业务复核，不能用断言通过率冒充准确率。

本工具保证记录处理覆盖和可追溯，不代替逐项业务签审。扫描误识别（如I/II/III、微单位、公式上下标）和表格行列可能仍需原图复核。图表索引保留原图，不把OCR文本伪装成已准确恢复的二维表。业务方负责口径、关键数值抽检和最终对外版。

PDF原件、OCR全文、客户产物、密钥和运行日志均不提交GitHub。GitHub只保存可复现代码、字段定义、合成测试和不含原文的运行统计。
