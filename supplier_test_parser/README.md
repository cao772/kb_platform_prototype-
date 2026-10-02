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

本工具保证记录处理覆盖和可追溯，不代替逐项业务签审。扫描误识别（如I/II/III、微单位、公式上下标）和表格行列可能仍需原图复核。图表索引保留原图，不把OCR文本伪装成已准确恢复的二维表。业务方负责口径、关键数值抽检和最终对外版。

PDF原件、OCR全文、客户产物、密钥和运行日志均不提交GitHub。GitHub只保存可复现代码、字段定义、合成测试和不含原文的运行统计。
