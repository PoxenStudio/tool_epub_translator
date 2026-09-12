# MyBooks书库 EPUB 翻译工具

MyBooks Toolbox 外部工具：用 LLM 把 EPUB 电子书翻译成目标语言。

[MyBooks项目地址](https://github.com/poxenstudio/mybooks)

## 功能简介

- 页面内配置 OpenAI-compatible LLM：API URL、API Key、模型、token encoding、目标语言、
  翻译模式（替换原文 / 追加纯文本 / 追加分段展示）、可选提示词；配置保存在工具自己的
  `config.json` 里，API Key 只写入不回显。
- 两种输入来源：从 MyBooks 书库搜索并选择带 EPUB 格式的书籍，或上传本地 `.epub` 文件。
- 同一时间只处理一个翻译任务；页面刷新或重新打开后仍能看到正在运行的任务、阶段和进度，
  以及实时的输入 / 输出 / 总 token 用量（服务端不返回用量时明确提示"未返回"，不会显示假的
  0）。
- 两种结果交付方式（二选一，不覆盖原书）：下载翻译后的 EPUB，或直接在书库里生成一本新书
  （标题默认标注目标语言，如「原书名 [Simplified Chinese]」）；生成新书完成后在页面下方
  显示新书名称和可点击的书籍链接。
- 上传的源文件、下载完成或过期未下载的翻译结果都会被清理，不在服务器上无限堆积临时文件。

首版不做：批量/队列翻译、任务取消后恢复、自动估算费用、把 API Key 同步到 MyBooks 全局
系统设置。

## 依赖

- MyBooks（`mybooks/mybooks`）Core API `>= 1.0.0`，需要 `CoreAPI.calibre` /
  `CoreAPI.storage` / `CoreAPI.tasks` / `CoreAPI.messages` / `CoreAPI.db.create_item`。
- MyBooks 运行环境的 Python 解释器（供 Calibre/Tornado 后端代码使用，随 MyBooks 部署提供，
  不需要工具自己打包）。
- 翻译库 [`epub-translator`](https://pypi.org/project/epub-translator/)（当前锁定的
  Python 版本约束为 `>=3.11,<3.14`），以及它依赖的 `tiktoken`、`jinja2`、
  `resource-segmentation`、`openai`、`mathml2latex`。这些不会被打进工具安装包，必须由
  MyBooks 所在机器的 **base conda 环境**预先安装好，运行时按普通 `import` 方式引入。
- 一个 OpenAI-compatible 的 LLM API（URL + API Key + 模型名）。

## 安装前检查

在 MyBooks 部署机器上确认依赖已装好：

```bash
conda activate base
python -c "import epub_translator; print(epub_translator.__file__)"
python -c "import openai, tiktoken, jinja2, resource_segmentation, mathml2latex; print('deps ok')"
```

任一条导入失败都说明 base conda 环境缺依赖，需要先 `pip install epub-translator` 补齐，
否则翻译任务会在启动时立即失败并提示"翻译依赖未就绪"。

## 开发校验和构建

```bash
cd /Volumes/data/projects/poxenstudio/tool_translation
mytool validate .      # 只校验，不打包
mytool build .          # 打包并打印 dist/epub_translator-<revision>.zip 的 sha256
```

不想全局装 `mytool` 也可以用仓库自带的构建脚本，效果等价（内部固定走
`mytool validate . && mytool build .`，装不到全局 `mytool` 时自动回退到
`npx mybooks-tools-builder`）：

```bash
./scripts/build.sh
```

## 安装

1. 在开启了"开发者模式"（管理员在系统设置里打开 `ENABLE_TOOLBOX_DEV_MODE`）的 MyBooks
   实例的 `/admin/toolbox` 页面上传 `dist/epub_translator-<revision>.zip`
   （对应 `POST /api/toolbox/install/upload`）。
2. 安装后**必须重启 MyBooks** 才会真正生效；之后管理员可以随时禁用/启用（立即生效，不需要
   重启）或卸载。
3. 安装完成后，先按上面"安装前检查"确认 base conda 环境依赖齐全，再进入工具页面填写 LLM
   配置并做一次真实翻译验证——静态打开 `frontend/index.html` 无法验证，必须在装了工具的
   真实 MyBooks 实例里跑。

## 配置与数据

- LLM 配置（含 API Key）保存在 MyBooks 服务器的工具专属数据目录下（
  `<TOOL_DATA_ROOT>/epub_translator/config.json`），不提交到 Git，也不会同步到 MyBooks
  全局系统设置。
- 任务状态（`job.json`）和翻译临时文件（`jobs/<job_id>/`）保存在同一数据目录下，工具自己
  负责原子写入和清理，不依赖 MyBooks 后台任务面板的内存状态（那份状态进程重启即丢）。

## 项目结构

```
tool_translation/
  manifest.json            # 工具元数据 + api_routes 声明
  backend/
    tool.py                 # EpubTranslator（BaseTool 子类）+ 5 个 HTTP handler，只做编排
    config.py                # 配置字段定义、校验、对前端的展示形态
    connection.py             # "测试连接"：发一个最小请求验证 URL/Key/Model
    translation.py            # 真正跑一次翻译：起 LLM、进度回调、生成新书、失败处理
    job_store.py               # job.json 的原子读写、单任务锁、过期清理
  frontend/
    index.html               # 页面结构（配置 / 选书 / 输出方式 / 进度 / 结果）
    style.css                 # 页面自己的样式（组件样式在 lib/theme.css）
    js/
      state.js                 # 全局运行时（bridge/i18n/state）+ $、alert 工具，最先加载
      config.js                 # LLM 配置面板：加载/保存配置、测试连接
      source.js                 # 选书：搜索书库、渲染列表、记录当前选中项
      task.js                   # 启动翻译、任务状态轮询与渲染、结果展示
      main.js                    # 启动入口：等 i18n 就绪后拉一次配置/状态
    lib/                      # 脚手架自带的 theme.css + i18n.js
    locales/{en,zh}.json      # 界面文案
  scripts/build.sh
```
