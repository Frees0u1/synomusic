from __future__ import annotations

import argparse
import re

from src.downloader.models import TrackInfo
from src.downloader.providers import DICMusicProvider, TorrentHTMLParser
from src.downloader.scoring import build_search_queries, merge_aliases
from src.downloader.selection import TorrentSelector
from src.downloader.settings import DownloaderSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Readonly downloader provider diagnostics")
    parser.add_argument("--title", default="The Bends")
    parser.add_argument("--artist", default="Radiohead")
    parser.add_argument("--album", default="")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--debug-dic-rows", action="store_true")
    parser.add_argument(
        "--select-preview",
        action="store_true",
        help="show readonly non-interactive selector decisions without submitting downloads",
    )
    args = parser.parse_args()

    settings = DownloaderSettings.from_env()
    providers = settings.build_providers()
    resolver = settings.build_metadata_resolver()
    track = TrackInfo(title=args.title, artist=args.artist, album=args.album)
    resolved = resolver.resolve(track) if resolver else None
    aliases = merge_aliases(settings.query_aliases, resolved.aliases if resolved else {})

    print(f"track: {track.search_label}")
    if resolved and resolved.notes:
        print("metadata:", ", ".join(resolved.notes))
    if aliases:
        print("aliases:")
        for source, values in aliases.items():
            print(f"  - {source}: {', '.join(values)}")
    print("queries:")
    for query in build_search_queries(track, aliases=aliases):
        print(f"  - {query}")
    print("providers:", ", ".join(provider.name for provider in providers))

    if args.debug_dic_rows:
        _debug_dic_rows(providers, track)
        return

    selector = TorrentSelector(
        auto_threshold=settings.auto_threshold,
        min_threshold=settings.min_threshold,
    )

    for provider in providers:
        print(f"\n== {provider.name} ==")
        try:
            results = provider.search(track, limit=args.limit, aliases=aliases)
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue

        if not results:
            print("no results")
            continue

        if args.select_preview:
            selected = selector.select(track, results, interactive=False)
            print(_selection_preview(results, selected, settings.auto_threshold, settings.min_threshold))

        for idx, result in enumerate(results, 1):
            url_kind = "magnet" if result.url.startswith("magnet:") else result.url
            print(
                f"{idx}. score={result.score} seeders={result.seeders} "
                f"size={result.size_bytes} title={result.title}"
            )
            print(f"   {_redact_url(url_kind)[:180]}")


def _selection_preview(results, selected, auto_threshold: int, min_threshold: int) -> str:
    if selected is None:
        return f"selection: none (best score below min threshold {min_threshold})"

    ordered = sorted(results, key=lambda r: (r.score, r.seeders), reverse=True)
    runner_up = ordered[1].score if len(ordered) > 1 else 0
    gap = selected.score - runner_up
    if selected.score >= auto_threshold and gap >= 4:
        mode = f"auto clear-best (threshold {auto_threshold}, gap {gap})"
    else:
        mode = f"non-interactive fallback (min threshold {min_threshold}, gap {gap})"
    return (
        "selection: "
        f"{mode}: score={selected.score} seeders={selected.seeders} "
        f"size={_format_size(selected.size_bytes)} title={selected.title}"
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def _debug_dic_rows(providers: list[object], track: TrackInfo) -> None:
    provider = next((p for p in providers if isinstance(p, DICMusicProvider)), None)
    if provider is None:
        print("\nDIC provider is not configured")
        return

    provider.login()
    query = build_search_queries(track)[0]
    response = provider._session.get(  # diagnostics only
        f"{provider._base_url}/torrents.php",
        params={"searchstr": query},
        timeout=provider._timeout,
    )
    response.raise_for_status()
    parser = TorrentHTMLParser()
    parser.feed(response.text)

    print(f"\n== dicmusic rows for {query!r} ==")
    for idx, row in enumerate(parser.rows[:20], 1):
        print(f"{idx}. text={row.text[:220]}")
        for link in row.links[:12]:
            text = link.text or "<empty>"
            print(f"   - {text[:80]!r} -> {_redact_url(link.href)[:160]}")


def _redact_url(value: str) -> str:
    value = re.sub(r"([?&](?:authkey|torrent_pass|passkey)=)[^&\s]+", r"\1<redacted>", value)
    return re.sub(r"([?&]torrent_pass=)[^&\s]+", r"\1<redacted>", value)


if __name__ == "__main__":
    main()
