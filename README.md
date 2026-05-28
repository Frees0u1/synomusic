# SynoMusic

将网易云音乐歌单同步到 [Navidrome](https://www.navidrome.org/) 的交互式命令行工具。

只做匹配，不下载音乐。前提是你的 Navidrome 曲库里已经有对应的音频文件。

---

## 功能

- 浏览**编辑精选歌单**（高质量精品歌单）或**热门歌单**（支持华语/流行/摇滚等 13 个分类筛选）
- 翻页浏览，已加载页本地缓存，前翻无需重新请求
- 通过歌单 ID 或 URL 直接同步
- 两级匹配：严格匹配（标题+艺术家精确）→ 模糊匹配（rapidfuzz 综合得分）
- 同步前预览：统计面板 + 完整歌曲列表（严格/模糊/未匹配分色标注），模糊匹配展示 Navidrome 候选文件路径
- 在 Navidrome 中创建 Subsonic 播放列表
- 本地 JSON 同步历史记录
- 每次同步生成文本报告，保存至 `data/reports/`

---

## 依赖

- Python 3.11+
- Docker（用于运行 NeteaseCloudMusicApi）
- 已配置好的 Navidrome 实例（需 Subsonic API 访问权限）

---

## 快速开始

### 1. 启动 NeteaseCloudMusicApi

```bash
docker compose up -d
```

默认监听 `http://localhost:3000`。

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```env
NETEASE_API_URL=http://localhost:3000
NAVIDROME_URL=http://your-navidrome-host:4533
NAVIDROME_USER=admin
NAVIDROME_PASSWORD=yourpassword

FUZZY_MATCH_THRESHOLD=80   # 模糊匹配最低分（0-100），建议 75-85
TOP_PLAYLIST_LIMIT=20      # 每页歌单数量
HISTORY_FILE=./data/history.json
```

### 3. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. 运行

```bash
python main.py
```

---

## 菜单说明

```
1. 浏览编辑精选歌单     — 网易云编辑精选高质量歌单，按 updateTime cursor 翻页
2. 浏览热门歌单（按分类） — 播放量排行歌单，进入前选择分类（全部/华语/流行/摇滚…）
3. 通过歌单 ID / URL 同步 — 粘贴歌单链接或输入纯数字 ID
4. 查看历史同步记录
5. 退出
```

翻页操作：`n` 下一页 / `p` 上一页 / 输入编号选择歌单 / 回车返回。

---

## 匹配逻辑

1. **严格匹配**：标题和艺术家规范化后完全一致，得分 100。
2. **模糊匹配**：标题 rapidfuzz ratio ≥ 阈值，综合得分 = 标题分 × 0.7 + 艺术家分 × 0.3。
   - 标题得分 ≥ 95 时，艺术家相似度不足也会命中（标记为低置信度），避免因繁简差异或曲库文件名带版本后缀而漏匹配。
3. **未匹配**：两级均未命中。

规范化处理包括：NFKC Unicode 标准化、全角转半角、小写、`ft./featuring` 统一、去除括号内版本标签（Live/Remix/Remaster…）、去除 ` - live` 等裸后缀。

---

## 预览界面

同步匹配完成后自动展示：

**第一节**：统计面板，显示严格/模糊/未匹配数量。

**第二节**：完整歌曲列表，每行一首：
- 绿色：严格匹配
- 黄色：模糊匹配，展示综合得分、置信度，以及 Navidrome 中 top-3 候选的曲名、歌手、文件路径
- 红色：未匹配

确认后创建播放列表，输入 `n` 取消。

---

## 项目结构

```
├── main.py              # 入口，菜单交互
├── src/
│   ├── config.py        # 环境变量加载与校验
│   ├── netease.py       # NeteaseCloudMusicApi 客户端
│   ├── navidrome.py     # Navidrome Subsonic API 客户端
│   ├── matcher.py       # 文本规范化与两级匹配
│   ├── report.py        # 预览渲染、同步报告生成与保存
│   └── history.py       # 本地 JSON 同步历史
├── tests/               # pytest 单元测试
├── docker-compose.yml   # NeteaseCloudMusicApi 容器
├── .env.example
└── requirements.txt
```

---

## 运行测试

```bash
pytest tests/ -v
```
