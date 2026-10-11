"""4chan BBS: ターミナルで動くCLI版(telnet非依存)。

    python cli.py
"""
from __future__ import annotations

import atexit
import logging
import os
import re
import shutil

try:
    import readline  # 入力履歴・行編集(Windowsでは pyreadline3 があれば有効)
except ImportError:
    readline = None

import chanjson
import sendqueue
import webhook
from config import config
from htmlcleaner import strip_tags

logging.basicConfig(format='[%(asctime)-15s] %(message)s',
                    level=config.loggingLevel)

HISTORY_FILE = os.path.expanduser(
    getattr(config, 'history_file', '~/.4chanbbs_history'))
HISTORY_LENGTH = 1000

BLUE = '\x1b[34m'
RED = '\x1b[31;1m'
RESET = '\x1b[0m'
SEPARATOR = f'{BLUE}*-------------------------------*{RESET}'

HINT_LIST = 'Enter - Next | o - Open | wh - Send | h - Help | q - Back'
HINT_THREAD = 'Enter - Next | wh - Send | h - Help | q - Back'
HINT_LAST = 'End of thread | wh - Send | h - Help | Enter/q - Back'

HELP_SHORT = '''
Usage: COMMAND {board} {thread}

    where COMMAND := {lb, lt, gr, wh, jobs, stop, i, di, ei, h, q}
'''

HELP_LONG = '''Commands -> (<name>, <alias>): usage
listboards, lb:
    list available boards.
        ex: lb

listthreads, lt:
    list the threads in a board. lt <board name>
        ex: lt b

getreplies, gr:
    list replies to a thread. gr <board name> <threadID>
        ex: gr a 115931903

webhook, wh:
    send a thread to the Discord webhooks in config.webhook_file.
    Images are sent as URLs. Numbers start at 1 (1 = the first post)
    and a range includes both ends.
        wh                  the thread you are viewing (all posts)
        wh 1                only post 1
        wh 3-10             posts 3 to 10
        wh 5-               post 5 to the last
        wh <board> <threadID> [range]    a thread given explicitly
    Each message ends with the thread URL (a link to that post).
    Options: --no-images (no image URLs), --no-url (no thread URL),
             --dry-run (print only)
        ex: wh a 115931903 3-10 --dry-run
    Sending runs in the background, so you can keep using other commands.
    Jobs are processed one at a time (3+ seconds between messages).

jobs:
    list the sending jobs and their progress / failures.

stop <jobNo>:
    stop a job after the message being sent now (a queued job is cancelled).
    With no number, stops the only active job.

info, i:
    show the current board / thread.

disableimages, di / enableimages, ei:
    turn the thumbnail display off / on.

help, h:
    shows this message

exit, quit, q:
    exits the program

While viewing threads or replies (the [/board/ threadID ...] prompt):
    Enter or n  next          o  open the thread (thread list only)
    wh [range]  send          h  help           i  current thread
    jobs        progress      stop [n]  stop a job
    q           back'''

_RANGE_RE = re.compile(r'^(\d+)(?:(-)(\d*))?$')


# ---------------------------------------------------------- 入力まわり

def setup_history() -> None:
    if readline is None:
        return
    try:
        readline.read_history_file(HISTORY_FILE)
    except OSError:
        pass
    readline.set_history_length(HISTORY_LENGTH)
    atexit.register(_save_history)


def _save_history() -> None:
    try:
        readline.write_history_file(HISTORY_FILE)
    except OSError:
        pass


def ask(prompt: str, keep: tuple = ()) -> str:
    """ページ送り用の1行入力。

    n / o / q などのページ送り操作はコマンド履歴に残さない。
    keep に挙げたコマンド(wh など)で始まる入力だけ履歴に残す。
    EOF(Ctrl-D)・Ctrl-C は 'q' として扱う。
    """
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 'q'

    if answer and answer.split()[0] not in keep and readline is not None:
        try:
            readline.remove_history_item(readline.get_current_history_length() - 1)
        except (ValueError, IndexError):
            pass
    return answer


def parse_range(spec: str, total: int) -> tuple[int, int]:
    """'3' / '3-10' / '5-' を (start, end) に変換する。1始まり、両端を含む。"""
    m = _RANGE_RE.match(spec)
    if not m:
        raise ValueError(f"Invalid range '{spec}'. Use 3, 3-10 or 5-")

    start = int(m.group(1))
    if m.group(2) is None:
        end = start
    elif m.group(3) == '':
        end = total
    else:
        end = int(m.group(3))

    if start < 1:
        raise ValueError('Numbers start at 1')
    if start > total:
        raise ValueError(f'The thread has only {total} posts')
    if end < start:
        raise ValueError('The end of the range is smaller than the start')
    return start, min(end, total)


# ---------------------------------------------------------- 本体

class Console:

    def __init__(self, server: chanjson.ChanServer) -> None:
        self._server = server
        self._show_images: bool = bool(config.showImages)
        self._cmd: str = ''
        # 最後に見たスレッド (板ID, スレッドID)。引数なしの wh の対象になる
        self._current: tuple[str, str] | None = None
        # Webhook送信はバックグラウンドのスレッドで、1本ずつ順番に処理する
        self._sender = sendqueue.SendQueue(self._notify)

        self._commands = {
            'listboards': self.command_listboards,
            'listthreads': self.command_listthreads,
            'getreplies': self.command_getreplies,
            'webhook': self.command_webhook,
            'jobs': self.command_jobs,
            'stop': self.command_stop,
            'info': self.command_info,
            'enableimages': self.command_enableimages,
            'disableimages': self.command_disableimages,
            'exit': self.command_exit,
            'help': self.command_help,
        }
        aliases = {
            'lb': 'listboards',
            'lt': 'listthreads',
            'gr': 'getreplies',
            'wh': 'webhook',
            'i': 'info',
            'ei': 'enableimages',
            'di': 'disableimages',
            'h': 'help',
            'quit': 'exit',
            'q': 'exit',
        }
        for alias, name in aliases.items():
            self._commands[alias] = self._commands[name]

    @property
    def img_size(self) -> tuple:
        """サムネイルのASCII化サイズ (width, height)。端末サイズから計算する。"""
        if getattr(config, 'naws', True):
            cols, rows = shutil.get_terminal_size((80, 24))
            height = max(min(rows - 2, cols // 2), 4)
            return (height * 2, height)  # 文字は縦長なので幅を2倍に
        return (config.img_width, config.img_hight)

    def _notify(self, message: str) -> None:
        """バックグラウンドの送信スレッドからの通知。入力中の行を再描画する。"""
        print(f'\n{message}')
        if readline is not None:
            try:
                readline.redisplay()
            except Exception:
                pass

    # ---- プロンプト

    def _main_prompt(self) -> str:
        """トップのプロンプト。最後に見たスレッドがあれば板ID/スレッドIDを含める。"""
        if self._current is None:
            return f'\n{config.prompt}'
        board, thread = self._current
        base = config.prompt.rstrip()
        if base.endswith('>'):
            return f'\n{base[:-1].rstrip()} [/{board}/ {thread}]> '
        return f'\n{base} [/{board}/ {thread}] '

    def _prompt(self, label: str, hint: str, allow_open: bool = False) -> str:
        """ページ送り用プロンプト。先頭の [ ] に板ID/スレッドIDを常に出す。

        wh / h / i はその場で実行して同じプロンプトに戻る。
        戻り値は 'next' / 'open' / 'quit'。
        """
        while True:
            parts = ask(f'[{label}] {hint}: ',
                        keep=('wh', 'webhook', 'h', 'help', 'i', 'info',
                              'jobs', 'stop')).split()
            cmd = parts[0] if parts else ''

            if cmd in ('', 'n'):
                return 'next'
            if cmd == 'q':
                return 'quit'
            if cmd == 'o' and allow_open:
                return 'open'

            if cmd in ('wh', 'webhook'):
                try:
                    self.command_webhook(parts[1:])
                except KeyboardInterrupt:
                    print('\n(interrupted)')
            elif cmd in ('h', 'help'):
                print(HELP_LONG)
            elif cmd in ('i', 'info'):
                self.command_info([])
            elif cmd == 'jobs':
                self.command_jobs([])
            elif cmd == 'stop':
                self.command_stop(parts[1:])
            else:
                choices = ('Enter/n, ' + ('o, ' if allow_open else '')
                           + 'q, wh, jobs, stop, h, i')
                print(f'Unknown input: {cmd}  ({choices})')

    # ---- 表示

    def _show_post(self, board: str, post: dict, footer: str = '') -> None:
        print()
        print(SEPARATOR)

        header = f"{post.get('no')} - {RED}{post.get('name', 'Anonymous')}{RESET}"
        if 'sub' in post:
            header += ' - ' + strip_tags(str(post['sub']))
        print(header)

        if self._show_images and 'tim' in post:
            img = self._server.getThumbNail(board, post['tim'], self.img_size)
            if img:
                print()
                print(img)
                print()

        if 'com' in post:
            try:
                print(strip_tags(post['com']))
            except Exception:
                pass

        if footer:
            print(footer)
        print(SEPARATOR)

    def _find_board(self, board_id: str):
        data = self._server.getBoards()
        for board in data.get('boards', []):
            if board.get('board') == board_id:
                return board
        return None

    def _fetch_posts(self, board: str, thread_id):
        data = self._server.getReplies(board, thread_id)
        posts = data.get('posts') if isinstance(data, dict) else None
        return posts or None

    def _view_thread(self, board: str, thread_id) -> bool:
        """スレッドを順に表示する。取得できなければ False(閲覧は始まらない)。"""
        posts = self._fetch_posts(board, thread_id)
        if not posts:
            print(f'Could not load /{board}/ {thread_id} '
                  '(deleted, not found, or a communication error).')
            return False

        # 閲覧中だけ「今見ているスレッド」にし、終わったら元の状態に戻す
        previous = self._current
        self._current = (board, str(thread_id))
        try:
            total = len(posts)
            for i, post in enumerate(posts, 1):
                self._show_post(board, post, f'{i}/{total}  thread {thread_id}')
                label = f'/{board}/ {thread_id}  {i}/{total}'

                if i < total:
                    if self._prompt(label, HINT_THREAD) == 'quit':
                        break
                else:
                    self._prompt(label, HINT_LAST)  # 最後はどの入力でも戻る
        finally:
            self._current = previous
        return True

    # ---- コマンド

    def command_listboards(self, params: list) -> None:
        boards = self._server.getBoards().get('boards', [])
        if not boards:
            print('Communication error...')
            return

        id_w, name_w = 5, 24
        border = f"*{'-' * (id_w + 2)}*{'-' * (name_w + 2)}*"
        print(border)
        print(f"| {'ID':<{id_w}} | {'Name':<{name_w}} |")
        print(border)
        for board in boards:
            bid = str(board.get('board', ''))
            title = str(board.get('title', ''))[:name_w]
            print(f'| {bid:<{id_w}} | {title:<{name_w}} |')
            print(border)

    def command_listthreads(self, params: list) -> None:
        """lt <boardID>: 板のスレッドを順に表示する。"""
        if not params:
            print('Usage: lt <boardID>')
            return

        board_id = params[0]
        info = self._find_board(board_id)
        if info is None:
            print('Communication error or invalid board ID...')
            return

        previous = self._current
        try:
            if self._list_threads(board_id, int(info.get('pages', 1))):
                print('(end of board)')
        finally:
            self._current = previous  # 一覧を出たら、IDの表示も消える

    def _list_threads(self, board_id: str, pages: int) -> bool:
        """スレッド一覧を順に表示する。最後まで進めば True、q や通信エラーなら False。"""
        for page in range(1, pages + 1):
            data = self._server.getThreads(board_id, page)
            threads = data.get('threads') if isinstance(data, dict) else None
            if not threads:
                print('Communication error...')
                return False

            for thread in threads:
                posts = thread.get('posts') or []
                if not posts:
                    continue
                op = posts[0]
                self._current = (board_id, str(op['no']))
                self._show_post(board_id, op,
                                f"replies: {op.get('replies', 0)}  thread {op['no']}")

                label = f"/{board_id}/ {op['no']}  page {page}/{pages}"
                while True:
                    answer = self._prompt(label, HINT_LIST, allow_open=True)
                    if answer == 'quit':
                        return False
                    if answer == 'open' and not self._view_thread(board_id, op['no']):
                        continue  # 開けなかったら、同じスレッドの入力に戻る
                    break
        return True

    def command_getreplies(self, params: list) -> None:
        """gr <boardID> <threadID>: スレッドのレスを順に表示する。"""
        if len(params) < 2:
            print('Usage: gr <boardID> <threadID>')
            return
        self._view_thread(params[0], params[1])

    def command_info(self, params: list) -> None:
        if self._current is None:
            print('No thread selected yet.')
        else:
            board, thread = self._current
            print(f'Current thread: /{board}/ {thread}')

    def command_webhook(self, params: list) -> None:
        """wh [range] / wh <board> <thread> [range] [--no-images] [--dry-run]"""
        usage = ('Usage: wh [range] | wh <boardID> <threadID> [range]'
                 ' [--no-images] [--no-url] [--dry-run]')
        flags = {p for p in params if p.startswith('--')}
        args = [p for p in params if not p.startswith('--')]

        range_spec = None
        if len(args) <= 1:
            # 引数なし/範囲だけ: 今見ているスレッドが対象
            if self._current is None:
                print('No thread selected yet. ' + usage)
                return
            board, thread_id = self._current
            if args:
                range_spec = args[0]
        elif len(args) <= 3:
            board, thread_id = args[0], args[1]
            if len(args) == 3:
                range_spec = args[2]
        else:
            print(usage)
            return

        include_images = '--no-images' not in flags
        # スレッドURLを各投稿の本文に含める(--no-url で無効)
        url_thread = None if '--no-url' in flags else thread_id
        # offline_mode では実際の送信はせず、内容の表示だけ行う
        dry_run = '--dry-run' in flags or config.offline_mode

        posts = self._fetch_posts(board, thread_id)
        if not posts:
            print('Communication error or invalid board/thread ID...')
            return

        total = len(posts)
        start, end = 1, total
        if range_spec is not None:
            try:
                start, end = parse_range(range_spec, total)
            except ValueError as ex:
                print(ex)
                return
        selected = posts[start - 1:end]

        print(f'/{board}/ {thread_id}: posts {start}-{end} '
              f'({len(selected)} of {total})')

        if dry_run:
            for post in selected:
                print(webhook.format_post(board, post, include_images, url_thread))
                print('-' * 30)
            print(f'(dry-run: {len(selected)} posts, nothing was sent)')
            return

        webhook_file = getattr(config, 'webhook_file', 'webhooks.txt')
        urls = webhook.load_webhook_urls(webhook_file)
        if not urls:
            print(f'No webhook URLs found in {webhook_file}')
            return

        interval = max(float(getattr(config, 'webhook_interval',
                                     webhook.MIN_INTERVAL)),
                       webhook.MIN_INTERVAL)
        eta = int(len(selected) * interval)
        print(f'{len(selected)} posts -> {len(urls)} webhook(s), '
              f'interval {interval:g}s (about {eta}s)')
        if ask('Send? [y/N]: ') != 'y':
            print('Cancelled.')
            return

        job, ahead = self._sender.submit(
            board, thread_id, start, end, selected, urls,
            include_images, url_thread is not None, interval)
        if ahead:
            print(f'Job {job.id} queued ({ahead} job(s) ahead). '
                  'Check progress with: jobs')
        else:
            print(f'Job {job.id} started in the background. '
                  'Check progress with: jobs')

    def command_jobs(self, params: list) -> None:
        jobs = self._sender.jobs
        if not jobs:
            print('No jobs.')
            return
        for job in jobs:
            line = (f'Job {job.id}  {job.state:<8} /{job.board}/ {job.thread_id}  '
                    f'posts {job.start}-{job.end}  {job.processed}/{job.total}')
            if job.failed_posts:
                line += f'  ({job.failed_posts} failed)'
            print(line)
            if job.error:
                print(f'    {job.error}')
            for url, nums in job.failures.items():
                if nums:
                    shown = ', '.join(f'#{n}' for n in nums[:5])
                    more = ' ...' if len(nums) > 5 else ''
                    print(f'    {webhook.mask_url(url)}: {len(nums)} failed ({shown}{more})')

    def command_stop(self, params: list) -> None:
        """stop [jobNo]: ジョブを中止する(番号なしは、動いているジョブが1つのときだけ)。"""
        if params:
            try:
                job_id = int(params[0])
            except ValueError:
                print('Usage: stop [jobNo]')
                return
        else:
            active = self._sender.active_jobs()
            if len(active) != 1:
                print('Usage: stop <jobNo>' if active else 'No active jobs.')
                return
            job_id = active[0].id

        if self._sender.stop(job_id):
            print(f'Stopping job {job_id} ...')
        else:
            print(f'Job {job_id} is not active.')

    def command_enableimages(self, params: list) -> None:
        print('Images have been enabled')
        self._show_images = True

    def command_disableimages(self, params: list) -> None:
        print('Images have been disabled')
        self._show_images = False

    def command_exit(self, params: list):
        active = self._sender.active_jobs()
        if active:
            desc = ', '.join(f'Job {j.id} {j.processed}/{j.total}' for j in active)
            if ask(f'Sending in progress ({desc}). Quit anyway? [y/N]: ') != 'y':
                return None  # 終了せず、プロンプトに戻る
            self._sender.cancel_all()
        print('good bye')
        return True

    def command_help(self, params: list) -> None:
        print(HELP_LONG if self._cmd == 'help' else HELP_SHORT)

    # ---- メインループ

    def run(self) -> None:
        print(config.welcome_message)
        print('    gr <boardID> <threadID> - Show the replies of a thread')
        print('    wh [range]              - Send the thread you are viewing to Discord')
        print('    jobs / stop [n]         - Check / stop background sending')
        print('    i                       - Show the current board / thread')
        print('    q                       - Quit (history: up/down arrows)\n')

        while True:
            try:
                line = input(self._main_prompt())
            except EOFError:
                print()
                self._sender.cancel_all()
                break
            except KeyboardInterrupt:
                print()
                continue

            parts = line.strip().split()
            if not parts:
                continue

            self._cmd = parts[0].lower()
            handler = self._commands.get(self._cmd)
            if handler is None:
                print('cmd not found. Try h or help for valid commands')
                continue

            try:
                if handler(parts[1:]) is True:
                    break
            except KeyboardInterrupt:
                print('\n(interrupted)')
            except Exception as ex:
                logging.exception('Unexpected error in command %s', self._cmd)
                print(f'Error: {type(ex).__name__}: {ex}')


def main() -> None:
    setup_history()
    server = chanjson.ChanServer()
    console = Console(server)
    try:
        console.run()
    finally:
        console._sender.cancel_all()
        server.close_session()


if __name__ == '__main__':
    main()
