# markitdown-read

一个面向 Codex 的全局 skill，用于在读取本地文档时，以 MarkItDown 作为受控的文本提取工具，同时保留结构化解析和视觉检查，避免把一次转换误认为完整读取。

## 功能

- PDF、Word、PowerPoint、Excel、HTML、图片、音频等适合文本提取的文件，可先转换为 Markdown。
- 源代码、纯文本、Markdown、日志、配置、CSV、JSON、XML 默认直接读取，避免不必要的转换和结构损失。
- 扫描件、复杂表格、图表、公式、印章和版式敏感内容要求文本路径与视觉路径同时检查。
- 大型目录先盘点，再按问题筛选并分批处理，不会递归转换全部文件。
- ZIP 先检查目录、路径穿越、重复条目、嵌套归档和资源上限，不会自动解压或递归处理所有内容。
- 原始文件只读，不生成持久化 Markdown 副本或目录索引。
- 将文件内容视为不可信数据，不执行文档中的命令或提示。

## 资源限制

默认限制如下：

| 项目 | 默认值 |
| --- | ---: |
| 目录递归深度 | 8 |
| 普通文件数量 | 2,000 |
| 总源文件大小 | 1 GiB |
| 单文件大小 | 256 MiB |
| 单批处理文件数 | 25 |
| ZIP 条目数 | 1,000 |
| ZIP 解压后大小 | 512 MiB |
| ZIP 单条目大小 | 128 MiB |
| ZIP 嵌套深度 | 2 |

超过限制时应停止并报告范围，不应静默抽样后声称已经完整分析。

## 完整性状态

每个文件或处理批次应标记为以下状态之一：

- `complete`：文本和视觉路径均完成，且没有发现明显遗漏。
- `partial`：存在未检查、失败或可能遗漏的内容。
- `failed`：密码、损坏、权限、格式、依赖或资源限制导致无法读取。
- `unverified`：能够读取，但无法证明结构或视觉信息完整。

完整性请求中，任一文本路径或视觉路径缺失，都只能报告为 `partial`。

## 检查脚本

`scripts/inspect_corpus.py` 是无第三方依赖的只读检查脚本。它只向标准输出 JSON，不写入源目录，不解压文件，也不跟随符号链接、junction 或 reparse point。

```bash
python scripts/inspect_corpus.py /path/to/file-or-directory
python scripts/inspect_corpus.py --runtime-info
```

`--runtime-info` 会报告 Python、MarkItDown 版本、CLI 是否可发现，以及可选 OCR、音频、YouTube 和 Azure 后端是否已安装。

检查脚本需要 Python 3.10 或更高版本。资源限制表中的批处理数量和上下文占比是 Codex 工作流约束，不是检查脚本对 MarkItDown 转换过程的硬性 token 限制。

## 安装

本仓库根目录就是 `markitdown-read` skill 目录，使用 Codex skill 安装器时指定仓库根路径：

```bash
python install-skill-from-github.py \
  --repo lwq232/markitdown-read \
  --path .
```

MarkItDown 及其可选依赖由使用环境单独安装。不要把 Python 运行时、`site-packages`、缓存、临时文件或用户文档提交到仓库。

## 隐私和外部服务

源文件不会被修改、移动或删除。转换结果优先保存在内存中；必须使用临时文件时，应使用任务专属临时目录并在任务结束后清理。

OCR、音频转写、Azure、YouTube 或其他网络服务可能需要额外依赖、凭据和网络访问。未经用户明确授权，不应上传本地文件到外部服务。

## 目录结构

```text
markitdown-read/            # GitHub 仓库根目录
├── LICENSE
├── README.md
├── SKILL.md
├── .gitignore
└── scripts/
    └── inspect_corpus.py
```

## 许可证

本 skill 使用 MIT License，详见 [LICENSE](LICENSE)。

MarkItDown 及其可选依赖属于各自的上游项目，继续遵循它们各自的许可证。
