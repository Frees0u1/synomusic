# Pagination & Sync Preview Design Spec

**Date:** 2026-05-28
**Goal:** 两项增强：热门歌单支持翻页浏览；同步前可分级预览匹配情况再决定是否创建播放列表。

---

## Feature 1: 热门歌单翻页

### API 层 (`src/netease.py`)

新增返回类型：

```python
@dataclass
class PlaylistPage:
    playlists: list[NeteasePlaylist]
    has_more: bool
    next_before: Optional[int]   # 末尾歌单的 updateTime，翻下一页时传入
```

`get_top_playlists` 签名改为：

```python
def get_top_playlists(self, limit: int = 20, before: Optional[int] = None) -> PlaylistPage:
```

- `before` 传给 API 的 `before` 参数（上一页末尾歌单的 `updateTime` 时间戳）
- 从响应提取 `data["more"]` 作为 `has_more`
- `next_before` = 本页最后一个歌单的 `updateTime`（`p["updateTime"]`）

### 交互层 (`main.py`)

`menu_top_playlists` 内部状态：

```python
pages: list[list[NeteasePlaylist]] = []   # 已加载页缓存
current_page: int = 0
has_more: bool = True
next_before: Optional[int] = None
```

**每页底部提示：**
```
[n] 下一页  [p] 上一页  [编号] 选择歌单  [回车] 返回
第 1 页，共已加载 N 页
```

**翻页逻辑：**
- `n`：若 `current_page < len(pages)-1` → 直接从缓存取；否则请求新一页追加缓存（若 `has_more=False` 提示"已是最后一页"）
- `p`：若 `current_page > 0` → 直接回退缓存，无需重新请求；否则提示"已是第一页"
- 编号：从当前页取对应歌单，进入同步流程
- 回车：返回主菜单

---

## Feature 2: 同步预览

### 报告层 (`src/report.py`)

将现有 `print_report` 内的两个展示块提取为独立函数：

```python
def print_fuzzy_details(fuzzy_matches: list[tuple[str, str, MatchResult]], console: Console) -> None:
    """打印模糊匹配详情表（rich Table）"""

def print_unmatched_list(unmatched: list[tuple[str, str]], console: Console) -> None:
    """打印缺失歌曲列表"""
```

`print_report` 保持不变，内部调用这两个函数。

### 同步层 (`src/sync.py`)

在 `_run_sync_inner` 中，匹配完成后、创建播放列表前，插入预览交互：

**流程：**
```
匹配完成
    → 打印汇总（总数/严格/模糊/缺失）
    → 循环展示预览菜单：
        "要查看详情吗？"
        1. 查看缺失歌曲（N 首）
        2. 查看模糊匹配（N 首）
        3. 查看全部
        [回车] 跳过，继续
    → 用户可多次查看，直到按回车
    → 询问"确认创建播放列表？[y/N]"
    → 继续或取消
```

**实现细节：**
- 预览菜单是一个 `while True` 循环，输入 `1/2/3` 展示对应详情并继续循环，回车则 `break`
- 调用 `print_fuzzy_details` 和 `print_unmatched_list`（从 `report.py`）
- 若某类数量为 0，选项仍显示但选后提示"无此类数据"（不隐藏选项，避免编号跳变）

---

## Files Changed

| File | Change |
|------|--------|
| `src/netease.py` | 新增 `PlaylistPage` dataclass；`get_top_playlists` 接受 `before` 参数，返回 `PlaylistPage` |
| `src/report.py` | 提取 `print_fuzzy_details` 和 `print_unmatched_list`；`print_report` 调用它们 |
| `src/sync.py` | `_run_sync_inner` 匹配后插入预览交互循环 |
| `main.py` | `menu_top_playlists` 改为分页状态循环 |

---

## Out of Scope

- 翻页历史持久化（关闭脚本后不保留页码）
- 预览结果保存到文件（预览只打印到终端）
- 通过歌单 ID/URL 同步时的翻页（无适用场景）
