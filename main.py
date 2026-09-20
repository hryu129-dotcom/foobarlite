# -*- coding: utf-8 -*-
"""
Foobar Lite Mobile  -  安卓版音乐搜索 / 下载器 (Kivy)

只做在线搜索 + 多链路下载,不含 Windows 专属功能
(声卡清单 / 驱动安装 / 输出设备切换 —— 这些手机上物理上不存在)。

界面:
  搜索页   输入关键词 -> 并发搜索多音源 -> 按歌曲聚合,展开可见各音源链路
  下载页   任务队列 / 实时进度 / 保存目录
  设置页   自定义解析接口(扩展下载链路),保存位置

依赖: kivy, requests, certifi
"""

import os
import sys
import json
import time
import threading

# 修复安卓上中文乱码问题 - 在导入kivy之前设置默认字体
from kivy.config import Config
from kivy.utils import platform

# 安卓上使用系统中文字体
if platform == "android":
    cn_fonts = [
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "/system/fonts/DroidSansFallback.ttf",
        "/system/fonts/NotoSansSC-Regular.otf",
        "/system/fonts/SourceHanSansCN-Regular.otf",
    ]
    for f in cn_fonts:
        if os.path.exists(f):
            Config.set('kivy', 'default_font', [f, f, f, f, f])
            break

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.progressbar import ProgressBar

import music_sources as ms

IS_ANDROID = (platform == "android")

# 配色(foobar2000 风格深色)
BG = (0.118, 0.122, 0.133, 1)
PANEL = (0.165, 0.173, 0.188, 1)
FG = (0.84, 0.85, 0.87, 1)
ACCENT = (0.184, 0.435, 0.749, 1)
OK_COLOR = (0.47, 0.86, 0.55, 1)
BAD_COLOR = (0.86, 0.51, 0.51, 1)


# =====================================================================
#  工具
# =====================================================================
def get_settings_path():
    base = App.get_running_app().user_data_dir if App.get_running_app() else os.getcwd()
    return os.path.join(base, "settings.json")


def load_settings():
    try:
        with open(get_settings_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"custom_apis": []}


def save_settings(data):
    try:
        with open(get_settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_download_dir():
    """选一个可写的下载目录。安卓上优先公共音乐目录(用户能找到),
    被沙箱拦下就退回应用私有目录,并把真实路径显示在界面上。"""
    if IS_ANDROID:
        candidates = ["/storage/emulated/0/Music/FoobarLite",
                      "/sdcard/Music/FoobarLite"]
        for c in candidates:
            try:
                os.makedirs(c, exist_ok=True)
                probe = os.path.join(c, ".w")
                with open(probe, "w") as f:
                    f.write("1")
                os.remove(probe)
                return c
            except Exception:
                continue
    base = App.get_running_app().user_data_dir if App.get_running_app() else os.path.expanduser("~")
    d = os.path.join(base, "downloads")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def request_android_permissions():
    """安卓上请求存储权限(写公共目录需要)"""
    if not IS_ANDROID:
        return
    try:
        from android.permissions import request_permissions, Permission
        request_permissions([Permission.READ_EXTERNAL_STORAGE,
                             Permission.WRITE_EXTERNAL_STORAGE])
    except Exception:
        pass


def toast(text):
    """轻量提示(Popup,安卓上没有原生 toast 也能用)"""
    content = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
    content.add_widget(Label(text=str(text), color=FG, halign="center"))
    p = Popup(title="提示", content=content, size_hint=(0.86, 0.34),
              background_color=PANEL, title_color=FG, separator_color=ACCENT)
    btn = Button(text="知道了", size_hint_y=None, height=dp(44),
                 background_normal="", background_color=ACCENT)
    btn.bind(on_release=p.dismiss)
    content.add_widget(btn)
    p.open()
    return p


# =====================================================================
#  下载任务
# =====================================================================
class DownloadTask(object):
    def __init__(self, song, cand, filename):
        self.song = song
        self.cand = cand
        self.filename = filename
        self.percent = 0
        self.status = "等待中"
        self.path = ""


class TaskRow(BoxLayout):
    """下载页里的一行:文件名 + 进度条 + 状态"""

    def __init__(self, task, **kw):
        super(TaskRow, self).__init__(orientation="vertical", size_hint_y=None,
                                      height=dp(64), padding=(dp(8), dp(4)),
                                      spacing=dp(2), **kw)
        self.task = task
        row = BoxLayout(size_hint_y=None, height=dp(24))
        self.name_lbl = Label(text=task.filename, color=FG, halign="left",
                              valign="middle", shorten=True, shorten_from="right",
                              font_size=dp(13))
        self.name_lbl.bind(size=self.name_lbl.setter("text_size"))
        row.add_widget(self.name_lbl)
        row.add_widget(Label(text=task.cand.get("label", ""), color=(0.55, 0.57, 0.6, 1),
                             size_hint_x=0.42, font_size=dp(11),
                             shorten=True, shorten_from="right"))
        self.add_widget(row)
        self.bar = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(12))
        self.add_widget(self.bar)
        self.state_lbl = Label(text=task.status, color=(0.6, 0.62, 0.66, 1),
                               size_hint_y=None, height=dp(18), font_size=dp(11),
                               halign="left", valign="middle")
        self.state_lbl.bind(size=self.state_lbl.setter("text_size"))
        self.add_widget(self.state_lbl)

    def refresh(self):
        self.bar.value = self.task.percent
        self.state_lbl.text = self.task.status
        if self.task.status.startswith("完成"):
            self.state_lbl.color = OK_COLOR
        elif self.task.status.startswith("失败"):
            self.state_lbl.color = BAD_COLOR
        else:
            self.state_lbl.color = (0.6, 0.62, 0.66, 1)


# =====================================================================
#  下载引擎(线程 + 回调到 UI 线程)
# =====================================================================
class Downloader(object):
    def __init__(self, app):
        self.app = app
        self.stop_flag = False
        self.running = False

    def start(self, tasks, folder):
        if self.running:
            toast("已有下载在进行中")
            return
        self.stop_flag = False
        self.running = True
        t = threading.Thread(target=self._run, args=(tasks, folder))
        t.daemon = True
        t.start()

    def _run(self, tasks, folder):
        ok = fail = 0
        for task in tasks:
            if self.stop_flag:
                break
            try:
                self._one(task, folder)
                ok += 1
            except Exception as e:
                task.status = "失败: {}".format(type(e).__name__)
                Clock.schedule_once(lambda dt, t=task: self.app.update_task(t), 0)
                fail += 1
        self.running = False
        Clock.schedule_once(lambda dt: self.app.on_downloads_done(ok, fail), 0)

    def _one(self, task, folder):
        url = task.cand["url"]
        target = os.path.join(folder, task.filename)
        tmp = target + ".part"
        task.status = "下载中"
        Clock.schedule_once(lambda dt, t=task: self.app.update_task(t), 0)
        r = ms.http_get(url, timeout=25, stream=True)
        try:
            if r.status_code >= 400:
                raise RuntimeError("HTTP {}".format(r.status_code))
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            last = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(65536):
                    if self.stop_flag:
                        break
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    if total and now - last > 0.25:
                        last = now
                        task.percent = int(got * 100 / total)
                        Clock.schedule_once(lambda dt, t=task: self.app.update_task(t), 0)
            if self.stop_flag:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                task.status = "已取消"
                Clock.schedule_once(lambda dt, t=task: self.app.update_task(t), 0)
                return
            os.replace(tmp, target)
            task.percent = 100
            task.path = target
            task.status = "完成 · {}".format(ms.human_size(got if not total else total))
            Clock.schedule_once(lambda dt, t=task: self.app.update_task(t), 0)
        finally:
            try:
                r.close()
            except Exception:
                pass


# =====================================================================
#  搜索结果:一首歌(含多条音源链路)
# =====================================================================
class SongGroup(BoxLayout):
    def __init__(self, group, app, **kw):
        super(SongGroup, self).__init__(orientation="vertical", size_hint_y=None,
                                        spacing=dp(2), padding=(dp(6), dp(3)), **kw)
        self.group = group
        self.app = app
        self.expanded = False
        self.variant_box = None
        self.bind(minimum_height=self.setter("height"))

        srcs = "/".join(ms.SOURCES[v["source"]]().name for v in group["variants"])
        title = "{}\n{}  ·  {}  ·  {} 条链路".format(
            group["name"], group["artist"] or "未知艺术家",
            srcs, len(group["variants"]))
        self.head = Button(text=title, size_hint_y=None, height=dp(58),
                           background_normal="", background_color=PANEL,
                           color=FG, font_size=dp(12), halign="left",
                           valign="middle")
        self.head.bind(size=self.head.setter("text_size"))
        self.head.bind(on_release=self.toggle)
        self.add_widget(self.head)

    def toggle(self, *_):
        if self.variant_box is None:
            self._build_variants()
        self.expanded = not self.expanded
        self.variant_box.height = self.variant_box.minimum_height if self.expanded else 0
        self.variant_box.opacity = 1 if self.expanded else 0

    def _build_variants(self):
        self.variant_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                     height=0, spacing=dp(2), opacity=0)
        self.variant_box.bind(minimum_height=self.variant_box.setter("height"))
        for v in self.group["variants"]:
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4),
                            padding=(dp(14), 0, 0, 0))
            row.add_widget(Label(text=ms.SOURCES[v["source"]]().name,
                                 color=(0.55, 0.72, 1, 1), size_hint_x=0.26,
                                 font_size=dp(12)))
            dl = Button(text="下载", size_hint_x=0.24, background_normal="",
                        background_color=ACCENT, font_size=dp(12))
            dl.bind(on_release=lambda *_, s=v: self.app.queue_song(s))
            row.add_widget(dl)
            play = Button(text="试听", size_hint_x=0.24, background_normal="",
                          background_color=(0.28, 0.30, 0.34, 1), font_size=dp(12))
            play.bind(on_release=lambda *_, s=v: self.app.preview_song(s))
            row.add_widget(play)
            row.add_widget(Label(text="", size_hint_x=0.2, font_size=dp(11)))
            self.variant_box.add_widget(row)
        self.add_widget(self.variant_box)


# =====================================================================
#  主界面
# =====================================================================
class RootUI(TabbedPanel):
    def __init__(self, app, **kw):
        super(RootUI, self).__init__(do_default_tab=False,
                                     tab_pos="top_mid", tab_width=dp(110),
                                     **kw)
        self.app = app
        self.tab_bg = BG
        self.build_search_tab()
        self.build_download_tab()
        self.build_settings_tab()

    # ---------------- 搜索页 ----------------
    def build_search_tab(self):
        tab = TabbedPanelItem(text="搜索")
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.kw_input = TextInput(text="", multiline=False, hint_text="歌曲名或歌手",
                                  background_color=PANEL, foreground_color=FG,
                                  cursor_color=ACCENT, font_size=dp(14))
        self.kw_input.bind(on_text_validate=lambda *_: self.app.do_search())
        row.add_widget(self.kw_input)
        self.src_spinner = Spinner(text="全部音源",
                                   values=["全部音源"] + [ms.SOURCES[k]().name for k in ms.SOURCE_ORDER],
                                   size_hint_x=0.34, background_normal="",
                                   background_color=PANEL, color=FG, font_size=dp(12))
        row.add_widget(self.src_spinner)
        box.add_widget(row)

        go = Button(text="搜索(多音源并发)", size_hint_y=None, height=dp(46),
                    background_normal="", background_color=ACCENT, font_size=dp(14))
        go.bind(on_release=lambda *_: self.app.do_search())
        box.add_widget(go)

        self.status_lbl = Label(text="输入关键词后点搜索", color=(0.6, 0.62, 0.66, 1),
                                size_hint_y=None, height=dp(22), font_size=dp(11),
                                halign="left", valign="middle")
        self.status_lbl.bind(size=self.status_lbl.setter("text_size"))
        box.add_widget(self.status_lbl)

        sv = ScrollView()
        self.results_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                     spacing=dp(4))
        self.results_box.bind(minimum_height=self.results_box.setter("height"))
        sv.add_widget(self.results_box)
        box.add_widget(sv)

        tab.add_widget(box)
        self.add_widget(tab)

    # ---------------- 下载页 ----------------
    def build_download_tab(self):
        tab = TabbedPanelItem(text="下载")
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        self.dir_lbl = Label(text="保存目录: " + self.app.download_dir,
                             color=(0.6, 0.62, 0.66, 1), size_hint_y=None,
                             height=dp(40), font_size=dp(11), halign="left",
                             valign="middle", shorten=True, shorten_from="right")
        self.dir_lbl.bind(size=self.dir_lbl.setter("text_size"))
        box.add_widget(self.dir_lbl)

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        b1 = Button(text="开始全部", background_normal="", background_color=ACCENT,
                    font_size=dp(13))
        b1.bind(on_release=lambda *_: self.app.start_downloads())
        b2 = Button(text="停止", background_normal="",
                    background_color=(0.28, 0.30, 0.34, 1), font_size=dp(13))
        b2.bind(on_release=lambda *_: self.app.stop_downloads())
        b3 = Button(text="清空", background_normal="",
                    background_color=(0.28, 0.30, 0.34, 1), font_size=dp(13))
        b3.bind(on_release=lambda *_: self.app.clear_tasks())
        for b in (b1, b2, b3):
            row.add_widget(b)
        box.add_widget(row)

        self.dl_summary = Label(text="", color=(0.6, 0.62, 0.66, 1), size_hint_y=None,
                                height=dp(22), font_size=dp(11), halign="left")
        self.dl_summary.bind(size=self.dl_summary.setter("text_size"))
        box.add_widget(self.dl_summary)

        sv = ScrollView()
        self.tasks_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                   spacing=dp(2))
        self.tasks_box.bind(minimum_height=self.tasks_box.setter("height"))
        sv.add_widget(self.tasks_box)
        box.add_widget(sv)

        tab.add_widget(box)
        self.add_widget(tab)

    # ---------------- 设置页 ----------------
    def build_settings_tab(self):
        tab = TabbedPanelItem(text="设置")
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        box.add_widget(Label(text="自定义解析接口(每行一个,扩展下载链路)",
                             color=(0.55, 0.72, 1, 1), size_hint_y=None,
                             height=dp(24), font_size=dp(12), halign="left"))
        box.add_widget(Label(
            text="格式:方法|名称|URL模板\n"
                 "方法 text = 请求后自动提取直链,direct = 模板本身即直链\n"
                 "占位符 {id} {mid} {hash} {keyword} {name}\n"
                 "例: text|我的接口|http://api/?id={id}",
            color=(0.6, 0.62, 0.66, 1), size_hint_y=None, height=dp(76),
            font_size=dp(11), halign="left"))
        self.api_input = TextInput(text="", multiline=True, background_color=PANEL,
                                   foreground_color=FG, cursor_color=ACCENT,
                                   font_size=dp(12))
        box.add_widget(self.api_input)

        save = Button(text="保存设置", size_hint_y=None, height=dp(44),
                      background_normal="", background_color=ACCENT)
        save.bind(on_release=lambda *_: self.app.save_custom_apis())
        box.add_widget(save)
        box.add_widget(Label(text="保存目录:" + self.app.download_dir,
                             color=(0.6, 0.62, 0.66, 1), size_hint_y=None,
                             height=dp(40), font_size=dp(11), shorten=True))
        box.add_widget(Label(text="Foobar Lite Mobile v{}   (仅在线搜索 + 下载)".format(ms.APP_VER),
                             color=(0.45, 0.47, 0.5, 1), size_hint_y=None,
                             height=dp(30), font_size=dp(11)))
        tab.add_widget(box)
        self.add_widget(tab)


# =====================================================================
#  App
# =====================================================================
class MobileApp(App):
    title = "Foobar Lite Mobile"
    name = "foobarlite"          # 显式命名,保证 user_data_dir 位置可预期

    def build(self):
        self.settings = load_settings()
        self.download_dir = get_download_dir()
        self.downloader = Downloader(self)
        self.task_rows = []
        self.tasks = []
        self.playing = None
        if IS_ANDROID:
            Clock.schedule_once(lambda dt: request_android_permissions(), 1)
        self.root_ui = RootUI(self)
        Clock.schedule_once(lambda dt: self._load_apis_into_ui(), 0)
        return self.root_ui

    def _load_apis_into_ui(self):
        lines = []
        for a in self.settings.get("custom_apis", []):
            lines.append("{}|{}|{}".format(a.get("method", "text"),
                                           a.get("name", ""), a.get("url", "")))
        self.root_ui.api_input.text = "\n".join(lines)

    # ---------------- 自定义接口 ----------------
    def save_custom_apis(self):
        apis = []
        for line in (self.root_ui.api_input.text or "").splitlines():
            line = line.strip()
            if not line or line.count("|") < 2:
                continue
            parts = line.split("|")
            method = parts[0].strip().lower()
            if method not in ("text", "direct"):
                method = "text"
            apis.append({"method": method, "name": parts[1].strip(),
                         "url": "|".join(parts[2:]).strip()})
        self.settings["custom_apis"] = apis
        if save_settings(self.settings):
            toast("已保存 {} 个自定义接口".format(len(apis)))
        else:
            toast("保存失败")

    # ---------------- 搜索 ----------------
    def do_search(self):
        kw = (self.root_ui.kw_input.text or "").strip()
        if not kw:
            toast("请输入歌曲名或歌手")
            return
        name = self.root_ui.src_spinner.text
        if name == "全部音源":
            keys = list(ms.SOURCE_ORDER)
        else:
            keys = [k for k in ms.SOURCE_ORDER if ms.SOURCES[k]().name == name]
        self.root_ui.results_box.clear_widgets()
        self.root_ui.status_lbl.text = "正在搜索 {} 个音源...".format(len(keys))
        self._pending = len(keys)
        self._progress = {}
        threading.Thread(target=self._search_worker, args=(kw, keys), daemon=True).start()

    def _search_worker(self, kw, keys):
        def on_result(key, results, error):
            self._progress[key] = "{}条".format(len(results)) if not error else "失败"
            Clock.schedule_once(lambda dt: self._update_search_status(), 0)

        groups = ms.search_all(kw, keys, limit=20, on_result=on_result, timeout=45)
        Clock.schedule_once(lambda dt: self._show_results(groups), 0)

    def _update_search_status(self):
        parts = ["{}: {}".format(ms.SOURCES[k]().name, self._progress[k])
                 for k in ms.SOURCE_ORDER if k in self._progress]
        self.root_ui.status_lbl.text = "  ".join(parts)

    def _show_results(self, groups):
        box = self.root_ui.results_box
        box.clear_widgets()
        if not groups:
            box.add_widget(Label(text="没有找到结果", color=(0.6, 0.62, 0.66, 1),
                                 size_hint_y=None, height=dp(40)))
            self.root_ui.status_lbl.text = "没有找到结果"
            return
        for g in groups:
            box.add_widget(SongGroup(g, self))
        self.root_ui.status_lbl.text = "共 {} 首(点一下展开各音源链路)".format(len(groups))

    # ---------------- 队列 / 下载 ----------------
    def queue_song(self, song):
        """解析该音源的多条链路,挑第一条可用直链入队"""
        if song["source"] == "migu" and not song.get("sid"):
            toast("该音源无有效 ID")
            return
        toast("正在解析链路...")

        def work():
            cands = ms.resolve_song(song, self.settings.get("custom_apis"))
            best = ms.best_direct(cands)
            Clock.schedule_once(lambda dt: self._queued(song, cands, best), 0)

        threading.Thread(target=work, daemon=True).start()

    def _queued(self, song, cands, best):
        if not best:
            good = [c for c in cands if c.get("kind") == "web"]
            msg = "没有可用的直链(可能需会员或接口受限)"
            if good:
                msg += "\n可复制链接到浏览器:\n" + good[0]["url"]
            toast(msg)
            return
        ext = ms.guess_ext(best["url"], best.get("quality", ""))
        fn = "{}_{}{}".format(ms.safe_filename(song.get("name", "")),
                              ms.safe_filename(song.get("artist", "")), ext)
        task = DownloadTask(song, best, fn)
        self.tasks.append(task)
        row = TaskRow(task)
        self.task_rows.append(row)
        self.root_ui.tasks_box.add_widget(row)
        toast("已加入下载队列:{}".format(fn))
        self.root_ui.dl_summary.text = "队列 {} 项".format(len(self.tasks))

    def update_task(self, task):
        for row in self.task_rows:
            if row.task is task:
                row.refresh()
                break

    def start_downloads(self):
        if not self.tasks:
            toast("下载队列为空")
            return
        self.downloader.start(list(self.tasks), self.download_dir)

    def stop_downloads(self):
        self.downloader.stop_flag = True
        toast("正在停止...")

    def clear_tasks(self):
        if self.downloader.running:
            toast("请先停止下载")
            return
        self.tasks = []
        self.task_rows = []
        self.root_ui.tasks_box.clear_widgets()
        self.root_ui.dl_summary.text = ""

    def on_downloads_done(self, ok, fail):
        self.root_ui.dl_summary.text = "完成 {} 个 / 失败 {} 个".format(ok, fail)
        toast("下载结束:成功 {} / 失败 {}".format(ok, fail))

    # ---------------- 试听(实验) ----------------
    def preview_song(self, song):
        toast("正在准备试听...")

        def work():
            cands = ms.resolve_song(song, self.settings.get("custom_apis"))
            best = ms.best_direct(cands)
            if not best:
                Clock.schedule_once(lambda dt: toast("没有可试听的直链"), 0)
                return
            try:
                from kivy.core.audio import SoundLoader
                ext = ms.guess_ext(best["url"], best.get("quality", ""))
                tmp = os.path.join(self.user_data_dir, "preview" + ext)
                if not os.path.exists(tmp):
                    r = ms.http_get(best["url"], timeout=30, stream=True)
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(65536):
                            if chunk:
                                f.write(chunk)
                    r.close()

                def play(dt):
                    snd = SoundLoader.load(tmp)
                    if snd is None:
                        toast("当前平台无法试听该格式")
                        return
                    if self.playing is not None:
                        try:
                            self.playing.stop()
                        except Exception:
                            pass
                    self.playing = snd
                    snd.play()
                    toast("正在试听:{}".format(song.get("name", "")))
                Clock.schedule_once(play, 0)
            except Exception as e:
                Clock.schedule_once(lambda dt: toast("试听失败:{}".format(type(e).__name__)), 0)

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    MobileApp().run()
