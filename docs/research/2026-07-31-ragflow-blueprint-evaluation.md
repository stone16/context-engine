# RAGFlow → ContextEngine 可复刻蓝图评估

> **决策状态**：本文开放问题已由维护者于 2026-07-31 全部决定（D1/D6/D12），结果见 [`five-repository-implementation-blueprint.md`](./2026-07-31-five-repository-implementation-blueprint.md) §5；正文推荐项为评估时刻的状态。

> 评估日期：2026-07-31  
> 证据边界：RAGFlow 固定 commit 的静态源码、许可证与依赖声明；未启动 RAGFlow，未运行动态 benchmark、OCR 质量测试或故障注入。  
> 结论口径：`copy+patch` 只表示许可证区域允许且下文给出了注册候选；只有逐文件哈希、嵌套依赖/资产许可证、补丁、SBOM 和批准全部闭合后才可进入产品制品。`clean-room` 在本文表示保留行为而按 ContextEngine seam 独立实现，不是因为 RAGFlow 根许可证禁止复制。

## 1. 固定 commit 与许可证核验

### 固定版本与仓库级结论

- **[一手静态]** 上游固定为 `https://github.com/infiniflow/ragflow.git` 的 [`4391e03886b996201f3b8818f671b19eb24d0f7b`](https://github.com/infiniflow/ragflow/commit/4391e03886b996201f3b8818f671b19eb24d0f7b)，根 [`LICENSE`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/LICENSE) 是 Apache License 2.0。
- **[一手静态]** 固定树根没有 `NOTICE` 或第三方 notice 聚合文件；这只证明该 checkout 没有相应路径，不证明所有嵌套代码和模型均无额外义务，参见[固定树](https://github.com/infiniflow/ragflow/tree/4391e03886b996201f3b8818f671b19eb24d0f7b)。
- ADR-0074 因而允许按**精确源码区域**复制 RAGFlow，但不允许以根许可证替代逐路径、依赖和模型资产核验。任何真正落盘的复制仍须位于 `third_party/ragflow/`，并具备 `LICENSE.upstream`、`UPSTREAM.toml`、`MODIFICATIONS.md`、`patches/`、SBOM 和制品内 notice。

### 逐路径许可证区域与准入状态

下表中的“Apache 源码区已核验”表示文件自身带 InfiniFlow Apache-2.0 header 且受根许可证覆盖；“可复制”不等于其依赖闭包或模型制品已获准分发。

| 精确上游路径 | 固定文件 SHA-256 | 静态许可证/依赖结果 | 本评估准入 |
|---|---:|---|---|
| [`deepdoc/parser/docx_parser.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/docx_parser.py) | `891ffc11d2a3ac32e5c0d8b25b35aa62ab8cda1033c9e0a93782e9d45e759586` | **[一手静态]** Apache 源码区；直接依赖 `python-docx`、`pandas`、RAGFlow tokenizer、`LazyImage` 和 `common.constants`。 | `copy+patch` 候选；只复制这一文件，补丁移除所有 RAGFlow 应用层依赖。第三方包许可证文本未在固定 checkout 聚合，注册前补齐。 |
| [`deepdoc/parser/utils.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/utils.py) | `7d1674fb7c92b2db24964575cb2290139a823a923da89a321cbdaea795452849` | **[一手静态]** Apache 源码区；`extract_pdf_outlines` 只需 `pypdf`，同文件 `get_text` 依赖 `rag.nlp.find_codec`。 | `copy+patch` 候选；删除 `get_text`，只保留 outline 提取，并把吞异常改为 typed refusal/warning policy。 |
| [`deepdoc/parser/pdf_parser.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/pdf_parser.py) | `edf235cff17e11eb7a541c11711055f1f57fcb3fdd0d130e4a65b9097d5282eb` | **[一手静态]** Apache 源码区；直接连接 OCR/layout/TSR、`pdfplumber`、`pypdf`、XGBoost、scikit-learn、Pillow、Hugging Face 下载、RAGFlow tokenizer/settings/prompts。构造时可联网下载模型，语言判断使用 `random.choices`，若干解析异常会被吞掉。 | 源码法律区域允许 `copy+patch`，但**当前不准入制品**；必须先闭合下述模型与依赖审计并完成去网络/确定性补丁。 |
| [`deepdoc/vision/__init__.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/__init__.py) | `b48fcf785f6373a891a89b4a22cfe01771653fce3ff87b11abea8c32bf812b16` | **[一手静态]** Apache 源码区；引入整个 vision 图并附带 CLI 文件遍历 helper。 | 仅提取最小 export；不复制 `init_in_out`。 |
| [`deepdoc/vision/ocr.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/ocr.py) | `a4b1c380046584124edacb1d14c2c11fe293e843c0ae32bbe3e3a8a26b8474e3` | **[一手静态]** Apache 源码区；加载 `det.onnx`、`rec.onnx` 和 `ocr.res`，并可调用 `snapshot_download`。 | 条件式 `copy+patch`；模型必须由父进程以 digest-bound bundle 传入，runner 不得下载。 |
| [`deepdoc/vision/operators.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/operators.py) | `0464aa347d171ca18cbf11b3ab7e000c8c69886d9da28c816baf75c6b80a2500` | **[一手静态]** Apache header；没有嵌套来源/notice 声明，并依赖 OpenCV、NumPy、Pillow 与 `rag.utils.lazy_image`。 | 暂缓准入；先做来源/notice 人工审阅，补丁移除 RAGFlow helper。 |
| [`deepdoc/vision/postprocess.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/postprocess.py) | `a63d34ef62721e9d7965c33827f408d576f829d61e47f90c493081c3f419e741` | **[一手静态]** Apache header；没有嵌套来源/notice 声明，依赖 OpenCV、NumPy、Shapely、pyclipper。 | 暂缓准入；先闭合源码来源与包 notice。 |
| [`deepdoc/vision/recognizer.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/recognizer.py) | `f7284fe38e2b88a720475a7d6e6e0214e7aa6654ebcab838244a3eb1c29809fa` | **[一手静态]** Apache 源码区；执行 ONNX 模型并提供稳定几何排序/重叠 helper。 | 条件式 `copy+patch`；推理与纯几何 helper 分文件，禁止隐式模型路径。 |
| [`deepdoc/vision/layout_recognizer.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/layout_recognizer.py) | `52033843da3564f41f7f9c13269862f512cfcf41bfae821a4e19b5cd6e36d33a` | **[一手静态]** Apache 源码区；ONNX 路径可下载 `InfiniFlow/deepdoc`，Ascend 路径引用 `.om` 与 checkout 中不存在的 client。 | 只评估 ONNX 路径；Ascend 分支 `do-not-take`。 |
| [`deepdoc/vision/table_structure_recognizer.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/table_structure_recognizer.py) | `387827e3c1299db7d2867a40eebf64b6765fc941d555c9e26e1f811c72cd3324` | **[一手静态]** Apache 源码区；混合 ONNX/Ascend 推理、RAGFlow tokenizer、typed table 重建和 HTML/string 渲染。 | 条件式 `copy+patch`；只保留 typed grid/span 重建，禁用 HTML 作为 canonical output，删除 Ascend 与 tokenizer 依赖。 |

### 嵌套 notice/模型资产扫描与排除项

- **[一手静态]** 固定仓没有跟踪 `rag/res/deepdoc/**` 模型文件；[`deepdoc/server/download_deps.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/server/download_deps.py) 会从仓外下载 `layout.onnx`、`det.onnx`、`rec.onnx`、`tsr.onnx`，而 [`deepdoc/server/README.md`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/server/README.md#L176-L192) 仅以文档文字称这些资产为 Apache-2.0。资产本体、资产 commit/hash、训练数据/基础模型 notice 不在该固定 Git checkout 内，故实际资产许可为 **[未取证]**。
- **[一手静态]** `pdf_parser.py` 还会下载 `InfiniFlow/text_concat_xgb_v1.0/updown_concat_xgb.model`，见[构造路径](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/pdf_parser.py#L92-L101)。该二进制也不在固定树内，许可与 hash 对本报告而言 **[未取证]**。
- **[一手静态]** 固定 `uv.lock` 锁定了运行包版本，但不提供逐包许可证清单，见 [`uv.lock`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/uv.lock) 与 [`pyproject.toml`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/pyproject.toml)。因此 `python-docx 1.2.0`、`pandas 2.3.3`、`pypdf 6.13.1`、`pdfplumber 0.10.4`、`xgboost 1.6.0`、`scikit-learn 1.8.0`、`Pillow 12.2.0`、`NumPy 1.26.4`、`opencv-python 4.10.0.84`、`onnxruntime 1.23.2`、`huggingface-hub 1.3.1`、`Shapely 2.1.2`、`pyclipper 1.4.0` 必须在复制 PR 中从各自主来源保存许可证/notice，本文不把常识许可证当已核验证据。
- `operators.py`、`postprocess.py` 的实现形状与常见 OCR 工具链相似，但固定仓未给嵌套来源 notice；是否含外部派生代码为 **[未取证]**。在人工逐块来源审查完成前，不批准这两个文件进入分发制品。

必须登记在 PDF/OCR 候选的 `excluded_paths`，且不得由 runner 自行取得的路径/资产如下：

| 排除项 | 原因 |
|---|---|
| `rag/res/deepdoc/**`，包括 `layout*.onnx`、`det.onnx`、`rec.onnx`、`tsr.onnx`、`ocr.res`、`updown_concat_xgb.model` | 固定 Git commit 未携带本体，资产级许可证、hash 与训练/基础模型 notice 未闭合。 |
| `**/*.om`、`deepdoc/vision/dla_cli.py` 及 `AscendLayoutRecognizer`/`_run_ascend_tsr` 分支 | `.om` 本体与 client 不在固定树的可核验闭包，且 ContextEngine 没有 Ascend 运行需求。 |
| `deepdoc/server/**` | 是上游第二个服务边界及联网下载面；违反 ADR-0075 的 owned pure runner 形状。 |
| `deepdoc/parser/__init__.py` | 导入所有 parser，扩大依赖和许可证闭包；现有 Markdown 注册已证明应绕开 initializer。 |
| `rag/nlp/**`、`rag/prompts/**`、`rag/app/**`、`api/**`、`common/settings.py`、`common/file_utils.py`、`common/misc_utils.py`、`rag/utils/lazy_image.py` | RAGFlow 应用、模型、存储和环境配置耦合；用 ContextEngine-owned ports/config 替换。 |
| parser 产出的 HTML、PIL image 对象以及原始 loose dict | 不是许可证排除，而是契约排除：不能成为 canonical Revision/Fragment 或直达 UI。 |

结论：本固定 checkout **没有发现一个已跟踪且已证实“非宽松许可证”的模型文件**，因为模型本体根本不在树内；同样也不能把“未发现”写成“模型可分发”。在资产审计关闭前，DOCX+outline 是唯一可立即准备注册 PR 的新 parser lift，PDF/OCR/layout/table 只可做隔离、不可分发的 evidence spike。

## 2. 能力盘点 → ContextEngine 区域映射表

| 能力/上游位置 | ContextEngine 区域与 seam | 结论 | Kernel/发布边界 |
|---|---|---|---|
| DOCX 段落、style、表格：`deepdoc/parser/docx_parser.py` | Supply / `adapters/parsers` → owned compiler-runner → typed `ParsedDocument` | **copy+patch**；最小源码区，移除 RAGFlow tokenizer/image/application coupling。 | 不改变 Runtime；未来发布仍为 immutable Revision/Fragment。 |
| PDF outline：`deepdoc/parser/utils.py::extract_pdf_outlines` | Supply / format-neutral document compiler | **copy+patch**；小而可隔离。 | Outline 是编译输入/结构，不是授权或 Runtime hydration。 |
| PDF OCR/layout/table：`pdf_parser.py` + `deepdoc/vision/*` | Supply / digest-bound offline model bundle + compiler-runner | **copy+patch（准入门未闭合）**；源码可用，资产/notice 未闭合前不得入制品。 | runner 无网络、DB、index、checkpoint；完整 typed document 成功后才能进入 `prepared`。 |
| PDF/DOCX loose tuple/dict、HTML、PIL 输出 | Supply domain constructor | **do-not-take**；只借识别行为，输出层重写。 | 自验证 constructor 重算顺序、结构、locator、digest 和 bound。 |
| `search.py::search/retrieval` 的后端 weighted-sum、稳定排序 | Runtime candidate discovery / `CandidateQuery`、`RankedCandidateList`、`prekernel_fusion` | **clean-room**；保留本仓原生 PostgreSQL FTS+pgvector 与现有 RRF，补 parity fixtures。 | pre-Kernel 仅 `CandidateRef` + rank evidence；index filter 永不授权。 |
| `rerank*`、`_rank_feature_scores` | Runtime authorized ranking/model inference port | **clean-room**；只在 `AuthorizedProjection` 上实现。 | 精确 cut：`AuthorizationKernel.authorizeAndProject` 之后。 |
| `retrieval_by_toc`、`retrieval_by_children`、`chunk_list` | `FragmentWindowRequest` + content-free expansion refs | **clean-room**；不可复制直接 datastore hydration。 | 同 Article/current Revision 经 lineage 验证继承；跨 Article 发新 `CandidateRef` 并重授权。 |
| `chunk_builder.py::get_parser/run_chunking/extract_outline` | Supply compiler selection/execution | **do-not-take**；registry、TaskContext、DB metadata write 与 v3 seam 冲突。只保留“显式 parser profile + outline 是结构”这一行为。 | 编译器 runner 纯变换，不能写 Doc metadata。 |
| comparator + recording context + write interceptor | Eval / Adapter parity executor | **clean-room**；采用 canonical typed artifact digest，不复制任意 Python object comparator。 | Eval 无发布权；不得把真实生产写返回值重放当安全等价。 |
| Redis unacked queue、task executor、heartbeat | Supply execution/checkpoint bridge | **do-not-take** 部署形状；**clean-room** 吸收 reclaim、阶段记录、取消、终态后 ack 行为。 | PostgreSQL durable job + exact signed WorkerLease 是唯一执行权；runner 无 ambient tenant 或持久化。 |

特别纠偏：**[一手静态]** 固定 `rag/nlp/search.py` 并没有 weighted RRF；它在 [`search`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L134-L245) 中把全文与 dense 表达式交给后端 `FusionExpr("weighted_sum", weights="0.05,0.95")`，随后在 [`retrieval`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L549-L745) 中做内容参与的 rerank 和稳定排序。RRF、exact-ref dedupe 与授权后权重压缩应继续以 ContextEngine 当前 owned 实现为 authority，而不是错误归因并复制 `search.py`。

## 3. 逐能力蓝图

### 3.1 deepdoc PDF/DOCX/table/OCR-layout parser

#### 层级与上游路径

- **[一手静态] DOCX：** [`RAGFlowDocxParser.get_picture/__extract_table_content/__compose_table_content/__call__`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/docx_parser.py#L33-L182) 返回段落文本+style 和表格自然语言字符串；它没有原始 DOCX member/XML locator，也不保留逐表格 cell 的 canonical typed structure。
- **[一手静态] PDF outline：** [`extract_pdf_outlines`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/utils.py#L39-L54) 深度遍历 `pypdf` outline，给出 title/depth/page。
- **[一手静态] PDF pipeline：** [`RAGFlowPdfParser.__call__`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/pdf_parser.py#L1674-L1699) 串接 page image/OCR、layout、table transformer、text merge、跨页 concat、filter 与 table/figure extraction；[`parse_into_bboxes`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/pdf_parser.py#L1701-L1763) 提供分批 page window 和 bbox 输出。
- **[一手静态] provenance 素材：** [`_line_tag/extract_positions/get_position`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/pdf_parser.py#L1443-L2000) 可提供 page/bbox；[`_extract_table_figure`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/pdf_parser.py#L1208-L1417) 组合跨页 table/figure、caption、crop 与 position。
- **[一手静态] OCR/layout/table：** [`OCR`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/ocr.py#L493-L670)、[`LayoutRecognizer`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/layout_recognizer.py#L33-L167)、[`Recognizer`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/recognizer.py#L32-L409) 和 [`TableStructureRecognizer.construct_table`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/vision/table_structure_recognizer.py#L156-L579) 构成推理、阅读顺序、table cell/span 重建与字符串渲染链。

#### 映射的本仓 seam/模块/ADR

- `adapters/parsers/<format>.py` 只能实现 Supply-owned `DocumentCompiler` port；subprocess envelope 复用 ADR-0079 的 fixed deadline、typed `CompilationFailure`、no network/DB/state 约束，并在生产调用时由 ADR-0075 Supply bridge 绑定父 WorkerLease。
- ADR-0036/0038/0079 的**确定性、版本显式、all-or-nothing、self-validation、provenance、hard bound**继续适用，但当前 `engine/supply/markdown.py::ParsedDocument` 的 `SectionKind` 和 UTF-8 byte span 是 Markdown representation，不足以诚实表达 DOCX zip member 或 PDF page+bbox。必须先写一个 ADR：是把 `ParsedDocument` 提升为 format-neutral family，还是新增同族 `ParsedOfficeDocument`/`ParsedPdfDocument` 后在统一 publication seam 汇合。不能把 PDF bbox 伪装成 Markdown byte span。
- 建议的 typed source locator union：
  - `TextByteSpan(source_digest, start, end)`：只用于真正可 round-trip 的文本；
  - `DocxXmlLocator(artifact_digest, part_uri, block_ordinal, xml_digest)`；
  - `PdfRegionLocator(artifact_digest, page_number, bbox_points, page_render_digest, extraction_method)`；
  - table/figure 可携带多个 ordered locators，但仍只生成一个 structural Fragment。
- structural mapping：outline/style heading → `HEADING`；连续 paragraph/text layout → `PARAGRAPH`；DOCX/PDF table（caption+grid+span）→ `TABLE`；list → `LIST`；figure+caption/OCR → 需要新 `FIGURE` kind 或明确拒绝。每一 heading、paragraph、list、table、figure 是一个结构单元和一个 Fragment；table cell、OCR word、PDF line都不是独立 Fragment。
- `ContextRevision` 绑定原始 artifact SHA-256、compiler/config/model-bundle identities 和完整 canonical parsed document digest；`ContextFragment` 绑定 structural path、locator、source text/canonical text、parent headings 与同一 Revision。发布仍走 `prepared -> indexed -> active`，parser success 不等于发布。

#### 复刻配方

1. **先定 representation ADR。** 固定 format profiles（例如 `docx-config-v1`、`pdf-layout-config-v1`）、locator union、structural kinds、canonical serialization、hard bounds（文件/页/像素/blocks/cells/text/model runtime）与 refusal vocabulary。未知 profile 在打开文件或加载模型前拒绝。
2. **拆 runner 输入。** 父进程只传 `artifact_bytes`、exact profile、fixed deadline、以及只读 `ModelBundleRef{name, revision, files[{sha256,size}], license_manifest_digest}`；生产 runner 不能读环境变量选择模型、不能访问 Hugging Face、不能接路径或 DB credential。
3. **先落 DOCX。** 复制 `docx_parser.py` 到新注册 subtree；patch 移除 `rag_tokenizer`、`LazyImage`、`MAXIMUM_PAGE_NUMBER`、Pandas DataFrame。直接遍历 OOXML block order（paragraph/table interleave，而不是上游分别遍历 `document.paragraphs` 和 `document.tables`），输出 owned raw blocks。记录 part URI、block ordinal、style、XML digest、paragraph/table canonical text；图片先输出 typed attachment descriptor 或拒绝，绝不返回 mutable PIL/LazyImage。
4. **加入 outline helper。** 从 `utils.py` 只保留 `extract_pdf_outlines` 的行为；异常按 profile 返回 `OUTLINE_UNAVAILABLE` warning 或使整个 compilation refusal，不能无条件 `except: return []`。outline title/depth/page 必须验证页范围并按源顺序稳定。
5. **做 PDF source-only spike。** 复制候选九文件到 `/tmp` 或尚未分发的受控 spike；先 patch 掉 `snapshot_download`、所有 Ascend/LLM/application imports、`random.choices`、环境变量控制、HTML canonical output和吞异常。语言采样改为固定 page/character stride；所有几何排序加完整 tie-break `(page, top, x0, bottom, x1, source_ordinal)`。
6. **关闭资产门。** 对每个 ONNX/XGBoost/dictionary 文件固定独立上游 repo revision、SHA-256、size、license、NOTICE、模型 card、训练/基础模型 obligations；任何一项未知则 PDF/OCR profile `NOT_ACTIVE`。将许可证文本和模型 components 写入 RAGFlow subtree SBOM及 wheel/sdist/container。
7. **分离推理与结构内核。** ONNX 只输出 bounded raw detections；ContextEngine-owned constructor独立验证 bbox 有限/页内、class vocabulary、置信度范围、reading order、caption/table association、cell spans和总量。parser 提供的 dict/HTML/IDs 都是不可信中间态。
8. **生成 coherent units。** 同表 caption、header、body、row/col spans合成一个 typed table Fragment；同 figure caption/OCR 合成一个 figure Fragment；heading ancestry在编译时复制到同 Fragment并计入 budget，不留 Runtime parent lookup。
9. **确定性和 all-or-nothing。** 两个 fresh subprocess、不同 hash seed/CPU thread setting对同 fixture产生逐字节相同 canonical document与 digest；模型输出若平台不可逐位稳定，则在 ADR 中定义固定量化/排序语义并证明跨目标平台一致，否则该 profile只支持一个明确 runtime target。
10. **接 publication。** 只有 typed constructor通过后，Supply worker在当前 exact WorkerLease generation下 staging；完整 Revision、Fragments、embedding/candidates、events准备完毕后才 CAS active pointer。runner永远不直接写这些表。

DOCX+outline 候选 `UPSTREAM.toml`（实施时拆成新的 registration block或新文件，不覆盖已注册 Markdown）：

```toml
repository = "https://github.com/infiniflow/ragflow.git"
commit = "4391e03886b996201f3b8818f671b19eb24d0f7b"
source_paths = [
  "deepdoc/parser/docx_parser.py",
  "deepdoc/parser/utils.py",
]
excluded_paths = [
  "deepdoc/parser/__init__.py",
  "deepdoc/parser/pdf_parser.py",
  "deepdoc/vision",
  "rag/nlp",
  "rag/utils/lazy_image.py",
  "common/constants.py",
]
reuse_mode = "copy-patch"
approval = "PENDING: create a dedicated ContextEngine issue/ADR after format contract closure"
license = "Apache-2.0"
nested_dependency_audit = "PENDING: attach primary license texts before approval"
nested_dependencies = [
  { name = "python-docx", version = "1.2.0", license = "PENDING_PRIMARY_VERIFICATION" },
  { name = "pypdf", version = "6.13.1", license = "PENDING_PRIMARY_VERIFICATION" },
]

[[files]]
upstream_path = "deepdoc/parser/docx_parser.py"
vendored_path = "third_party/ragflow/deepdoc/parser/docx_parser.py"
sha256 = "891ffc11d2a3ac32e5c0d8b25b35aa62ab8cda1033c9e0a93782e9d45e759586"

[[files]]
upstream_path = "deepdoc/parser/utils.py"
vendored_path = "third_party/ragflow/deepdoc/parser/utils.py"
sha256 = "7d1674fb7c92b2db24964575cb2290139a823a923da89a321cbdaea795452849"
```

PDF/OCR/layout/table 候选模板（这是**blocked registration**；`approval` 和所有 `PENDING` 关闭前不得复制进工作树）：

```toml
repository = "https://github.com/infiniflow/ragflow.git"
commit = "4391e03886b996201f3b8818f671b19eb24d0f7b"
source_paths = [
  "deepdoc/parser/pdf_parser.py",
  "deepdoc/parser/utils.py",
  "deepdoc/vision/__init__.py",
  "deepdoc/vision/ocr.py",
  "deepdoc/vision/operators.py",
  "deepdoc/vision/postprocess.py",
  "deepdoc/vision/recognizer.py",
  "deepdoc/vision/layout_recognizer.py",
  "deepdoc/vision/table_structure_recognizer.py",
]
excluded_paths = [
  "deepdoc/server",
  "deepdoc/parser/__init__.py",
  "rag/res/deepdoc",
  "rag/nlp",
  "rag/prompts",
  "rag/app",
  "api",
  "common/settings.py",
  "common/file_utils.py",
  "common/misc_utils.py",
  "rag/utils/lazy_image.py",
  "**/*.om",
]
reuse_mode = "copy-patch"
approval = "BLOCKED: model/dependency/source-lineage audit and representation ADR required"
license = "Apache-2.0-source-region"
model_assets = [
  { name = "layout*.onnx", sha256 = "PENDING", license = "UNVERIFIED_AT_PINNED_GIT_COMMIT" },
  { name = "det.onnx", sha256 = "PENDING", license = "UNVERIFIED_AT_PINNED_GIT_COMMIT" },
  { name = "rec.onnx", sha256 = "PENDING", license = "UNVERIFIED_AT_PINNED_GIT_COMMIT" },
  { name = "tsr.onnx", sha256 = "PENDING", license = "UNVERIFIED_AT_PINNED_GIT_COMMIT" },
  { name = "ocr.res", sha256 = "PENDING", license = "UNVERIFIED_AT_PINNED_GIT_COMMIT" },
  { name = "updown_concat_xgb.model", sha256 = "PENDING", license = "UNVERIFIED_AT_PINNED_GIT_COMMIT" },
]

[[files]]
upstream_path = "deepdoc/parser/pdf_parser.py"
vendored_path = "third_party/ragflow/deepdoc/parser/pdf_parser.py"
sha256 = "edf235cff17e11eb7a541c11711055f1f57fcb3fdd0d130e4a65b9097d5282eb"
[[files]]
upstream_path = "deepdoc/parser/utils.py"
vendored_path = "third_party/ragflow/deepdoc/parser/utils.py"
sha256 = "7d1674fb7c92b2db24964575cb2290139a823a923da89a321cbdaea795452849"
[[files]]
upstream_path = "deepdoc/vision/__init__.py"
vendored_path = "third_party/ragflow/deepdoc/vision/__init__.py"
sha256 = "b48fcf785f6373a891a89b4a22cfe01771653fce3ff87b11abea8c32bf812b16"
[[files]]
upstream_path = "deepdoc/vision/ocr.py"
vendored_path = "third_party/ragflow/deepdoc/vision/ocr.py"
sha256 = "a4b1c380046584124edacb1d14c2c11fe293e843c0ae32bbe3e3a8a26b8474e3"
[[files]]
upstream_path = "deepdoc/vision/operators.py"
vendored_path = "third_party/ragflow/deepdoc/vision/operators.py"
sha256 = "0464aa347d171ca18cbf11b3ab7e000c8c69886d9da28c816baf75c6b80a2500"
[[files]]
upstream_path = "deepdoc/vision/postprocess.py"
vendored_path = "third_party/ragflow/deepdoc/vision/postprocess.py"
sha256 = "a63d34ef62721e9d7965c33827f408d576f829d61e47f90c493081c3f419e741"
[[files]]
upstream_path = "deepdoc/vision/recognizer.py"
vendored_path = "third_party/ragflow/deepdoc/vision/recognizer.py"
sha256 = "f7284fe38e2b88a720475a7d6e6e0214e7aa6654ebcab838244a3eb1c29809fa"
[[files]]
upstream_path = "deepdoc/vision/layout_recognizer.py"
vendored_path = "third_party/ragflow/deepdoc/vision/layout_recognizer.py"
sha256 = "52033843da3564f41f7f9c13269862f512cfcf41bfae821a4e19b5cd6e36d33a"
[[files]]
upstream_path = "deepdoc/vision/table_structure_recognizer.py"
vendored_path = "third_party/ragflow/deepdoc/vision/table_structure_recognizer.py"
sha256 = "387827e3c1299db7d2867a40eebf64b6765fc941d555c9e26e1f811c72cd3324"
```

Patch-diff sketch：

```diff
- from huggingface_hub import snapshot_download
- from common import settings
- from rag.nlp import rag_tokenizer
- model_dir = snapshot_download(...)
+ from context_engine_runner_contract import ModelBundle, RawStructuralBlock
+ model_dir = verified_read_only_bundle.require_files(expected_manifest)
+ # no environment-derived mode, network, database, application service or LLM

- random.choices(page_chars, k=min(100, len(page_chars)))
+ stable_stride_sample(page_chars, maximum=100)

- except Exception:
-     logging.exception(...)
-     return []
+ except ClosedParserError as error:
+     raise CompilationFailure(category=map_closed_category(error)) from None

- return secs, tbls
+ return tuple(RawStructuralBlock(..., source_locator=...))

- return "<table>..."
+ return TypedTable(caption=..., cells=..., row_spans=..., column_spans=...)
```

#### 验证计划

- `make test`：DOCX/PDF domain constructor、malformed zip/PDF、zip bomb、page/pixel/block/cell bounds、out-of-range bbox、duplicate/overlap tie、outline cycle/invalid page、table colspan/rowspan、no HTML execution、all-or-nothing、model manifest mismatch、runner timeout/kill、two-process digest determinism。
- `make catalog`：只有 owned runner adapter 可 import vendored deepdoc；production runner禁止 `socket`/Hugging Face/DB/settings/host path；第三方 registration/hash/SBOM/制品 notice完整；Markdown 已注册文件不得被新 initializer 间接扩大依赖。
- `make integration`（需要先由协调者按仓约启动 DB）：真实 PostgreSQL 下 staged Revision 不可见、完整结构单元原子激活、旧/新 Revision全有或全无、每 Fragment同 Article/Revision lineage、失败不产生部分 Fragment、恢复不重新调用已完成的确定性编译步骤。
- `make security-gate`：PDF/DOCX候选混入 denied/cross-Organization 时正文进入 content-bearing consumer 数为 0；parser metadata、outline、bbox、index字段均不能授权。
- 额外离线 corpus gate：按格式/语言/扫描质量/table/outline/异常切片报告 structural recall、reading-order、table exactness 与 refusal；未运行前均为 **[未取证]**，不得称 PDF/OCR 已可用。

#### 工作量估计与依赖

| 子项 | 工程日 | 依赖 |
|---|---:|---|
| representation ADR + format-neutral typed contracts | 4–6 | 维护者决定 PDF/DOCX locator 与 figure kind |
| DOCX+outline copy/patch、注册、单测 | 6–9 | 依赖许可证文本、OOXML fixtures |
| PDF source-only deterministic spike | 8–12 | representation ADR、离线模型 bundle contract |
| 模型/依赖/source-lineage 法务与 SBOM closure | 4–8 | 模型实际来源 owner；可能并行，但结果可否决 |
| PDF/OCR/layout/table runner 产品化与 integration | 15–22 | 前述两门全绿、固定 supported runtime target |

总体：DOCX+outline 10–15 engineer-days；PDF/OCR 再增加 27–42 engineer-days。若模型资产门失败，PDF/OCR 立即停止，不把 sunk cost 当准入理由。

### 3.2 `rag/nlp/search.py` retrieval pipeline

#### 层级与上游路径及逐函数 cut line

| 函数 | 固定源码事实 | ContextEngine cut/结论 |
|---|---|---|
| [`get_vector`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L55-L62) | **[一手静态]** query embedding → dense match expression。 | 可借行为，继续用 owned query embedding/provider profile；只产生 candidate plan，不读 Fragment正文。 |
| [`get_filters`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L121-L132) | **[一手静态]** 从 request dict接受 KB/doc/id 等 filter。 | `do-not-take`；caller filter只可 RequestNarrowing，不能构造可信 scope。 |
| [`search`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L134-L245) | **[一手静态]** 默认 `src` 包含正文、title、doc name、position、tags等，并使用后端 weighted-sum。 | 整函数不能位于 pre-Kernel。保留本仓 FTS/pgvector ports，仅返回 named ranked `CandidateRef` lists和可选原始分数。 |
| [`_prune_deleted_chunks`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L64-L119) | **[一手静态]** 根据 DB document存在性过滤 stale index chunk，自称 temporary safety net。 | `do-not-take` 为授权；current Resource/Revision/tombstone由 Kernel/authoritative projection检查，index cleanup另行异步。 |
| [`_knn_scores`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L363-L394) | **[一手静态]** 对既有 candidate IDs取 KNN分数且不取 source fields。 | pre-Kernel 可等价实现为 content-free `RankerEvidence.score`，但分数不能进入 Kernel决策。 |
| [`rerank_with_knn`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L434-L459)、[`rerank`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L461-L492)、[`rerank_by_model`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L494-L519) | **[一手静态]** 全部读取 `content_ltks`/title/question/keywords；model版本还把 docs送外部 reranker。 | **精确 cut：全部 post-Kernel**；输入必须是 `AuthorizedRerankItem(AuthorizedProjection, rank evidence)`。模型调用还需 governed inference/egress/budget。 |
| [`_rerank_window`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L524-L547) | **[一手静态]** 使 candidate window成为 page size整倍数。 | 可 clean-room保留分页 invariant；server-owned bound，不能由 caller使候选提交超过 ceiling。 |
| [`retrieval`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L549-L745) | **[一手静态]** 一个函数内混合 recall、stale prune、正文 rerank、稳定排序、分页和正文 hydration。 | 必须拆开：`discover → fuse refs → Kernel → compact/reweight admitted ranks → authorized rerank → budget`。不能复制整函数。 |
| [`chunk_list`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L751-L799) | **[一手静态]** 按 doc_id直接读 chunk fields。 | 仅可由 Kernel构造的 `FragmentWindowSession` 间接触发；不是公共/可插拔 datastore port。 |
| [`retrieval_by_toc`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L839-L900) | **[一手静态]** 读取 TOC正文、调用 chat model、直接 get 新 chunk正文。 | post-Kernel clean-room；TOC model只接 AuthorizedProjection；它返回 expansion refs，不直接 hydrate。 |
| [`retrieval_by_children`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L902-L940) | **[一手静态]** child有 `mom_id` 时直接读取 parent chunk并用 parent正文替换。 | post-Kernel clean-room；同 Article/current Revision经 lineage验证继承，任何 parent指向另一 Article都返回 CandidateRef给 Kernel重授权。 |
| [`insert_citations`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py#L251-L328) | **[一手静态]** 处理答案文本并插入 chunk IDs。 | `do-not-take` 到 ContextRuntime；引擎不生成答案，BotDelivery只从当前 ContextPackage/Evidence closure构造引用。 |

#### 映射的本仓 seam/模块/ADR

- Candidate discovery：`PostgreSQLHybridCandidateIndex` 返回 FTS/vector 两个 `RankedCandidateList`；`fuse_candidate_evidence` pre-Kernel exact-ref dedupe并仅携带 rank evidence。
- Authorization：sealed `AuthorizationKernel`是唯一 `CandidateRef -> AuthorizedProjection`构造者；它不可见/不依赖 rank权重，避免排名成为授权侧信道或裁决输入。
- 授权后排序：`join_authorized_ranking`只 join admitted projections，并对 admitted candidates压缩每 ranker position后应用 server-owned weights。RAGFlow的固定 `0.05/0.95` 不能变成 caller参数。
- 内容相关性：本地/外部 reranker只接受 `AuthorizedRerankItem`；模型输入、输出、profile、成本、egress在 governed inference port记录。
- Expansion：`FragmentWindowRequest(anchor=AuthorizedProjection, ...)`；同 Article/current active Revision继承必须由 authoritative reader与 Kernel双检，cross-Article refs回到 Kernel。

**Kernel cut line（不可含糊）：** `CandidateQuery/RankedCandidateList → bounded exact-ref dedupe/RRF → tuple[CandidateRef] → AuthorizationKernel.authorizeAndProject → tuple[AuthorizedProjection] → admitted-rank compaction/weights → content rerank → expansion refs → Kernel reauthorization → PackageBudget`。`search.py` 中凡读取 `field`、正文、title、tag、TOC、parent或调用 relevance model的代码一律在 cut 之后，且不能直接接受 RAGFlow dict。

#### 复刻配方

1. 冻结现有 `CandidateQuery`/`RankerEvidence` canonical fixtures，加入一个与 RAGFlow fixed behavior对应的 weighted-sum oracle，但将它标为“quality comparison”，不是 RRF provenance。
2. 给 FTS/vector lists定义总排序：ranker position优先，随后 `_candidate_sort_key`；同一 ranker重复 exact ref只计首次，不同 Article即使 fragment_ref相同也不 dedupe。
3. pre-Kernel只保存每 ranker position与optional finite score；禁止 title/snippet/path/tag/vector/body进入 `CandidateRef`或 pre-Kernel trace。
4. 继续使用 uniform provisional RRF排 bounded authorization order；真正 server-owned weights只在 admitted candidates上重新压缩 position并计算，denied candidate不得改变最终 admitted order。
5. 若引入 RAGFlow式 lexical/vector score blending，新建 `AuthorizedScoreBlendProfile`，输入是授权后的投影+分离 rank evidence；先离线 ablation，再由唯一 ReleaseManifest promote owner激活，request body不能提交权重。
6. reranker adapter nominal type只接受 `tuple[AuthorizedRerankItem,...]`；测试用反射/静态 gate禁止 `CandidateRef`、candidate session、DB connection或raw index fields传入。
7. TOC/parent flow先输出 bounded `ExpansionPlan`：`same_article_current_revision_fragment_refs` 与 `reauthorization_refs` 两组；Kernel核对 active Revision、source ACL projection lineage与Article identity后才返回 content。
8. PackageBudget在 expansion/rerank后统一选择；heading/parent context必须已在projection中可见计费，不能出现隐藏 parent text。
9. 所有 rejected/denied rank evidence只进入受限安全 lineage的类别/摘要，不进入 tenant-visible ContextRun、debug或Learning corpus。
10. 用同一 fixture跑现有实现和行为 oracle；结果差异以 retrieval metrics/ordering报告，不以“上游一致”自动批准。

没有建议复制的 `search.py` 代码，因此不新增 `UPSTREAM.toml`；把整文件列入现有 RAGFlow registration的 `excluded_paths` 是正确状态。

#### 验证计划

- `make test`：weighted RRF worked examples、重复ref、同fragment不同Article、tie total order、NaN/inf/overbound、denied candidate不改变admitted权重、hostile rank evidence、reranker nominal input、TOC/parent same/cross Article矩阵、budget可见性。
- `make catalog`：pre-Kernel模块import allowlist只含 content-free types；Kernel不导入/访问 rank evidence；content consumers的type hints只允许 `AuthorizedProjection`/`AuthorizedRerankItem`；index filter无授权语义。
- `make integration`：真实PG混入authorized/denied/cross-org/stale/tombstoned refs；断言denied正文、title、tag、TOC、parent进入reranker/assembler为0；same Article/current Revision window成功，stale Revision与cross Article未重授权均失败。
- `make security-gate`：Unauthorized Evidence=0，并验证rank顺序/阈值/aggregate不泄露 denied existence。质量变化另进 `make eval-v1-execute` 的预注册slice，不可抵消安全失败。

#### 工作量估计与依赖

- 固定行为 oracle与现有 RRF审计：3–5 engineer-days。
- authorized score blend + release profile：5–8 engineer-days，依赖 golden corpus/ablation和 release manifest schema。
- governed reranker adapter：5–8 engineer-days，依赖 inference/egress port及预算计量。
- TOC/parent expansion：6–10 engineer-days，依赖现有 FragmentWindow integration与 format compiler structural metadata。

最低下一步（只补证据）3–5天；完整内容rerank+expansion 16–26天。

### 3.3 `task_executor_refactor/chunk_builder.py` 与 v3 compiler

#### 层级与上游路径

- **[一手静态]** [`get_parser`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/chunk_builder.py#L38-L67) 是 RAGFlow application-level factory，import十余 `rag.app`模块并用字符串/`ParserType`选 parser。
- **[一手静态]** [`run_chunking`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/chunk_builder.py#L70-L109) 把 tenant/kb/task config、progress callback与limiter传给 `chunker.chunk`，返回 loose chunk dict。
- **[一手静态]** [`extract_outline`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/chunk_builder.py#L112-L133) 从第一 chunk弹出 `__outline__`并直接写 `DocMetadataService`，写失败仅warning。

#### 映射与结论

结论是 **skip代码、adopt三个行为、adapt到owned seams**：

1. adopt“parser由显式profile选择”，但选择权属于 immutable SourceVersion/CompilationProfile，不是task字符串；
2. adopt“解析受bounded limiter/deadline控制”，但runner由父WorkerLease和server config控制，不把tenant/kb/callback传进parser；
3. adopt“outline是结构输出”，但它必须在同一个typed compilation digest/Revision内，不能作为best-effort metadata side write。

它与ADR-0079 v3 compiler不能合并：v3已固定 exact Markdown grammar、raw byte spans、self-validating constructor、2048-token ceiling和owned subprocess。`chunk_builder.py`既不提供这些contract，也会重新引入parser registry、DB write与ambient settings。对PDF/DOCX应复用runner envelope/失败语义，而不是复用Markdown grammar或这个application orchestrator。

#### 复刻配方

1. 新建closed `CompilationProfileRef -> DocumentCompilerFactory` composition map，只有composition root能注册；未知profile在artifact read/model load前失败。
2. `CompilerRunnerRequest`只含artifact bytes/digest、profile/version、bounds、可选verified model bundle ref与父job binding摘要；不得含tenant-authored parser id、DB service或callback。
3. runner返回`ParsedDocument | CompilationFailure`的canonical bytes；progress只由父进程根据bounded lifecycle阶段记录，不允许parser任意消息落入durable tenant-visible记录。
4. outline成为`ParsedSection`/document structural index的一部分；空outline是profile允许的明确值，解析异常则按closed category处理。
5. 删除任何“从first chunk pop magic key”的contract；domain constructor拒绝未知字段、重复structure path与out-of-order units。
6. Markdown v1/v2/v3 dispatch保持冻结；PDF/DOCX使用自己的format version，绝不让新factory默默重新解释已有Revision。

无复制项，故无新TOML或patch。

#### 验证计划

- `make test`：profile registry闭集、unknown version before I/O、runner request字段shape、no callback/DB/network、outline在digest内、failure无部分document、v1/v2/v3 frozen fixture不变。
- `make catalog`：parser factory只能从composition root引用；raw compiler/runner private capability不被生产其他模块import；`DocMetadataService`等持久化符号不在parser dependency closure。
- `make integration`：profile change创建new Revision；失败/outline差异不修改active；recovery在同一compiler/config/model digest下复用prepared boundary。

#### 工作量估计与依赖

2–4 engineer-days，依赖3.1的representation ADR。它是小型composition cleanup，不应成为独立parser framework项目。

### 3.4 comparator differential verification → Adapter parity gate

#### 层级与上游路径

- **[一手静态]** [`RecordingContext`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/recording_context.py#L110-L258) 用context-local dict记录任意中间Python values与timing；`NullRecordingContext`给production关闭记录。
- **[一手静态]** [`WriteOperationInterceptor`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/write_operation_interceptor.py#L27-L133) 用allowlist和FIFO回放旧路径写操作返回值。
- **[一手静态]** [`ContextComparator.compare/compare_value`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/comparator.py#L92-L257) 去掉若干时间字段后递归比较；chunk比较按count、ID set、selected content/vector fields分层，见 [`_compare_chunks`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/comparator.py#L298-L454)。
- **[一手静态]** [`TaskManager.dry_run_task`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/task_manager.py#L109-L180) 用旧执行记录构造interceptor、跑新handler、再比较；当前差异只logging，不是release veto。

#### 映射的本仓 seam/模块/ADR

- 放在 `eval/parity/`，不进入production Runtime/Supply composition，也不拥有ReleaseManifest promotion。
- 比较对象不是arbitrary locals，而是每个public/internal seam定义的canonical artifact：`ParsedDocument`、serialized `SupplyChangePage`、`CandidateQuery/FusedCandidates`、`AuthorizedProjection`的安全测试投影、publication plan等。
- 安全seam不允许“等价容差”：Organization/Article/Revision/Fragment refs、source locators、正文、field projection、policy lineage、拒绝类别必须exact；float容差只适用于明确声明的quality measurement，且不能把NaN/缺字段当相等。
- parity PASS只是替换必要条件，不是安全/质量/发布充分条件；仍需`make catalog/integration/security-gate`与唯一promote authority。

#### 复刻配方

1. 定义`ParityFixtureManifest`：fixture bytes SHA-256、format/profile/compiler/model identities、old/new implementation refs、expected outcome kind、允许差异清单及审批issue。fixture不可从生产tenant抓取。
2. 为每个seam实现独立canonical serializer（RFC8785 JSON或既有canonical bytes）；serializer先用domain constructor自验证，再计算domain-separated SHA-256。
3. `ParityExecutor`在两个隔离runner中对相同immutable input分别执行old/new；禁止共享mutable object、clock、random、network、DB connection或上一个实现的return values。
4. 捕获closed stage records：input digest、output kind、canonical output digest、structure count/order、warning/refusal category、implementation/config/model digest、duration bucket。默认不保留正文；需要diff时写owner-only短期artifact并记录删除策略。
5. comparator先比outcome kind，再比schema/ordered structural identities，再比canonical digest；digest不同才生成bounded field-path diff。不得像上游那样浅层strip任意`seconds/_created_time`后宣称等价。
6. 预期representation变化必须在fixture manifest逐条登记`expected_divergence`和迁移理由；没有审批的差异全部FAIL。missing key、extra key、duplicate ID、order change、unconsumed intercepted write均FAIL。
7. 写mutation tests：删除一个Fragment、交换顺序、改bbox/byte span、改Article、改模型digest、改warning/refusal、同ID不同content、向量维度变化、NaN、只改non-authoritative timing。只有最后一种按contract忽略。
8. 对带副作用Adapter使用ephemeral owned sink记录**intent canonical digest**，不回放生产写返回值；两个实现均不得获得production DB credentials。随后在integration中分别执行同一intent到fresh fixture DB验证durable state digest。
9. 报告closed状态：`EXACT_MATCH`、`APPROVED_DIVERGENCE`、`MISMATCH`、`REFUSED`；只有前两者且安全/许可证门另行通过，候选才可提交激活评审。
10. 把fixture manifest、report schema与implementation digests纳入版本控制；大/敏感fixture按现有golden corpus durable-root规则保存并验证lineage。

无复制项：RAGFlow comparator的核心价值是迁移方法，不是其Python-object比较代码；不新增TOML。

#### 验证计划

- `make test`：canonical serializer、上述mutation matrix、expected divergence审批、refusal、report determinism、owner-only/redacted diff、两个fresh process。
- `make catalog`：Eval executor无production secrets/ports、无promotion import、禁止caller-authoredsecurity observations；parity report不能写active pointer。
- `make integration`：fresh PG各执行old/new intent，比较durable rows/events/active pointer digest；失败路径均无部分publication或wrong-org row。
- `make security-gate`：parity工具本身不能绕开CandidateRef→Kernel→AuthorizedProjection或伪造clean security observation。

#### 工作量估计与依赖

6–9 engineer-days建立通用gate；每新增Adapter fixture 1–2天。依赖canonical serializers和golden artifact存储；不依赖PDF模型准入，可先覆盖Markdown、Candidate fusion和Supply page。

### 3.5 Task executor job/queue shape → signed WorkerLease runner lifecycle

#### 层级与上游路径

- **[一手静态]** [`collect`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor.py#L203-L272) 先尝试Redis unacked iterator，再消费新消息；空、未知或已取消task会ack。部分task fields（包括某些tenant信息）来自消息。
- **[一手静态]** [`handle_task`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor.py#L1719-L1785) 在finally后ack消息；异常更新progress，operation log另行记录。ack与业务DB状态不是一个事务。
- **[一手静态]** [`insert_chunks`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor.py#L1270-L1368) 分batch插入并更新chunk IDs，取消时尝试删除部分RAPTOR写入；这是值得测试的crash/cancel窗口，但不是可继承的发布原子性。
- **[一手静态]** [`report_status`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor.py#L1798-L1876) 向Redis写worker heartbeat、pending/lag/done/failed/current tasks，并清理过期executor；这是liveness观测，不是job authority。
- **[一手静态]** 上游`TaskContext`只强制`id`与`tenant_id`存在，其他字段有大量默认值，见 [`TaskContext`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/task_context.py#L70-L280)。ContextEngine不能接受“字段存在”作为可信tenant/job绑定。

#### 映射的本仓 seam/模块/ADR

- ADR-0075/现有 Supply bridge：PostgreSQL durable job是唯一truth；server-minted WorkerLease精确绑定Organization、SourceVersion、job、ServiceActor、allowed operation、audience、policy epoch、generation、nonce、idempotency和expiry。
- runner是一次性subprocess，只消费exact job envelope、执行纯adapter/compiler、把bounded page/artifact发回父进程；无DB/queue/index/checkpoint持久化。
- checkpoint只有在引擎durably接受完整change page后推进；publication watermark另算。queue delivery、heartbeat、parser success都不推进二者。
- Recovery使用更高lease generation；被替换generation/nonce再无任何写效果。已有ADR-0060的bounded retry上限优先于上游无限unacked reclaim形状。

#### 复刻配方：要写进规范的生命周期行为

1. **Durable-before-dispatch：** job row先提交`pending`，固定Organization/Source/SourceVersion/operation/input digest/config/model bundle/attempt ceiling；dispatch消息只含opaque job ref，不能含可信tenant或可覆盖fields。
2. **Atomic claim：** scheduler在function-only DB authority中选择当前可执行job，验证source active、ServiceActor、policy epoch、retry budget，原子写`claimed(generation, lease_digest, expires_at)`后签发lease。签名成功但未送达可在expiry后以更高generation恢复。
3. **Pre-spawn verification：** 父worker在打开artifact、模型、DB transaction前验证signature、job/org/source/audience/operation/generation/nonce/expiry；缺字段、默认tenant、消息与DB不一致统一`work not available`且effect=0。
4. **One-shot runner：** subprocess只收canonical request，通过固定IPC；限制wall time、CPU/memory、stdout/stderr、page/artifact bytes；超时/kill/crash转closed refusal，子进程没有凭证也不能ack job。
5. **Stage recording：** append-only content-free events `claimed`, `runner_started`, `artifact_emitted`, `prepared`, `indexed`, `active`, `completed`, `refused`, `interrupted`, `reclaimed`；只记digests、counts、closed categories和generation，不记正文、denied IDs或parser stack trace。
6. **Write interception：** 在candidate implementation/parity模式，runner输出被ephemeral sink捕获，绝不触发publication。生产模式中所有写由父进程已有definer functions/transaction执行；allowlist按typed command而不是字符串method name定义，未知command拒绝。
7. **Idempotent boundaries：** 每个committed step比较job/generation/input/output digest；相同重放返回原receipt，不同digest拒绝。`prepared`完整包含Revision/Fragments/candidates；`active`只有CAS pointer transaction可见。
8. **Recovery：** reclaim从最后一个verified durable checkpoint继续；prepared digest匹配时不重跑parser/embedding，缺失或不匹配则拒绝而不是猜测。旧generation在每个write function再次验证，不能在长runner结束后偷写。
9. **Cancel/preempt：** cancel/preempt是trusted Control操作，提交cancel state并使当前generation失效；runner可被kill，但可见性由active pointer/tombstone决定，不靠best-effort index delete。需要替换still-live lease时必须显式operator reason digest。
10. **Terminal acknowledgement：** 只有`completed/refused/terminal_failed` durable transition后才认为dispatch完成；若外部queue存在，ack在该receipt之后，ack失败只会导致安全的idempotent replay。V1不因上游有Redis就新增第二queue。
11. **Liveness不授权：** heartbeat可报告opaque worker、stage、lease-expiry bucket、bounded counters；不能延长lease、选择Organization或证明job仍可写。lease renewal若未来需要，另立ADR，不能由heartbeat隐式实现。
12. **Fault matrix：** 在claim前/后、runner spawn前/后、artifact receipt前/后、prepared/indexed/active transaction前/后、terminal transition与ack之间逐点kill；每点断言old-or-new visibility、effect≤1、old generation effect=0、checkpoint/watermark不倒退。

无复制项：Redis consumer、TaskContext、task manager和write interceptor均列入`excluded_paths`；吸收的是observable lifecycle oracle。

#### 验证计划

- `make test`：lease codec/envelope、missing context、wrong job/org/source/audience、expiry/replay/generation、runner timeout/oversize/malformed output、event redaction、typed write allowlist、idempotent step reducer。
- `make catalog`：runner import/network/DB/credential禁令；worker role只能调用允许的functions；所有durable tenant tables FORCE RLS；无第二queue/active pointer authority。
- `make integration`：真实PG concurrent claim、token-not-delivered、旧generation迟到、preempt、每个fault point、prepared recovery、terminal receipt后重放、checkpoint与watermark分离。
- `make smoke`：API/worker/runner process readiness和一次one-shot job；green smoke不升级为publication/security证明。
- `make security-gate`：cross-job/cross-org/replay/missing context的业务effect全为0，且worker refused details不进入tenant ContextRun/Learning。

#### 工作量估计与依赖

8–12 engineer-days补齐规范、fault fixtures与runner stage receipts；如果现有Supply bridge已覆盖部分项目，实际可压到5–8天。依赖真实PostgreSQL harness、现有WorkerLease/Supply bridge与compiler-runner envelope，不依赖RAGFlow部署。

## 4. 不可借鉴清单与必须杀死的隐含前提

| 上游形状/诱人捷径 | 处置 | 必须杀死的隐含前提 | ContextEngine硬证据 |
|---|---|---|---|
| 根Apache许可证覆盖一切 | 不接受 | 根license自动覆盖外部模型、训练资产、复制片段和所有依赖notice | 每个source/model/dependency精确revision+hash+license+SBOM；缺一即不准入 |
| `server/README`写Apache即可下载ONNX | 排除资产 | 文档声明等于模型本体/训练依赖法律审查 | 固定模型repo revision、本体hash、license/notice/model card；当前[未取证] |
| 复制整个`deepdoc/parser`或initializer | do-not-take | 同目录就是一个许可证/依赖区域 | exact path allowlist；initializer、server、rag/app/nlp/common耦合全排除 |
| parser成功返回loose dict/tuple | do-not-take | 有文字/框就能发布 | self-validating typed constructor、all-or-nothing、canonical digest、bounds、immutable publication |
| PDF bbox可冒充UTF-8 byte span | 杀死 | 所有格式共享Markdown provenance坐标 | format-specific locator ADR，绑定artifact/page/member/render digest |
| OCR word/table cell各自成chunk | do-not-take | 越细越利于检索且不损语义 | 一structural unit一Fragment；cell/word仅typed metadata；同Fragment ancestry/budget |
| HTML table是canonical内容 | do-not-take | 上游生成的HTML可安全存储/渲染 | typed table canonical form；任何UI rendering另做escaping/security review |
| runner可按环境变量/缺文件联网下载模型 | 禁止 | 运行环境是可信且网络永远可复现 | 父进程传digest-bound read-only bundle；runner网络=0 |
| random sampling和并行浮点推理仍天然deterministic | 杀死 | 同输入通常同输出即可支撑Revision identity | fixed sampling/ties/runtime target/quantization + fresh-process digest proof |
| `search.py`已有RRF | 纠正 | weighted_sum就是RRF | pinned源码只证明weighted_sum；本仓owned RRF worked fixture |
| candidate search默认取正文/title/tag | do-not-take | index-side过滤后取正文安全 | pre-Kernel content-free type/static gate；denied content consumer count=0 |
| stale document prune/index available flag授权 | 禁止 | index存在性或cleanup状态可裁决访问 | current Article policy/Revision/epoch由Kernel+PG authority核验 |
| child授权可继承parent/TOC/neighbor | 禁止 | 文档内部引用天然同授权atom/current revision | same Article/current Revision lineage验证；cross Article逐项重授权 |
| rerank/fusion阈值可在request里配 | 禁止 | 质量参数无安全影响 | server-owned versioned profile；授权后admitted ranks压缩；ReleaseManifest激活 |
| `chunk_builder`可作为通用compiler framework | do-not-take | parser registry+TaskContext等于typed deterministic seam | owned profile map + pure runner + typed result；DB write不在parser |
| outline best-effort side write | 禁止 | outline失败不影响Revision语义 | outline进入同compilation digest或closed warning/refusal；一次原子publication |
| arbitrary RecordingContext差异少即等价 | do-not-take | 被记录字段集合代表完整behavior | canonical seam artifacts + mutation-complete comparator + missing/extra/order/digest veto |
| 回放生产write返回值可证明新实现正确 | 禁止 | 相同返回值意味着相同持久化/安全效果 | ephemeral intent sink；fresh real-PG durable-state comparison |
| parity PASS可批准发布 | 禁止 | 行为相同就自动安全/合规/质量合格 | parity、license、Security、Reliability、Quality、Budget独立门；promote owner唯一 |
| Redis message携带tenant_id可构造worker context | 禁止 | queue producer与消息完整性就是tenant authority | DB-owned exact job + signed WorkerLease + ServiceActor；message只含opaque ref |
| unacked reclaim/heartbeat等于lease | 禁止 | liveness或consumer group ownership授予业务写权 | DB generation+nonce+expiry每次write重验证；heartbeat绝不续权 |
| 最后ack即可消除DB/queue crash window | 禁止 | ack与业务commit天然exactly once | durable idempotent terminal receipt先于ack；重放effect≤1 |
| cancel后best-effort删index即可撤权 | 禁止 | cleanup决定可见性/撤权 | active pointer/tombstone+Policy Epoch；cleanup异步且非authority |
| `make smoke`或上游单测数量证明能力 | 禁止 | process可启动等于安全/质量闭合 | `make test/catalog/integration/security-gate`分层；实际PDF/OCR corpus仍[未取证] |

## 5. 推荐实现顺序 + 给 coordinator 的开放问题

### 推荐顺序

1. **先做检索与parity的低风险证据增量（3–5天）。** 给现有RRF/authorized weight compaction补RAGFlow weighted-sum对照fixture和cut-line catalog；明确报告“RRF不是来自固定`search.py`”。这不引入第三方代码或模型。
2. **落通用Adapter parity gate（6–9天）。** 先覆盖Markdown v3、SupplyChangePage、Candidate fusion；以后DOCX/PDF候选都复用同一canonical digest/mutation gate。
3. **决策format-neutral ParsedDocument/provenance ADR（4–6天）。** 未回答PDF bbox/DOCX OOXML locator、figure kind与model identity之前，不写parser产品代码。
4. **DOCX+PDF outline copy+patch（6–9天）。** 完成专属third-party registration、依赖license文本、patch、SBOM、determinism与typed fixtures；仍不激活PDF OCR。
5. **加固WorkerLease runner lifecycle（5–12天）。** 在DOCX runner进入production publication前补fault matrix、stage receipts、preempt/reclaim与terminal idempotency。
6. **PDF/OCR source-only spike（8–12天，可与模型法务审计并行）。** 只在`/tmp`验证去网络、确定性、typed mapping和质量；不得复制到工作树。模型/source-lineage任一门失败即停。
7. **满足明确质量需求后才产品化PDF/OCR（15–22天）。** 先选一个固定runtime target和最小profile（PDF text+outline，或scanned PDF OCR+layout+table），不要同时承诺Ascend、vision LLM、所有layout domains。
8. **最后做authorized rerank/TOC/parent expansion（11–18天）。** 以golden slice ablation决定是否激活；所有内容从AuthorizedProjection开始，cross-Article reauth由integration/security gate证明。

### 给 coordinator 的开放问题

1. **ParsedDocument family：** 是否授权写新ADR，将当前Markdown-specific `ParsedDocument/SectionKind/SourceSpan`提升为format-neutral contract？建议答案是“共享publication interface，保留各format locator/profile的nominal subtype”，避免PDF bbox伪装byte span。
2. **首个非Markdown格式：** 先做DOCX（低资产风险、6–9天）还是PDF text+outline（用户价值可能更高但会触发PDF大文件和provenance设计）？建议先DOCX，再PDF text-only，OCR另立准入。
3. **Figure语义：** figure+caption/OCR是否是V1 structural Fragment kind，还是首版明确refuse/omit整份文档？不能静默丢图；建议新增`FIGURE`并使image bytes走separate bounded artifact/projection policy。
4. **模型分发策略：** ContextEngine制品是否物理携带ONNX bundle，还是由独立受控安装步骤取得并校验？无论哪种都要固定revision/hash/license manifest；建议首版离线安装到只读bundle，runner不下载。
5. **准入runtime target：** PDF/OCR是否只支持一个CPU/ONNX Runtime/architecture组合以取得determinism，还是必须跨macOS/Linux/arm64/x86_64？后者会显著增加量化与golden tolerance工作。
6. **复刻批准记录：** DOCX/outline与PDF/OCR应各自使用哪个ContextEngine issue作为`UPSTREAM.toml.approval`？现有#124仅批准Markdown parser，不应被复用为新区域批准。
7. **检索范围：** coordinator是否只需要补现有RRF的provenance/parity，还是要排期server-owned authorized score blend/reranker？建议前者立即做，后两者等待golden corpus ablation。
8. **parity gate的替换对象：** 第一批应比较哪一对实现？建议先比较frozen Markdown v2/v3 canonical fixtures与现有candidate fusion，而不是尚未获准的RAGFlow PDF模型。

在这些问题得到决定前，可安全启动的只有顺序1–2；顺序3需要架构授权，顺序4需要新的第三方批准，顺序6–7还受模型许可证与动态质量证据双重否决。
