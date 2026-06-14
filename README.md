# SynoMusic

将网易云音乐歌单同步到 [Navidrome](https://www.navidrome.org/) 的交互式命令行工具。

默认流程只做曲库匹配；可选的 downloader 包可以按网易云歌曲信息检索种子，并提交到 Synology Download Station 下载。

---

## 功能

- 浏览**编辑精选歌单**（高质量精品歌单）或**热门歌单**（支持华语/流行/摇滚等 13 个分类筛选）
- 翻页浏览，已加载页本地缓存，前翻无需重新请求
- 通过歌单 ID 或 URL 直接同步
- 两级匹配：严格匹配（标题+艺术家精确）→ 模糊匹配（rapidfuzz 综合得分）
- 同步前预览：统计面板 + 完整歌曲列表（严格/模糊/未匹配分色标注），模糊匹配展示 Navidrome 候选文件路径
- 歌单有缺失歌曲时，可批量检索 DIC Music / Pirate Bay，并把最高匹配候选提交到 Synology Download Station
- 在 Navidrome 中创建 Subsonic 播放列表
- 本地 JSON 同步历史记录
- 每次同步生成文本报告，保存至 `data/reports/`
- 可选：通过歌曲 ID 或歌曲名检索 DIC Music / Pirate Bay 候选，确认后提交 Synology Download Station

---

## 依赖

- Python 3.11+
- Docker（用于运行 NeteaseCloudMusicApi）
- 已配置好的 Navidrome 实例（需 Subsonic API 访问权限）
- 可选：Synology Download Station（菜单 6 下载单曲时需要）

---

## 快速开始

### 1. 启动 NeteaseCloudMusicApi

```bash
docker compose up -d
```

默认监听宿主机 `http://localhost:3300`，容器内仍是 `3000`。这里避开了本机常见的 `3000` 端口冲突。

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```env
NETEASE_API_URL=http://localhost:3300
NAVIDROME_URL=http://your-navidrome-host:4533
NAVIDROME_USER=admin
NAVIDROME_PASSWORD=yourpassword

FUZZY_MATCH_THRESHOLD=80   # 模糊匹配最低分（0-100），建议 75-85
TOP_PLAYLIST_LIMIT=20      # 每页歌单数量
HISTORY_FILE=./data/history.json

# 可选：种子下载器
SYNOLOGY_URL=http://your-synology-host:5000
SYNOLOGY_USER=admin
SYNOLOGY_PASSWORD=yourpassword
SYNOLOGY_DOWNLOAD_DESTINATION=music/lib

DICMUSIC_BASE_URL=https://dicmusic.com
DICMUSIC_USER=
DICMUSIC_PASSWORD=
# 也可改用浏览器 Cookie；配置后优先于用户名密码
DICMUSIC_COOKIE=

PIRATEBAY_API_URL=https://apibay.org
PIRATEBAY_TIMEOUT_SECONDS=8
PIRATEBAY_RETRIES=2
PIRATEBAY_QUERY_DELAY_SECONDS=1.0
PIRATEBAY_RATE_LIMIT_COOLDOWN_SECONDS=180

# 可选：MusicBrainz 元数据别名解析。默认开启；仅在中文歌曲且 DIC Music 直搜无候选时兜底使用。
MUSICBRAINZ_ENABLED=true
MUSICBRAINZ_BASE_URL=https://musicbrainz.org/ws/2
MUSICBRAINZ_USER_AGENT="SynoMusic/0.1 (your-email@example.com)"
MUSICBRAINZ_MIN_SCORE=85
MUSICBRAINZ_RATE_LIMIT_SECONDS=1.05

# 可选：手工兜底别名。多个别名用 |，多条规则用 ;
# DOWNLOADER_QUERY_ALIASES=周杰伦=Jay Chou;魔杰座=Capricorn
DOWNLOADER_AUTO_THRESHOLD=88
DOWNLOADER_MIN_THRESHOLD=70
DOWNLOADER_POLL_INTERVAL=15
# DOWNLOADER_TIMEOUT_SECONDS=7200
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
5. 清除本地缓存
6. 通过歌曲 ID / URL 或歌名下载缺失单曲 — 检索种子，选择候选并确认后提交 Synology Download Station
7. 退出
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

确认后创建播放列表；确认提示支持 `Y/N`、`yes/no` 大小写输入，直接回车按默认同意处理。

如果预览中存在未匹配歌曲，会先询问是否批量下载缺失歌曲，提示为 `[Y/n]`，直接回车默认同意。选择下载后，工具会逐首搜索 DIC Music 和 Pirate Bay，按最高匹配度选择候选，低于 `DOWNLOADER_MIN_THRESHOLD` 的候选会跳过；提交完 Synology Download Station 任务后会打印 ASCII 统计框，包含歌单总数、音乐库总数、严格/模糊/缺失匹配、添加下载任务数、预计下载大小、跳过数和失败数，并列出跳过歌曲与失败原因。随后停止本次导入。等待下载完成并让 Navidrome 扫描新文件后，重新导入同一个网易云歌单即可把补齐歌曲加入播放列表。

---

## 下载器

downloader 包位于 `src/downloader/`，核心输入是 `NeteaseTrack` 或 `TrackInfo`：

1. `DICMusicProvider` 使用 `login.php` 表单登录，搜索 `torrents.php?searchstr=...`。没有配置 DIC 账号或 Cookie 时会自动跳过。
2. 默认先用原始标题、艺人、专辑直搜；英文歌曲不会解析 MusicBrainz 别名。搜索会自动补充常见标点变体，例如直撇号和弯撇号。
3. 手工配置的 `DOWNLOADER_QUERY_ALIASES` 会直接生效；只有中文歌曲且 DIC Music 直搜无候选时，`MusicBrainzResolver` 才补充艺人/专辑/歌曲别名，例如 `周杰伦 → Jay Chou`、`魔杰座 → Capricorn`，并用别名补搜 DIC Music 和 Pirate Bay。
4. `PirateBayProvider` 使用 `apibay.org/q.php` JSON 搜索，过滤音频音乐分类并生成 magnet。`apibay.org` 可能返回 HTTP 429 限流；客户端默认每条 query 间隔 `PIRATEBAY_QUERY_DELAY_SECONDS=1.0` 秒，遇到 429 后按 `Retry-After` 或 `PIRATEBAY_RATE_LIMIT_COOLDOWN_SECONDS=180` 秒冷却，冷却期间跳过 Pirate Bay。
5. `TorrentSelector` 按标题、艺术家、专辑、质量关键词和 seeders 评分；自动流程可按阈值选择，人工入口会始终展示候选并要求用户确认。
6. `DownloadStationClient` 使用 Synology WebAPI 登录 Download Station，调用 `SYNO.DownloadStation.Task.create` 提交种子 URL/magnet，目标目录默认 `music/lib`。Download Station API 期望的是从共享文件夹开始的路径；如果配置成 `/volume1/music/lib` 或 `/Volumes/music/lib`，客户端会自动转成 `music/lib`。
7. 提交后后台轮询 `SYNO.DownloadStation.Task.getinfo/list`，完成后在 CLI 提示。

歌单同步中的批量下载不会等待任务完成，只负责添加 Download Station 任务并输出统计；下载完成后需要重新运行歌单导入。

无候选、Provider 检索失败、候选低于阈值都会写 warning 日志并在交互界面提示。

只读测试 Provider：

```bash
.venv/bin/python -m src.downloader.diagnostics --title "The Bends" --artist "Radiohead" --album "The Bends"
```

这只会登录和检索，不会连接 Synology，也不会创建下载任务。中文曲目在 Pirate Bay 上经常没有公开结果；实际下载流程只会在中文歌曲且 DIC Music 直搜无候选时使用 MusicBrainz 自动别名。手工 `DOWNLOADER_QUERY_ALIASES` 始终生效，可用于艺人更名等确定性场景，例如 `周杰伦=Jay Chou;魔杰座=Capricorn;The Chicks=Dixie Chicks|The Dixie Chicks`。

按歌曲名搜索并人工确认提交到 Synology：

```bash
.venv/bin/python -m src.downloader.search "南山南" --artist "马頔" --album "孤岛"
```

也可以不带参数进入提示式输入：

```bash
.venv/bin/python -m src.downloader.search
```

命令会分别检索 DIC Music 和 Pirate Bay，合并展示候选。输入候选编号后，还会再次确认目标目录；确认后直接把 DIC 的下载链接（包含 `authkey` / `torrent_pass`）或 Pirate Bay magnet 提交给 Download Station，不会先下载 `.torrent` 文件。默认只提交任务；需要在本地持续轮询完成状态时加 `--wait`。

---

## 项目结构

```
├── main.py              # 入口，菜单交互
├── src/
│   ├── config.py        # 环境变量加载与校验
│   ├── netease.py       # NeteaseCloudMusicApi 客户端
│   ├── navidrome.py     # Navidrome Subsonic API 客户端
│   ├── downloader/      # 种子检索、选择、Synology 下载编排
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
