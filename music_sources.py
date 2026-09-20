# -*- coding: utf-8 -*-
"""
music_sources.py  -  跨平台音乐音源模块(桌面版 / 安卓版共用)

设计原则:
  * 只依赖 requests,不含任何 GUI 代码
  * 不含任何 Windows 专属代码(无 winreg / ctypes / pnputil / 注册表)
  * 因此可以直接跑在 Windows / Linux / Android 上

提供:
  SOURCES / SOURCE_ORDER          音源注册表
  search_all(keyword, keys, limit) 并发聚合搜索
  validate_url(url)                校验直链可用性
  apply_custom_apis(...)           自定义解析接口(扩展下载链路)
  guess_ext / norm_key / human_size / fmt_duration  工具函数
"""

import os
import re
import json
import hashlib
import threading
import urllib.parse

import requests

# ---------------------------------------------------------------- 常量
APP_NAME = "Foobar Lite Mobile"
APP_VER = "1.0"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

DEFAULT_HEADERS = {"User-Agent": UA}

AUDIO_EXTS = (".mp3", ".flac", ".wav", ".ape", ".ogg", ".m4a", ".aac", ".wma", ".opus")

# 反斜杠用 chr(92) 生成,避免源码里出现转义字符导致的可读性问题
_BS = chr(92)

# 从 URL 里识别音频扩展名(运行时构建,等价于 \.(mp3|flac|...)(?=[?#]|$))
_EXT_RE = re.compile(
    "(" + "|".join(e.replace(".", _BS + ".") for e in AUDIO_EXTS) + ")(?=[?#]|$)",
    re.I)


# ---------------------------------------------------------------- 工具
def fmt_duration(seconds, show_zero=False):
    """秒 -> mm:ss;show_zero=True 时 0 显示 00:00(播放进度用)"""
    try:
        seconds = int(seconds)
    except Exception:
        return "--:--"
    if seconds <= 0:
        return "00:00" if show_zero else "--:--"
    return "{:02d}:{:02d}".format(seconds // 60, seconds % 60)


def human_size(n):
    try:
        n = float(n)
    except Exception:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "{:.1f}{}".format(n, unit)
        n /= 1024
    return "{:.1f}TB".format(n)


_STRIP_CHARS = set(" \t\r\n-_.,!?'" + chr(34) + chr(0x2019) + chr(0x3002) + chr(0xFF01))


def norm_key(text):
    """归一化:用于把不同音源的同一首歌聚合到一起。
    规则:小写 -> 去掉括号内版本说明 -> 去掉 feat. 之后内容 -> 去掉空白与标点
    刻意不用正则,避免转义带来的隐患。"""
    if not text:
        return ""
    t = str(text).lower()
    # 去掉 (...) （...） [...] 里的内容
    out, depth = [], 0
    for ch in t:
        if ch in "(（[":
            depth += 1
            continue
        if ch in ")）]":
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    t = "".join(out)
    # feat./ft. 之后视为版本说明
    for marker in ("feat.", "feat ", " ft.", " ft "):
        idx = t.find(marker)
        if idx > 0:
            t = t[:idx]
    # 去掉空白与标点
    t = "".join(ch for ch in t if ch not in _STRIP_CHARS)
    return t.strip()

def _unescape_url(u):
    """把 JSON 里可能残留的 \\/ 还原成 /"""
    return (u or "").replace(_BS + "/", "/")


def guess_ext(url, quality_hint=""):
    """推断下载文件扩展名"""
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in AUDIO_EXTS:
        return ext
    m = _EXT_RE.search(url or "")
    if m:
        return "." + m.group(1).lower().lstrip(".")
    hint = (quality_hint or "").lower()
    qh = quality_hint or ""
    if "flac" in hint or "lossless" in hint or "无损" in qh:
        return ".flac"
    if "m4a" in hint:
        return ".m4a"
    return ".mp3"


def safe_filename(name):
    """把歌名/歌手处理成合法文件名"""
    bad = _BS + '/:*?"<>|'
    out = []
    for ch in str(name):
        out.append("_" if ch in bad else ch)
    return "".join(out).strip() or "audio"


def http_get(url, headers=None, timeout=10, **kw):
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    return requests.get(url, headers=h, timeout=timeout, **kw)


def validate_url(url, timeout=8):
    """校验链接是否为可用音频直链,返回 (ok, note, size)"""
    if not url:
        return False, "空链接", 0
    try:
        r = http_get(url, headers={"Range": "bytes=0-1"}, timeout=timeout,
                     stream=True, allow_redirects=True)
        status = r.status_code
        ct = (r.headers.get("Content-Type") or "").lower()
        cl = r.headers.get("Content-Length")
        final = r.url
        r.close()
        if status >= 400:
            return False, "HTTP {}".format(status), 0
        if final.endswith("/404") or "/404" in final:
            return False, "资源不存在/需会员", 0
        base_ct = ct.split(";")[0].strip()
        size = int(cl) if (cl and cl.isdigit()) else 0
        if size < 1024:
            try:
                hr = requests.head(url, headers=DEFAULT_HEADERS, timeout=timeout,
                                   allow_redirects=True)
                hcl = hr.headers.get("Content-Length")
                if hcl and hcl.isdigit() and int(hcl) > size:
                    size = int(hcl)
            except Exception:
                pass
        if "audio" in ct or "octet-stream" in ct or "mpegurl" in ct:
            return True, "可用 · {}".format(base_ct), size
        if "html" in ct or "json" in ct or "plain" in ct:
            return False, "非音频({})".format(base_ct or "html"), size
        if size > 65536:
            return True, "可用 · {}".format(base_ct or "未知"), size
        return False, "可疑({})".format(base_ct or "未知"), size
    except requests.exceptions.Timeout:
        return False, "连接超时", 0
    except Exception as e:
        return False, type(e).__name__, 0


# ---------------------------------------------------------------- 音源
class BaseSource(object):
    key = "base"
    name = "基类"
    home = ""

    def search(self, keyword, limit=20):
        raise NotImplementedError

    def resolve(self, song):
        raise NotImplementedError


class NeteaseSource(BaseSource):
    key = "netease"
    name = "网易云"
    home = "https://music.163.com"

    def search(self, keyword, limit=20):
        url = ("https://music.163.com/api/search/get/web?s={}&type=1&offset=0"
               "&total=true&limit={}").format(urllib.parse.quote(keyword), limit)
        data = http_get(url, headers={"Referer": self.home + "/"}, timeout=12).json()
        out = []
        res = (data or {}).get("result") or {}
        for s in (res.get("songs") or []):
            artists = s.get("artists") or []
            out.append({
                "source": self.key,
                "name": s.get("name", ""),
                "artist": ", ".join(a.get("name", "") for a in artists if a.get("name")),
                "album": (s.get("album") or {}).get("name", ""),
                "duration": int(s.get("duration", 0) or 0) // 1000,
                "sid": str(s.get("id", "")),
                "extra": {},
            })
        return out

    def resolve(self, song):
        sid = song.get("sid", "")
        if not sid:
            return []
        cands = []
        for scheme in ("https", "http"):
            cands.append({
                "label": "网易云直链 · {} CDN".format(scheme.upper()),
                "url": "{}://music.163.com/song/media/outer/url?id={}.mp3".format(scheme, sid),
                "kind": "direct",
                "quality": "官方",
            })
        cands.append({
            "label": "网易云网页版",
            "url": "https://music.163.com/#/song?id={}".format(sid),
            "kind": "web",
            "quality": "-",
        })
        return cands


class KugouSource(BaseSource):
    key = "kugou"
    name = "酷狗"
    home = "https://www.kugou.com"

    def search(self, keyword, limit=20):
        url = ("https://songsearch.kugou.com/song_search_v2?keyword={}&page=1"
               "&pagesize={}").format(urllib.parse.quote(keyword), limit)
        data = http_get(url, timeout=12).json()
        out = []
        for s in (((data or {}).get("data") or {}).get("lists") or []):
            out.append({
                "source": self.key,
                "name": s.get("SongName", ""),
                "artist": s.get("SingerName", ""),
                "album": s.get("AlbumName", ""),
                "duration": int(s.get("Duration", 0) or 0),
                "sid": s.get("FileHash", ""),
                "extra": {"album_id": s.get("AlbumID", "")},
            })
        return out

    def resolve(self, song):
        h = song.get("sid", "")
        if not h:
            return []
        cands = []
        key = hashlib.md5((h + "kgcloudv2").encode()).hexdigest()
        seen_q = {}
        for br in ("sq", "hq", ""):
            try:
                u = ("https://trackercdn.kugou.com/i/v2/?key={}&hash={}&br={}"
                     "&appid=1005&pid=2&cmd=25&behavior=play").format(key, h, br)
                j = http_get(u, headers={"Referer": self.home + "/"}, timeout=10).json()
                if j.get("status") != 1:
                    continue
                extn = (j.get("extName") or "mp3").lower()
                br_k = int((j.get("bitRate") or 0) // 1000)
                qname = "{} {}k".format(extn.upper(), br_k).strip()
                key_q = (extn, br_k)
                for link in (j.get("url") or []):
                    if seen_q.get(key_q, 0) >= 2:
                        break
                    seen_q[key_q] = seen_q.get(key_q, 0) + 1
                    cands.append({
                        "label": "酷狗直链 · {}".format(qname)
                                 + (" #{}".format(seen_q[key_q]) if seen_q[key_q] > 1 else ""),
                        "url": _unescape_url(link),
                        "kind": "direct",
                        "quality": extn,
                    })
            except Exception:
                continue
        album_id = (song.get("extra") or {}).get("album_id", "")
        if album_id:
            try:
                u = ("https://wwwapi.kugou.com/yy/index.php?r=play/getdata&hash={}"
                     "&album_id={}&dfid=&mid=&platid=4").format(h, album_id)
                j = http_get(u, headers={"Referer": self.home + "/"}, timeout=8).json()
                play = ((j.get("data") or {}).get("play_url") or "")
                if play:
                    cands.append({"label": "酷狗移动端接口", "url": play,
                                  "kind": "mirror", "quality": "备用"})
            except Exception:
                pass
        cands.append({"label": "酷狗网页版",
                      "url": "https://www.kugou.com/song/#hash={}".format(h),
                      "kind": "web", "quality": "-"})
        return cands


class QQSource(BaseSource):
    key = "qq"
    name = "QQ音乐"
    home = "https://y.qq.com"

    def search(self, keyword, limit=20):
        url = ("https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w={}&format=json"
               "&p=1&n={}").format(urllib.parse.quote(keyword), limit)
        data = http_get(url, headers={"Referer": self.home + "/"}, timeout=12).json()
        out = []
        lst = (((data or {}).get("data") or {}).get("song") or {}).get("list") or []
        for s in lst:
            singers = s.get("singer") or []
            out.append({
                "source": self.key,
                "name": s.get("songname", ""),
                "artist": ", ".join(x.get("name", "") for x in singers if x.get("name")),
                "album": s.get("albumname", ""),
                "duration": int(s.get("interval", 0) or 0),
                "sid": s.get("songmid", ""),
                "extra": {},
            })
        return out

    def resolve(self, song):
        mid = song.get("sid", "")
        if not mid:
            return []
        cands = []
        for fmt, ext in (("C400", "m4a"), ("M500", "mp3")):
            fn = "{}{}.{}".format(fmt, mid, ext)
            try:
                u = ("https://c.y.qq.com/base/fcgi-bin/fcg_music_express_mobile3.fcg?"
                     "format=json&platform=yqq&cid=205361747&songmid={}&filename={}"
                     "&guid=10000").format(mid, fn)
                j = http_get(u, headers={"Referer": self.home + "/"}, timeout=8).json()
                items = (j.get("data") or {}).get("items") or []
                vkey = items[0].get("vkey", "") if items else ""
                if vkey:
                    cands.append({
                        "label": "QQ音乐直链 · {}".format(ext.upper()),
                        "url": ("https://dl.stream.qqmusic.qq.com/{}?vkey={}"
                                "&guid=10000&uin=0&fromtag=66").format(fn, vkey),
                        "kind": "direct",
                        "quality": ext.upper(),
                    })
            except Exception:
                continue
        cands.append({"label": "QQ音乐网页版",
                      "url": "https://y.qq.com/n/ryqq/songDetail/{}".format(mid),
                      "kind": "web", "quality": "-"})
        return cands


class MiguSource(BaseSource):
    key = "migu"
    name = "咪咕"
    home = "https://music.migu.cn"

    def search(self, keyword, limit=20):
        url = ("https://m.music.migu.cn/migu/remoting/scr_search_tag?keyword={}"
               "&type=2&pgc=1&rows={}").format(urllib.parse.quote(keyword), limit)
        r = http_get(url, headers={"Referer": "https://m.music.migu.cn/"}, timeout=12)
        ctype = r.headers.get("Content-Type") or ""
        if "json" not in ctype:
            raise RuntimeError("接口已限流/改版")
        data = r.json()
        out = []
        for s in (data.get("musics") or []):
            out.append({
                "source": self.key,
                "name": s.get("songName", ""),
                "artist": s.get("singerName", ""),
                "album": s.get("albumName", ""),
                "duration": int(s.get("duration", 0) or 0) // 1000,
                "sid": str(s.get("id") or s.get("copyrightId") or ""),
                "extra": {},
            })
        return out

    def resolve(self, song):
        sid = song.get("sid", "")
        cands = []
        if sid:
            try:
                u = ("https://music.migu.cn/v3/api/music/audioPlayer/getPlayInfo"
                     "?dataType=2&copyrightId={}").format(sid)
                j = http_get(u, headers={"Referer": self.home + "/"}, timeout=8).json()
                link = ((j.get("data") or {}).get("playUrl")) or ""
                if link:
                    cands.append({"label": "咪咕直链", "url": link,
                                  "kind": "direct", "quality": "官方"})
            except Exception:
                pass
        cands.append({"label": "咪咕网页版",
                      "url": "https://music.migu.cn/v3/music/song/{}".format(sid),
                      "kind": "web", "quality": "-"})
        return cands


SOURCES = {
    "netease": NeteaseSource,
    "kugou": KugouSource,
    "qq": QQSource,
    "migu": MiguSource,
}
SOURCE_ORDER = ["netease", "kugou", "qq", "migu"]


# ---------------------------------------------------------------- 聚合搜索
def search_one(key, keyword, limit=20):
    """搜索单个音源,返回 (key, results, error)"""
    try:
        return key, SOURCES[key]().search(keyword, limit), ""
    except Exception as e:
        return key, [], str(e) or type(e).__name__


def search_all(keyword, keys=None, limit=20, on_result=None, timeout=40):
    """
    并发搜索多个音源并按 歌名+歌手 聚合分组。

    返回 groups: [{"name","artist","album","duration","variants":[song,...]}]
    on_result(key, results, error) 每完成一个音源就回调一次(用于刷新界面)
    """
    keys = list(keys or SOURCE_ORDER)
    results = {}
    lock = threading.Lock()
    threads = []

    def worker(k):
        res = search_one(k, keyword, limit)
        with lock:
            results[res[0]] = res
        if on_result:
            try:
                on_result(*res)
            except Exception:
                pass

    for k in keys:
        t = threading.Thread(target=worker, args=(k,))
        t.daemon = True
        t.start()
        threads.append(t)

    deadline = timeout
    for t in threads:
        t.join(deadline)
        deadline = max(0.5, deadline / 2)

    # 按 歌名+歌手 聚合
    groups = {}
    order = []
    for k in keys:
        _, songs, _ = results.get(k, (k, [], "no result"))
        for s in songs:
            gk = norm_key(s["name"]) + "|" + norm_key(s["artist"])
            if gk not in groups:
                groups[gk] = {"name": s["name"], "artist": s["artist"],
                              "album": s["album"], "duration": s["duration"],
                              "variants": []}
                order.append(gk)
            groups[gk]["variants"].append(s)

    def score(g):
        direct = sum(1 for v in g["variants"] if v["source"] in ("netease", "kugou"))
        return (-len(g["variants"]), -direct, norm_key(g["name"]))

    return sorted((groups[gk] for gk in order), key=score)


def resolve_song(song, custom_apis=None, do_validate=True):
    """解析一首歌的全部下载链路,可选逐条校验。返回候选列表"""
    cands = []
    try:
        cls = SOURCES.get(song.get("source"))
        if cls:
            cands = cls().resolve(song)
    except Exception:
        cands = []
    cands = apply_custom_apis(cands, song, custom_apis or [])

    seen, uniq = set(), []
    for c in cands:
        u = c.get("url", "")
        if not u or u in seen:
            continue
        seen.add(u)
        uniq.append(c)

    for c in uniq:
        if do_validate and c.get("kind") in ("direct", "mirror"):
            ok, note, size = validate_url(c["url"])
            c["ok"], c["note"], c["size"] = ok, note, size
        elif c.get("kind") == "web":
            c["ok"], c["note"], c["size"] = None, "网页链接", 0
    return uniq


def best_direct(cands):
    """挑出第一条可用的直链"""
    for c in cands:
        if c.get("kind") in ("direct", "mirror") and c.get("ok"):
            return c
    return None


# ---------------------------------------------------------------- 自定义接口
_WS = chr(9) + chr(10) + chr(13) + " "
_URL_RE = re.compile('https?://[^"' + "'" + _WS + "<>]+")


def _iter_strings(obj):
    """递归收集 JSON 里的所有字符串值"""
    if isinstance(obj, dict):
        for v in obj.values():
            for x in _iter_strings(v):
                yield x
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            for x in _iter_strings(v):
                yield x
    elif isinstance(obj, str):
        yield obj


def extract_audio_link(body):
    """从接口返回的 JSON / HTML / 纯文本里提取音频直链。
    先按 JSON 解析,再退回正则抓 http 链接;优先返回带音频扩展名的。"""
    body = body or ""
    # 1) 按 JSON 解析
    try:
        data = json.loads(body)
    except Exception:
        data = None
    if data is not None:
        pool = [s for s in _iter_strings(data) if s.startswith("http")]
        for s in pool:
            if _EXT_RE.search(s):
                return s
        if pool:
            return pool[0]
    # 2) 从原始文本里抓 http 链接
    urls = [m.group(0) for m in _URL_RE.finditer(body)]
    for u in urls:
        if _EXT_RE.search(u):
            return u
    return urls[0] if urls else ""


def apply_custom_apis(cands, song, custom_apis):
    """用户自定义解析接口:为每首歌增加额外的下载链路。
    method=direct 表示模板本身即音频直链;method=text 表示请求后自动提取。"""
    if not custom_apis:
        return cands
    sid = song.get("sid", "")
    ctx = {
        "id": sid, "mid": sid, "hash": sid,
        "keyword": urllib.parse.quote(song.get("name", "")),
        "name": urllib.parse.quote(song.get("name", "")),
    }
    for api in custom_apis:
        try:
            url = (api.get("url") or "").format(**ctx)
            if not url:
                continue
            label = api.get("name") or "自定义接口"
            method = (api.get("method") or "text").lower()
            if method == "direct":
                cands.append({"label": label, "url": url, "kind": "mirror",
                              "quality": "自定义"})
                continue
            r = http_get(url, timeout=8)
            ct = (r.headers.get("Content-Type") or "").lower()
            if "audio" in ct or "octet" in ct:
                cands.append({"label": label, "url": url, "kind": "direct",
                              "quality": "自定义"})
                continue
            link = extract_audio_link(r.text)
            if link:
                cands.append({"label": label, "url": link, "kind": "direct",
                              "quality": "自定义"})
        except Exception:
            continue
    return cands
