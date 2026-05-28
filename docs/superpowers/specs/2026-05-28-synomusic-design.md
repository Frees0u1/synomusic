# SynoMusic Design Spec

**Date:** 2026-05-28  
**Goal:** 交互式脚本，从网易云音乐抓取热门歌单，在 Navidrome 上创建对应播放列表，并输出匹配报告。

---

## Architecture

```
synomusic/
├── docker-compose.yml          # 启动 NeteaseCloudMusicApi
├── .env.example                # 配置模板
├── .env                        # 用户本地配置（gitignore）
├── requirements.txt
├── main.py                     # 入口，主菜单
└── src/
    ├── netease.py              # 封装 NeteaseCloudMusicApi 调用
    ├── navidrome.py            # 封装 Subsonic API 调用
    ├── matcher.py              # 分级匹配逻辑
    ├── sync.py                 # 同步主流程
    ├── report.py               # 生成同步报告
    └── history.py              # 本地同步历史记录（JSON）
```

**Data flow:**
```
用户选择歌单
    → netease.py 拉取歌单歌曲列表
    → matcher.py 分级匹配（严格 → 模糊）
    → navidrome.py 创建 Subsonic 播放列表
    → report.py 输出报告（总数/命中数/缺失列表）
    → history.py 写入本地记录
```

---

## Configuration (.env)

```bash
# NeteaseCloudMusicApi
NETEASE_API_URL=http://localhost:3000

# Navidrome / Subsonic
NAVIDROME_URL=http://localhost:4533
NAVIDROME_USER=admin
NAVIDROME_PASSWORD=yourpassword

# 同步行为
FUZZY_MATCH_THRESHOLD=80        # 0-100，模糊匹配最低得分
TOP_PLAYLIST_LIMIT=20           # 每次拉取热门歌单数量
HISTORY_FILE=./data/history.json
```

NeteaseCloudMusicApi 通过 `docker-compose.yml` 启动，监听 `NETEASE_API_URL` 对应端口。

---

## Matching Logic (matcher.py)

每首网易云歌曲按以下顺序匹配 Navidrome 曲库：

**预处理（所有级别共用）：**
- 去除首尾空格
- 全角字符转半角
- 统一小写
- 标准化 `feat.` / `ft.` / `featuring` 格式
- 去除括号内的版本说明（Live / Remix / Remastered 等）

**第一级：严格匹配**
- 歌名完全一致 AND 艺术家名完全一致（标准化后）

**第二级：模糊匹配**
- 歌名 `rapidfuzz` 相似度 ≥ `FUZZY_MATCH_THRESHOLD`
- 艺术家名严格一致，或艺术家名相似度 ≥ 85（后者标注为低置信度）

**未匹配：** 两级都未命中，不加入播放列表，仅出现在报告中。

**匹配结果分类：**

| 状态 | 符号 | 说明 |
|------|------|------|
| 严格匹配 | ✅ | 完全命中 |
| 模糊匹配 | 🔶 | 相似度命中，报告标注得分 |
| 未匹配 | ❌ | 曲库中不存在 |

---

## Interactive UI

**主菜单**（`rich` Panel + 数字选择）：
```
╭─────────────────────────────╮
│      SynoMusic              │
│  Netease → Navidrome 同步   │
╰─────────────────────────────╯
  1. 浏览热门歌单并同步
  2. 通过歌单 ID / URL 同步
  3. 查看历史同步记录
  4. 退出
```

**同步流程：**
1. 显示热门歌单表格（排名、歌单名、歌曲数、播放量）
2. 用户选择歌单编号
3. 进度条：拉取歌曲 → 匹配中 → 创建播放列表
4. 确认提示：`共找到 X 首，曲库命中 Y 首，确认创建播放列表？[y/N]`
5. 创建后展示报告

**同步报告：**
```
╭── 同步报告：精选华语 Top 100 ──────────────────╮
│ 歌单共 100 首                                   │
│ ✅ 严格匹配   72 首                             │
│ 🔶 模糊匹配    8 首                             │
│ ❌ 未匹配     20 首                             │
│ 播放列表已创建：精选华语 Top 100                │
╰─────────────────────────────────────────────────╯

缺失歌曲（20首）：
  · 周杰伦 - 稻香
  · 林俊杰 - 江南
  ...
报告已保存：./data/reports/2026-05-28_精选华语Top100.txt
```

---

## History (history.py)

本地 JSON 文件，每次同步追加一条记录：

```json
{
  "synced_at": "2026-05-28T14:00:00",
  "playlist_name": "精选华语 Top 100",
  "netease_id": "123456",
  "total": 100,
  "matched_strict": 72,
  "matched_fuzzy": 8,
  "unmatched": 20,
  "navidrome_playlist_id": "abc123"
}
```

历史记录页（菜单选项 3）以表格展示，可按日期排序。

---

## Dependencies

- **Python:** `rich`, `requests`, `rapidfuzz`, `python-dotenv`
- **Docker:** `NeteaseCloudMusicApi` (binaryify/NeteaseCloudMusicApi)
- **Navidrome:** 已在本机运行，通过 Subsonic API 操作

---

## Out of Scope

- 音频文件下载
- Navidrome 数据库直接操作
- 多用户支持
- Web UI
