import json
import logging

import requests

from config import config

if config.offline_mode:
    import test_data

API_BASE = 'https://a.4cdn.org'
IMG_BASE = 'https://i.4cdn.org'
TIMEOUT = 15

logger = logging.getLogger(__name__)


class ChanServer:

    def __init__(self) -> None:
        self._boards = None
        self._session = requests.Session()

        self.getBoards()

    def close_session(self) -> None:
        self._session.close()

    def _get_json(self, url: str):
        response = self._session.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()

    # Gets all the boards
    def getBoards(self) -> dict:
        if self._boards:
            return self._boards

        try:
            if config.offline_mode:
                data = json.loads(test_data.boards_list)
            else:
                data = self._get_json(f'{API_BASE}/boards.json')
        except (requests.RequestException, ValueError):
            logger.error('Error: Failed to process getBoards()')
            # 失敗はキャッシュしない(次回呼び出しで再取得できるように)
            return {'boards': []}

        self._boards = data
        return data

    # Get a page of threads from a board. 失敗時は None
    def getThreads(self, board, page):
        if config.offline_mode:
            return json.loads(test_data.threads_list)
        try:
            return self._get_json(f'{API_BASE}/{board}/{page}.json')
        except (requests.RequestException, ValueError):
            logger.error('Error: Failed to process getThreads()')
            return None

    # Get posts for a particular thread. 失敗時は None
    def getReplies(self, board, thread):
        if config.offline_mode:
            return json.loads(test_data.replies_list)
        try:
            return self._get_json(f'{API_BASE}/{board}/thread/{thread}.json')
        except (requests.RequestException, ValueError):
            logger.error('Error: Failed to process getReplies()')
            return None

    # Get thumbnail for a post as ASCII art. 失敗時は空文字
    def getThumbNail(self, board, img_id, dimensions: tuple) -> str:
        if config.offline_mode:
            return '**********\n**********'

        try:
            # aalib が無い環境でも、画像表示を使わない限り動くよう遅延importする
            import ascii_image

            response = self._session.get(
                f'{IMG_BASE}/{board}/{img_id}s.jpg', timeout=TIMEOUT)
            response.raise_for_status()
            file = ascii_image.open_img(response.content)
            if file is None:
                return ''
            return ascii_image.convert_image(
                img=file, x=dimensions[0], y=dimensions[1])
        except Exception:
            logger.error('Error: Failed to get image for %s => %s', board, img_id)
            return ''

    # Full-size image URL for a post (画像が無ければ None)
    @staticmethod
    def getImageURL(board, post: dict):
        if 'tim' in post and 'ext' in post:
            return f"{IMG_BASE}/{board}/{post['tim']}{post['ext']}"
        return None

    def __str__(self) -> str:
        return str(self.__class__.__name__)
