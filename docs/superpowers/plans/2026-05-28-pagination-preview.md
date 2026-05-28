# Pagination & Sync Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为热门歌单浏览添加翻页功能（加载更多式，缓存已加载页），并在同步前插入分级预览交互（汇总→按需展开详情→确认）。

**Architecture:** `netease.py` 新增 `PlaylistPage` 返回类型支持翻页；`report.py` 提取两个展示函数供预览复用；`sync.py` 在确认前插入预览菜单循环；`main.py` 改为分页状态循环管理已加载页缓存。

**Tech Stack:** Python 3.11+, rich, rapidfuzz, requests（均已在 requirements.txt）

---

## File Map

| File | Change |
|------|--------|
| `src/netease.py` | 新增 `PlaylistPage` dataclass；`get_top_playlists` 签名改为返回 `PlaylistPage`，接受 `before` 参数 |
| `src/report.py` | 提取 `print_fuzzy_details` 和 `print_unmatched_list`；`print_report` 改为调用它们 |
| `src/sync.py` | `_run_sync_inner` 在匹配后、确认前插入预览菜单循环 |
| `main.py` | `menu_top_playlists` 改为分页缓存循环 |
| `tests/test_netease_pagination.py` | 新增：`PlaylistPage` 和翻页逻辑单元测试 |
| `tests/test_report_helpers.py` | 新增：`print_fuzzy_details` 和 `print_unmatched_list` 单元测试 |

---

## Task 1: `PlaylistPage` dataclass 与 `get_top_playlists` 翻页支持

**Files:**
- Modify: `src/netease.py`
- Create: `tests/test_netease_pagination.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_netease_pagination.py`：

```python
from unittest.mock import patch, MagicMock
from src.netease import NeteaseClient, PlaylistPage, NeteasePlaylist


def _make_api_response(n=2, more=True):
    playlists = [
        {
            "id": i,
            "name": f"歌单{i}",
            "description": "",
            "trackCount": 10,
            "playCount": 1000,
            "coverImgUrl": "",
            "updateTime": 1700000000 + i,
        }
        for i in range(n)
    ]
    return {"code": 200, "playlists": playlists, "more": more}


def test_get_top_playlists_returns_playlist_page():
    client = NeteaseClient("http://localhost:3000")
    with patch.object(client, "_get", return_value=_make_api_response(2, more=True)) as mock_get:
        page = client.get_top_playlists(limit=2)
        mock_get.assert_called_once_with("/top/playlist/highquality", params={"limit": 2})
    assert isinstance(page, PlaylistPage)
    assert len(page.playlists) == 2
    assert page.has_more is True
    assert page.next_before == 1700000001  # updateTime of last playlist


def test_get_top_playlists_with_before_param():
    client = NeteaseClient("http://localhost:3000")
    with patch.object(client, "_get", return_value=_make_api_response(2, more=False)) as mock_get:
        page = client.get_top_playlists(limit=2, before=1700000001)
        mock_get.assert_called_once_with(
            "/top/playlist/highquality", params={"limit": 2, "before": 1700000001}
        )
    assert page.has_more is False


def test_get_top_playlists_empty_page():
    client = NeteaseClient("http://localhost:3000")
    with patch.object(client, "_get", return_value={"code": 200, "playlists": [], "more": False}):
        page = client.get_top_playlists(limit=20)
    assert page.playlists == []
    assert page.has_more is False
    assert page.next_before is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/jinghao.liu/script/synomusic
.venv/bin/pytest tests/test_netease_pagination.py -v
```

预期：`ImportError: cannot import name 'PlaylistPage' from 'src.netease'`

- [ ] **Step 3: 在 `src/netease.py` 新增 `PlaylistPage` 并修改 `get_top_playlists`**

在 `NeteasePlaylist` dataclass 定义之后，添加：

```python
@dataclass
class PlaylistPage:
    playlists: list[NeteasePlaylist]
    has_more: bool
    next_before: Optional[int]
```

将 `get_top_playlists` 替换为：

```python
def get_top_playlists(self, limit: int = 20, before: Optional[int] = None) -> PlaylistPage:
    params: dict = {"limit": limit}
    if before is not None:
        params["before"] = before
    data = self._get("/top/playlist/highquality", params=params)
    playlists = []
    for p in data.get("playlists", []):
        playlists.append(NeteasePlaylist(
            id=str(p["id"]),
            name=p["name"],
            description=p.get("description") or "",
            track_count=p.get("trackCount", 0),
            play_count=p.get("playCount", 0),
            cover_url=p.get("coverImgUrl", ""),
        ))
    has_more = bool(data.get("more", False))
    next_before = playlists[-1] and data.get("playlists", [{}])[-1].get("updateTime") if playlists else None
    return PlaylistPage(playlists=playlists, has_more=has_more, next_before=next_before)
```

**注意**：`next_before` 从原始响应数据中取 `updateTime`，不从 `NeteasePlaylist` dataclass 取（该字段未存储在 dataclass 中）。更清晰的写法：

```python
raw_playlists = data.get("playlists", [])
playlists = []
for p in raw_playlists:
    playlists.append(NeteasePlaylist(
        id=str(p["id"]),
        name=p["name"],
        description=p.get("description") or "",
        track_count=p.get("trackCount", 0),
        play_count=p.get("playCount", 0),
        cover_url=p.get("coverImgUrl", ""),
    ))
has_more = bool(data.get("more", False))
next_before = raw_playlists[-1].get("updateTime") if raw_playlists else None
return PlaylistPage(playlists=playlists, has_more=has_more, next_before=next_before)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest tests/test_netease_pagination.py -v
```

预期：3 个测试全部 PASS

- [ ] **Step 5: 运行全套测试确认无回归**

```bash
.venv/bin/pytest tests/ -v
```

预期：所有测试 PASS（`main.py` 中 `menu_top_playlists` 还未更新，但不影响测试）

- [ ] **Step 6: 提交**

```bash
git add src/netease.py tests/test_netease_pagination.py
git commit -m "feat: add PlaylistPage and pagination support to get_top_playlists [ai:y]"
```

---

## Task 2: 提取 `print_fuzzy_details` 和 `print_unmatched_list`

**Files:**
- Modify: `src/report.py`
- Create: `tests/test_report_helpers.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_report_helpers.py`：

```python
from io import StringIO
from rich.console import Console
from src.matcher import MatchResult, MatchStatus
from src.report import print_fuzzy_details, print_unmatched_list


def _console() -> Console:
    return Console(file=StringIO(), highlight=False, markup=False)


def test_print_fuzzy_details_empty():
    console = _console()
    print_fuzzy_details([], console)
    output = console.file.getvalue()
    assert output == ""


def test_print_fuzzy_details_renders_rows():
    console = _console()
    result = MatchResult(status=MatchStatus.FUZZY, track_id="1", score=85, low_confidence=False)
    print_fuzzy_details([("稻香", "周杰伦", result)], console)
    output = console.file.getvalue()
    assert "稻香" in output
    assert "周杰伦" in output
    assert "85" in output


def test_print_unmatched_list_empty():
    console = _console()
    print_unmatched_list([], console)
    output = console.file.getvalue()
    assert output == ""


def test_print_unmatched_list_renders_entries():
    console = _console()
    print_unmatched_list([("稻香", "周杰伦"), ("江南", "林俊杰")], console)
    output = console.file.getvalue()
    assert "稻香" in output
    assert "江南" in output
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest tests/test_report_helpers.py -v
```

预期：`ImportError: cannot import name 'print_fuzzy_details' from 'src.report'`

- [ ] **Step 3: 修改 `src/report.py`**

在 `print_report` 函数之前，添加两个新函数，并修改 `print_report` 调用它们：

```python
def print_fuzzy_details(fuzzy_matches: list[tuple[str, str, MatchResult]], console: Console) -> None:
    if not fuzzy_matches:
        return
    table = Table(title="模糊匹配详情", box=box.SIMPLE)
    table.add_column("歌曲", style="yellow")
    table.add_column("艺术家", style="yellow")
    table.add_column("得分", justify="right")
    table.add_column("置信度")
    for title, artist, result in fuzzy_matches:
        conf = "[red]低[/red]" if result.low_confidence else "[green]正常[/green]"
        table.add_row(title, artist, str(result.score), conf)
    console.print(table)


def print_unmatched_list(unmatched: list[tuple[str, str]], console: Console) -> None:
    if not unmatched:
        return
    console.print(f"\n[red]缺失歌曲（{len(unmatched)}首）：[/red]")
    for title, artist in unmatched:
        console.print(f"  · {artist} - {title}")


def print_report(summary: SyncSummary, console: Console) -> None:
    panel_lines = [
        f"歌单共 [bold]{summary.total}[/bold] 首",
        f"✅ 严格匹配  [green]{len(summary.strict_matches)}[/green] 首",
        f"🔶 模糊匹配  [yellow]{len(summary.fuzzy_matches)}[/yellow] 首",
        f"❌ 未匹配    [red]{len(summary.unmatched)}[/red] 首",
    ]
    if summary.navidrome_playlist_id:
        panel_lines.append(f"\n播放列表已创建：[bold]{summary.playlist_name}[/bold]")
    else:
        panel_lines.append("\n[yellow]播放列表未创建（用户取消或无匹配）[/yellow]")

    console.print(Panel("\n".join(panel_lines), title=f"同步报告：{summary.playlist_name}", expand=False))
    print_fuzzy_details(summary.fuzzy_matches, console)
    print_unmatched_list(summary.unmatched, console)
```

**注意**：`print_report` 中原来的两个展示块（`if summary.fuzzy_matches:` 和 `if summary.unmatched:`）完全删除，替换为对上面两个函数的调用。

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest tests/test_report_helpers.py tests/test_config.py tests/test_matcher.py -v
```

预期：所有测试 PASS

- [ ] **Step 5: 提交**

```bash
git add src/report.py tests/test_report_helpers.py
git commit -m "feat: extract print_fuzzy_details and print_unmatched_list from print_report [ai:y]"
```

---

## Task 3: 同步预览交互（`src/sync.py`）

**Files:**
- Modify: `src/sync.py`

无新测试（预览交互依赖 `console.input`，属于 UI 层，通过 Task 4 手动验证）。

- [ ] **Step 1: 修改 `src/sync.py`**

在 `src/sync.py` 顶部导入行中加入新的报告函数：

```python
from src.report import SyncSummary, print_report, save_report, print_fuzzy_details, print_unmatched_list
```

在 `_run_sync_inner` 中，找到这一段（约第 71-86 行）：

```python
    console.print()
    total = len(netease_tracks)
    matched = len(matched_ids)
    console.print(
        f"共找到 [bold]{total}[/bold] 首，曲库命中 [bold green]{matched}[/bold green] 首，"
        f"缺失 [bold red]{len(unmatched_list)}[/bold red] 首"
    )

    if not matched_ids:
        console.print("[yellow]曲库中没有匹配的歌曲，取消创建播放列表。[/yellow]")
        return

    confirm = console.input(f"\n确认创建播放列表「{playlist.name}」？[y/N] ").strip().lower()
    if confirm != "y":
        console.print("[yellow]已取消。[/yellow]")
        return
```

替换为：

```python
    console.print()
    total = len(netease_tracks)
    matched = len(matched_ids)
    console.print(
        f"共找到 [bold]{total}[/bold] 首，曲库命中 [bold green]{matched}[/bold green] 首，"
        f"缺失 [bold red]{len(unmatched_list)}[/bold red] 首"
    )

    if not matched_ids:
        console.print("[yellow]曲库中没有匹配的歌曲，取消创建播放列表。[/yellow]")
        return

    # 预览菜单循环
    while True:
        console.print(
            f"\n查看详情？"
            f"  [cyan]1[/cyan] 缺失歌曲（{len(unmatched_list)}首）"
            f"  [cyan]2[/cyan] 模糊匹配（{len(fuzzy_matches)}首）"
            f"  [cyan]3[/cyan] 全部"
            f"  [dim][回车] 跳过[/dim]"
        )
        preview_choice = console.input("").strip()
        if preview_choice == "1":
            if unmatched_list:
                print_unmatched_list(unmatched_list, console)
            else:
                console.print("[dim]无缺失歌曲。[/dim]")
        elif preview_choice == "2":
            if fuzzy_matches:
                print_fuzzy_details(fuzzy_matches, console)
            else:
                console.print("[dim]无模糊匹配。[/dim]")
        elif preview_choice == "3":
            print_fuzzy_details(fuzzy_matches, console)
            print_unmatched_list(unmatched_list, console)
        else:
            break

    confirm = console.input(f"\n确认创建播放列表「{playlist.name}」？[y/N] ").strip().lower()
    if confirm != "y":
        console.print("[yellow]已取消。[/yellow]")
        return
```

- [ ] **Step 2: 运行全套测试确认无回归**

```bash
.venv/bin/pytest tests/ -v
```

预期：所有测试 PASS

- [ ] **Step 3: 提交**

```bash
git add src/sync.py
git commit -m "feat: add tiered preview menu before playlist creation [ai:y]"
```

---

## Task 4: 翻页交互（`main.py`）

**Files:**
- Modify: `main.py`

无新单元测试（翻页状态循环是纯 UI/交互逻辑，通过手动验证）。

- [ ] **Step 1: 修改 `main.py` 的导入行**

在 `from src.netease import NeteaseClient` 这行，改为：

```python
from src.netease import NeteaseClient, PlaylistPage
```

- [ ] **Step 2: 替换 `menu_top_playlists` 函数**

将整个 `menu_top_playlists` 函数替换为：

```python
def menu_top_playlists(netease: NeteaseClient, navi: NavidromeClient, cfg: Config) -> None:
    pages: list[list] = []
    current_page = 0
    has_more = True
    next_before = None

    def _load_next_page() -> bool:
        nonlocal has_more, next_before
        with console.status("正在获取热门歌单..."):
            try:
                page: PlaylistPage = netease.get_top_playlists(
                    cfg.top_playlist_limit, before=next_before
                )
            except Exception as e:
                console.print(f"[red]获取热门歌单失败：{e}[/red]")
                return False
        pages.append(page.playlists)
        has_more = page.has_more
        next_before = page.next_before
        return True

    if not _load_next_page():
        return

    while True:
        playlists = pages[current_page]
        table = Table(box=box.ROUNDED)
        table.add_column("#", justify="right", style="dim")
        table.add_column("歌单名称")
        table.add_column("歌曲数", justify="right")
        table.add_column("播放量", justify="right")
        for i, p in enumerate(playlists, 1):
            table.add_row(str(i), p.name, str(p.track_count), f"{p.play_count:,}")
        console.print(table)

        nav_parts = []
        if has_more or current_page < len(pages) - 1:
            nav_parts.append("[cyan]n[/cyan] 下一页")
        if current_page > 0:
            nav_parts.append("[cyan]p[/cyan] 上一页")
        nav_parts.append("[cyan]编号[/cyan] 选择")
        nav_parts.append("[dim]回车 返回[/dim]")
        console.print("  ".join(nav_parts) + f"  （第 {current_page + 1} 页，共已加载 {len(pages)} 页）")

        choice = console.input("").strip().lower()

        if choice == "":
            return
        elif choice == "n":
            if current_page < len(pages) - 1:
                current_page += 1
            elif has_more:
                if _load_next_page():
                    current_page += 1
            else:
                console.print("[yellow]已是最后一页。[/yellow]")
        elif choice == "p":
            if current_page > 0:
                current_page -= 1
            else:
                console.print("[yellow]已是第一页。[/yellow]")
        else:
            try:
                idx = int(choice) - 1
                if not (0 <= idx < len(playlists)):
                    raise ValueError
            except ValueError:
                console.print("[red]无效输入。[/red]")
                continue
            run_sync(playlists[idx], netease, navi, cfg, console)
```

- [ ] **Step 3: 运行全套测试确认无回归**

```bash
.venv/bin/pytest tests/ -v
```

预期：所有测试 PASS

- [ ] **Step 4: 提交**

```bash
git add main.py
git commit -m "feat: paginated top playlists with cache in menu_top_playlists [ai:y]"
```

---

## Task 5: 端到端验证

**Files:** 无新文件

- [ ] **Step 1: 确认 .env 已配置，NeteaseCloudMusicApi 已启动**

```bash
cat .env  # 确认 NETEASE_API_URL / NAVIDROME_URL / NAVIDROME_USER / NAVIDROME_PASSWORD 已填写
docker compose ps  # 确认 netease-api 容器 running
```

- [ ] **Step 2: 运行脚本，验证翻页**

```bash
python main.py
```

验证步骤：
1. 选 `1` → 看到第 1 页歌单表格
2. 输入 `n` → 看到第 2 页歌单（提示"第 2 页，共已加载 2 页"）
3. 输入 `p` → 回到第 1 页，无需重新请求
4. 输入 `p` → 提示"已是第一页"
5. 在最后一页输入 `n` → 提示"已是最后一页"

- [ ] **Step 3: 验证预览功能**

1. 选一个歌单进入同步
2. 匹配完成后，看到汇总数字
3. 输入 `1` → 看到缺失歌曲列表，菜单继续显示
4. 输入 `2` → 看到模糊匹配表格
5. 输入 `3` → 看到模糊+缺失全部
6. 按回车 → 进入确认提示
7. 输入 `N` → 取消，不创建播放列表

- [ ] **Step 4: 运行全套测试**

```bash
.venv/bin/pytest tests/ -v
```

预期：所有测试 PASS
