"""Discord Webhook送信(4chan CLI用の同期版)。

Botトークンは不要で、Webhook URLへ直接POSTする。複数URLを指定した場合は
全URLへ同一内容を送る。画像は添付せず、画像URLを本文の末尾に載せる
(DiscordがURLからプレビューを展開する)。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

import requests

from chanjson import ChanServer
from htmlcleaner import strip_tags

# Webhookへの送信間隔(秒)。これより短くはできない。
MIN_INTERVAL = 3.0
MAX_RETRIES = 5
DISCORD_LIMIT = 2000
CHUNK_SIZE = 1900
BOARD_BASE = 'https://boards.4chan.org'

logger = logging.getLogger(__name__)


def load_webhook_urls(path: str) -> list[str]:
    """webhook URL一覧を読み込む。JSON配列・改行区切りテキストのどちらにも対応する
    (`#`始まりの行はコメントとして無視)。ファイルが無ければ空リストを返す。
    """
    p = Path(path).expanduser()
    if not p.exists():
        return []

    text = p.read_text(encoding='utf-8').strip()
    if not text:
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        return [str(u).strip() for u in data if str(u).strip()]

    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith('#')
    ]


def mask_url(url: str) -> str:
    """Webhook URLは秘密情報なので、表示用に末尾だけ残す。"""
    return '...' + url[-6:]


def thread_url(board: str, thread_id, post_no=None) -> str:
    """スレッドのURL。post_no を渡すと、その投稿へジャンプするURLになる。"""
    url = f'{BOARD_BASE}/{board}/thread/{thread_id}'
    if post_no is not None:
        url += f'#p{post_no}'
    return url


def format_post(board: str, post: dict, include_image: bool = True,
                thread_id=None) -> str:
    """1投稿をDiscord向けの本文にする。

    画像があればそのURLを、thread_id を渡せばスレッドのURL(この投稿へのリンク)を
    最後の行に付ける。
    """
    name = strip_tags(str(post.get('name', 'Anonymous')))
    if post.get('trip'):
        name += ' ' + str(post['trip'])

    lines = [f"**{post.get('no')}** : {name} : {post.get('now', '')}"]

    if post.get('sub'):
        lines.append(f"**{strip_tags(str(post['sub']))}**")

    if post.get('com'):
        try:
            body = strip_tags(post['com'])
        except Exception:
            body = ''
        if body:
            lines.append(body)

    if include_image:
        url = ChanServer.getImageURL(board, post)
        if url:
            lines.append(url)

    if thread_id is not None:
        # <> で囲むと、Discordがこのリンクのプレビューを展開しない
        # (画像URLのプレビューを優先し、リンク自体は押せるまま)
        lines.append(f"<{thread_url(board, thread_id, post.get('no'))}>")

    return '\n'.join(lines)


def split_message(text: str, limit: int = CHUNK_SIZE) -> list[str]:
    """limit文字以内に分割する。できるだけ改行位置で切る
    (末尾のURL行が途中で切れないようにするため)。
    """
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        if cut < limit // 2:
            # 手前すぎる改行(見出しだけが単独で送られる等)では切らず、limitで切る
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip('\n')
    parts.append(text)
    return parts


def _post_once(session: requests.Session, url: str, content: str) -> None:
    # 4chanの本文に @everyone などが含まれていてもメンションさせない
    payload = {'content': content, 'allowed_mentions': {'parse': []}}

    for _ in range(MAX_RETRIES):
        resp = session.post(url, json=payload, timeout=30)

        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get('retry_after', 1.0))
            except (ValueError, TypeError):
                retry_after = 1.0
            time.sleep(retry_after if retry_after > 0 else 1.0)
            continue

        if resp.status_code >= 400:
            raise RuntimeError(f'Webhook送信エラー: {resp.status_code} {resp.text}')

        return

    raise RuntimeError('Webhook送信エラー: レート制限のリトライ上限に達しました')


def send_to_webhook(session: requests.Session, url: str, content: str,
                    interval: float = MIN_INTERVAL) -> None:
    """1件のメッセージを1つのURLへ送信する(2000字超は自動分割)。"""
    if len(content) <= DISCORD_LIMIT:
        _post_once(session, url, content)
        return

    parts = split_message(content)
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        _post_once(session, url, part if last else part + '\n(続く...)')
        if not last:
            time.sleep(interval)


def broadcast_posts(
    urls: list[str],
    board: str,
    posts: list[dict],
    include_images: bool = True,
    interval: float = MIN_INTERVAL,
    on_progress: Callable[[int, int, dict], None] | None = None,
    thread_id=None,
) -> dict[str, list[int]]:
    """投稿を1件ずつ全URLへ順にブロードキャストする。

    戻り値は {webhook_url: [送信に失敗したpost no のリスト]}。
    """
    interval = max(interval, MIN_INTERVAL)
    failures: dict[str, list[int]] = {u: [] for u in urls}

    with requests.Session() as session:
        for i, post in enumerate(posts, 1):
            content = format_post(board, post, include_images, thread_id)

            for url in urls:
                try:
                    send_to_webhook(session, url, content, interval)
                except Exception as ex:
                    failures[url].append(post.get('no'))
                    logger.warning('送信失敗 %s #%s: %s',
                                   mask_url(url), post.get('no'), ex)

            if on_progress:
                on_progress(i, len(posts), post)

            if i < len(posts):
                time.sleep(interval)

    return failures
