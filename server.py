# -*- coding: utf-8 -*-
"""
小说阅读器 - 本地服务器
使用方法：双击 "启动阅读器.bat" 或运行 python server.py
"""

import http.server
import json
import os
import re
import urllib.parse
import webbrowser
import socket

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
NOVEL_DIR = os.path.join(ROOT_DIR, 'main')     # 小说：main/<首字母>/<书名>/<章节>.txt
MUSIC_DIR = os.path.join(ROOT_DIR, 'music')    # 本地音乐
PORT = 8080

# 允许上传的音频扩展名
AUDIO_EXTS = ('.mp3', '.m4a', '.flac', '.wav', '.ogg', '.aac', '.opus', '.wma')


CHINESE_NUM = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
               '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def chinese_to_int(s: str) -> int:
    """将中文数字字符串转为整数，如 '一千一十一' → 1011"""
    if not s:
        return 0
    result = 0
    temp = 0
    for ch in s:
        if ch in CHINESE_NUM:
            temp = CHINESE_NUM[ch]
        elif ch == '十':
            temp = 10 if temp == 0 else temp * 10
            result += temp
            temp = 0
        elif ch == '百':
            temp = max(temp, 1) * 100
            result += temp
            temp = 0
        elif ch == '千':
            temp = max(temp, 1) * 1000
            result += temp
            temp = 0
    result += temp
    return result


def chapter_sort_key(filename):
    """按章节号排序，支持 '第100章'、'第一千一十一章'、'第两百一十一章' 等格式。

    文件名可能是带子目录的相对路径（有些书按卷分文件夹），排序只看文件名那一段。
    """
    base = filename.replace('\\', '/').split('/')[-1]

    # 阿拉伯数字: 第123章
    m = re.match(r'第(\d+)章', base)
    if m:
        return (int(m.group(1)), filename.lower())

    # 中文数字: 第两百一十一章、第一千一十一章等
    re_cn = r'[一二三四五六七八九十百千万零两]+'
    m = re.match(rf'第({re_cn})章', base)
    if m:
        return (chinese_to_int(m.group(1)), filename.lower())

    # 其他文件（如请假条）排在最后
    return (9999, filename.lower())


def list_chapters(book_dir):
    """递归收集书目录下的 .txt，返回相对路径（以 / 分隔）。

    原来只用 os.listdir，看不到子目录 —— 按卷分文件夹的书会整卷漏掉。
    """
    out = []
    for root, dirs, files in os.walk(book_dir):
        dirs.sort()
        rel_root = os.path.relpath(root, book_dir)
        for name in files:
            if name.lower().endswith('.txt'):
                rel = name if rel_root == '.' else os.path.join(rel_root, name)
                out.append(rel.replace('\\', '/'))
    return sorted(out, key=chapter_sort_key)


def book_has_chapters(book_dir):
    """书目录里（含子目录）是否存在 .txt"""
    for _root, _dirs, files in os.walk(book_dir):
        if any(f.lower().endswith('.txt') for f in files):
            return True
    return False


def detect_encoding(file_path):
    """检测文件编码，优先 UTF-8，回退 GBK/GB18030"""
    encodings = ['utf-8', 'gbk', 'gb18030']
    for enc in encodings:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                f.read()
                return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return 'utf-8'


# ---------------------------------------------------------------------------
# 中文书名 → 拼音首字母
#
# GB2312 的一级汉字区（3755 字）本身就是按拼音排序的，所以能用区位码反推
# 首字母，不需要任何第三方库。二级汉字区（生僻字）按部首排序，这套办法不
# 适用，会落到 '#'。
# ---------------------------------------------------------------------------
_PY_BOUNDS = [
    (0xB0A1, 'A'), (0xB0C5, 'B'), (0xB2C1, 'C'), (0xB4EE, 'D'), (0xB6EA, 'E'),
    (0xB7A2, 'F'), (0xB8C1, 'G'), (0xB9FE, 'H'), (0xBBF7, 'J'), (0xBFA6, 'K'),
    (0xC0AC, 'L'), (0xC2E8, 'M'), (0xC4C3, 'N'), (0xC5B6, 'O'), (0xC5BE, 'P'),
    (0xC6DA, 'Q'), (0xC8BB, 'R'), (0xC8F6, 'S'), (0xCBFA, 'T'), (0xCDDA, 'W'),
    (0xCEF4, 'X'), (0xD1B9, 'Y'), (0xD4D1, 'Z'),
]


def first_letter(name):
    """归档首字母：英文取首字符，中文取拼音首字母，都不行则归 '#'。"""
    for ch in name:
        if ch.isascii() and ch.isalpha():
            return ch.upper()
        if '\u4e00' <= ch <= '\u9fff':
            try:
                gb = ch.encode('gb2312')
            except UnicodeEncodeError:
                continue
            if len(gb) != 2:
                continue
            code = gb[0] * 256 + gb[1]
            hit = None
            for bound, letter in _PY_BOUNDS:
                if code >= bound:
                    hit = letter
                else:
                    break
            if hit:
                return hit
    return '#'


def safe_name(name):
    """清掉文件名里的非法字符，并去掉首尾的点（防目录穿越）。"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name).strip().strip('.')
    return name or '未命名'


def safe_rel_path(rel):
    """规范化前端传来的相对路径，挡掉 ../ 之类的越权写法。"""
    rel = rel.replace('\\', '/').lstrip('/')
    parts = []
    for seg in rel.split('/'):
        # 必须先按原始片段判断，再清洗 —— 反过来的话 '../' 会先被
        # safe_name 改成合法名字，'.'/ '..' 的检查就失效了。
        if not seg or seg in ('.', '..'):
            continue
        clean = safe_name(seg)
        if clean:
            parts.append(clean)
    return '/'.join(parts)


def ensure_dirs():
    """保证 main/ 与 music/ 存在 —— 换机器、换位置也能自动建好。"""
    os.makedirs(NOVEL_DIR, exist_ok=True)
    os.makedirs(MUSIC_DIR, exist_ok=True)


class _SliceReader:
    """限定读取字节数的包装，用于 HTTP Range 响应。"""

    def __init__(self, fh, length):
        self.fh = fh
        self.left = length

    def read(self, size=-1):
        if self.left <= 0:
            return b''
        if size is None or size < 0 or size > self.left:
            size = self.left
        chunk = self.fh.read(size)
        self.left -= len(chunk)
        return chunk

    def close(self):
        try:
            self.fh.close()
        except Exception:
            pass


class NovelHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == '/api/structure':
            self._handle_structure()
        elif parsed.path == '/api/chapters':
            self._handle_chapters(params)
        elif parsed.path == '/api/content':
            self._handle_content(params)
        elif parsed.path == '/api/books':
            self._handle_books()
        elif parsed.path == '/api/music':
            self._handle_music_list()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/ingest':
            self._handle_ingest()
        elif parsed.path == '/api/music/upload':
            self._handle_music_upload()
        else:
            self._send_error('未知接口')

    def do_OPTIONS(self):
        self.send_response(200)
        self._add_cors_headers()
        self.end_headers()

    def _add_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')

    def end_headers(self):
        """本地阅读器一律不缓存。

        不加这个的话，改完 index.html 浏览器还在用缓存里的旧页面，
        表现就是"新加的功能看不到、按钮没出现" —— 而且刷新一下可能
        还是旧的（普通刷新会发 If-Modified-Since 走 304）。
        排查起来很费劲，不如直接禁掉。
        """
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def _read_upload(self):
        """读取原始请求体。前端把文件字节直接放在 body 里，路径走请求头，
        这样服务端不用解析 multipart，纯标准库就够。"""
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b''

    def _handle_ingest(self):
        """录入一本小说：请求头带书名与相对路径，body 是章节文件原始字节。

        归档规则：main/<书名拼音首字母>/<书名>/<相对路径>
        """
        book = safe_name(urllib.parse.unquote(self.headers.get('X-Book-Name', '')))
        rel = safe_rel_path(urllib.parse.unquote(self.headers.get('X-Rel-Path', '')))
        if not book or not rel:
            self._send_error('缺少 X-Book-Name 或 X-Rel-Path')
            return

        letter = first_letter(book)
        dest_dir = os.path.join(NOVEL_DIR, letter, book)
        dest = os.path.join(dest_dir, *rel.split('/'))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            data = self._read_upload()
            with open(dest, 'wb') as f:
                f.write(data)
        except OSError as e:
            self._send_error(f'写入失败: {e}')
            return

        self._send_json({
            'ok': True,
            'book': book,
            'letter': letter,
            'rel': rel,
            'bytes': len(data),
        })

    def _handle_music_list(self):
        """列出 music/ 下的音频文件"""
        files = []
        if os.path.isdir(MUSIC_DIR):
            for name in sorted(os.listdir(MUSIC_DIR), key=lambda s: s.lower()):
                full = os.path.join(MUSIC_DIR, name)
                if os.path.isfile(full) and name.lower().endswith(AUDIO_EXTS):
                    files.append({'name': name, 'size': os.path.getsize(full)})
        self._send_json(files)

    def _handle_music_upload(self):
        """录入一首音乐：文件名走请求头，body 是原始字节"""
        name = safe_name(urllib.parse.unquote(self.headers.get('X-File-Name', '')))
        if not name:
            self._send_error('缺少 X-File-Name')
            return
        if not name.lower().endswith(AUDIO_EXTS):
            self._send_error(f'不支持的音频格式（支持 {", ".join(AUDIO_EXTS)}）')
            return

        os.makedirs(MUSIC_DIR, exist_ok=True)
        dest = os.path.join(MUSIC_DIR, name)
        # 重名不覆盖，自动加 (2)(3)
        stem, ext = os.path.splitext(name)
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(MUSIC_DIR, f'{stem}({n}){ext}')
            n += 1

        try:
            data = self._read_upload()
            with open(dest, 'wb') as f:
                f.write(data)
        except OSError as e:
            self._send_error(f'写入失败: {e}')
            return

        self._send_json({'ok': True, 'name': os.path.basename(dest), 'bytes': len(data)})

    def _handle_structure(self):
        """返回完整的字母-小说-章节树结构"""
        structure = {}
        if not os.path.isdir(NOVEL_DIR):
            self._send_json(structure)
            return
        for letter_dir in sorted(os.listdir(NOVEL_DIR)):
            letter_path = os.path.join(NOVEL_DIR, letter_dir)
            if not os.path.isdir(letter_path) or len(letter_dir) != 1:
                continue
            novels = {}
            for novel_name in sorted(os.listdir(letter_path)):
                novel_path = os.path.join(letter_path, novel_name)
                if not os.path.isdir(novel_path):
                    continue
                chapters = list_chapters(novel_path)
                if chapters:
                    novels[novel_name] = chapters
            if novels:
                structure[letter_dir] = novels
        self._send_json(structure)

    def _handle_books(self):
        """简化版：只返回字母 → 小说列表"""
        structure = {}
        if not os.path.isdir(NOVEL_DIR):
            self._send_json(structure)
            return
        for letter_dir in sorted(os.listdir(NOVEL_DIR)):
            letter_path = os.path.join(NOVEL_DIR, letter_dir)
            if not os.path.isdir(letter_path) or len(letter_dir) != 1:
                continue
            novels = []
            for novel_name in sorted(os.listdir(letter_path)):
                novel_path = os.path.join(letter_path, novel_name)
                if os.path.isdir(novel_path) and book_has_chapters(novel_path):
                    novels.append(novel_name)
            if novels:
                structure[letter_dir] = novels
        self._send_json(structure)

    def _handle_chapters(self, params):
        """返回某小说的章节列表"""
        book = safe_rel_path(params.get('book', [None])[0] or '')
        if not book:
            self._send_error("缺少 book 参数")
            return
        book_path = os.path.join(NOVEL_DIR, *book.split('/'))
        if not os.path.isdir(book_path):
            self._send_error("小说目录不存在")
            return
        self._send_json(list_chapters(book_path))

    def _handle_content(self, params):
        """返回章节内容"""
        book = safe_rel_path(params.get('book', [None])[0] or '')
        chapter = safe_rel_path(params.get('chapter', [None])[0] or '')
        if not book or not chapter:
            self._send_error("缺少参数")
            return
        # safe_rel_path 已经滤掉 ../ 与绝对路径，逐段拼接不会再逃出小说目录
        file_path = os.path.join(NOVEL_DIR, *book.split('/'), *chapter.split('/'))
        if not os.path.isfile(file_path):
            self._send_error("文件不存在")
            return
        try:
            encoding = detect_encoding(file_path)
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
        except Exception as e:
            self._send_error(f"读取文件失败: {str(e)}")
            return
        self._send_json({"content": content, "chapter": chapter})

    def _send_json(self, data):
        self.send_response(200)
        self._add_cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.wfile.write(body)

    def _send_error(self, msg):
        self.send_response(400)
        self._add_cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        body = json.dumps({"error": msg}, ensure_ascii=False).encode('utf-8')
        self.wfile.write(body)

    def send_head(self):
        """在标准静态文件服务之上补一个 Range 支持。

        音乐播放器拖动进度条、暂停后继续，浏览器都可能发 Range 请求；
        标准库的 SimpleHTTPRequestHandler 不支持，只能返回 200 全量内容。
        """
        # 先掐掉协商缓存。标准库会用 If-Modified-Since 回 304 让浏览器
        # 用旧缓存 —— 本地阅读器不需要这套，留着只会出现"改了前端却看不到"。
        for h in ('If-Modified-Since', 'If-None-Match'):
            if h in self.headers:
                del self.headers[h]

        range_header = self.headers.get('Range')
        if not range_header:
            return super().send_head()

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            fh = open(path, 'rb')
        except OSError:
            self.send_error(404, 'File not found')
            return None

        size = os.fstat(fh.fileno()).st_size
        m = re.match(r'bytes=(\d*)-(\d*)', range_header.strip())
        if not m or size == 0:
            fh.close()
            return super().send_head()

        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            fh.close()
            self.send_response(416)
            self.send_header('Content-Range', 'bytes */%d' % size)
            self.end_headers()
            return None

        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        fh.seek(start)
        return _SliceReader(fh, end - start + 1)

    def log_message(self, format, *args):
        """精简控制台输出"""
        msg = format % args
        if '/api/' in msg:
            print(f"  [API] {msg.split(' - - ')[1] if ' - - ' in msg else msg}")


def find_free_port(start=8080, end=8099):
    """查找可用端口"""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    return 8080


if __name__ == '__main__':
    # 静态文件从项目根目录提供（index.html / music/ 都在这里），
    # 小说目录单独用 NOVEL_DIR 拼绝对路径，两者互不影响。
    # 全部基于 __file__ 推导，所以整个文件夹搬到哪都能跑。
    os.chdir(ROOT_DIR)
    ensure_dirs()
    port = find_free_port()
    # 用 ThreadingHTTPServer 而不是 HTTPServer：
    # 浏览器打开一个页面会同时开好几条连接，单线程的 HTTPServer 只会
    # 一条一条处理，其中一条占住就会让整个页面一直转圈打不开。
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), NovelHandler)
    url = f'http://127.0.0.1:{port}'

    print('=' * 50)
    print('  小说阅读器已启动')
    print('=' * 50)
    print(f'  地址:     {url}')
    print(f'  项目目录: {ROOT_DIR}')
    print(f'  小说目录: {NOVEL_DIR}')
    print(f'  音乐目录: {MUSIC_DIR}')
    print(f'  按 Ctrl+C 停止服务器')
    print('=' * 50)

    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  已停止。')