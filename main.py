# -*- coding: utf-8 -*-
"""
Foobar Lite Mobile  -  安卓版音乐播放器 (Kivy)
功能：本地扫描、在线搜索、试听播放、下载、播放历史、播放列表
"""

import os
import sys
import json
import time
import threading
import random

# 修复安卓上中文乱码问题
from kivy.config import Config
from kivy.utils import platform

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
from kivy.core.audio import SoundLoader

import music_sources as ms

IS_ANDROID = (platform == "android")

# 配色
BG = (0.118, 0.122, 0.133, 1)
PANEL = (0.165, 0.173, 0.188, 1)
FG = (0.84, 0.85, 0.87, 1)
ACCENT = (0.184, 0.435, 0.749, 1)
OK_COLOR = (0.47, 0.86, 0.55, 1)
BAD_COLOR = (0.86, 0.51, 0.51, 1)
GRAY = (0.6, 0.62, 0.66, 1)

AUDIO_EXTS = ('.mp3', '.flac', '.wav', '.ape', '.ogg', '.m4a', '.aac', '.wma', '.opus')


def get_settings_path():
    base = App.get_running_app().user_data_dir if App.get_running_app() else os.getcwd()
    return os.path.join(base, "settings.json")


def load_settings():
    try:
        with open(get_settings_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"custom_apis": [], "history": [], "playlist": []}


def save_settings(data):
    try:
        with open(get_settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def request_android_permissions():
    if not IS_ANDROID:
        return
    try:
        from android.permissions import request_permissions, Permission
        request_permissions([Permission.READ_EXTERNAL_STORAGE,
                             Permission.WRITE_EXTERNAL_STORAGE])
    except Exception:
        pass


def toast(text):
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
#  本地文件扫描线程
# =====================================================================
class ScanThread(threading.Thread):
    def __init__(self, app, root_dir="/storage/emulated/0"):
        super().__init__()
        self.app = app
        self.root_dir = root_dir
        self.running = True

    def run(self):
        files = []
        skip_dirs = {'Android', '.git', 'DCIM', 'Movies', 'WhatsApp'}

        for root, dirs, filenames in os.walk(self.root_dir):
            if not self.running:
                break
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs]
            for f in filenames:
                if f.lower().endswith(AUDIO_EXTS):
                    files.append(os.path.join(root, f))
                    if len(files) % 20 == 0:
                        Clock.schedule_once(lambda dt, c=len(files): self.app.scan_progress(c), 0)

        Clock.schedule_once(lambda dt: self.app.scan_finished(files), 0)


# =====================================================================
#  本地歌曲列表项
# =====================================================================
class LocalSongRow(BoxLayout):
    def __init__(self, filepath, app, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None,
                         height=dp(56), padding=(dp(8), dp(4)), spacing=dp(6), **kw)
        self.filepath = filepath
        self.app = app
        filename = os.path.basename(filepath)
        name, ext = os.path.splitext(filename)

        self.name_lbl = Label(text=name, color=FG, halign="left", valign="middle",
                              shorten=True, shorten_from="right", font_size=dp(13))
        self.name_lbl.bind(size=self.name_lbl.setter("text_size"))
        self.add_widget(self.name_lbl)

        self.ext_lbl = Label(text=ext.lstrip('.').upper(), color=GRAY,
                             size_hint_x=0.18, font_size=dp(10), valign="middle")
        self.add_widget(self.ext_lbl)

        play_btn = Button(text="▶", size_hint_x=0.15, background_normal="",
                          background_color=ACCENT, font_size=dp(14))
        play_btn.bind(on_release=lambda *_: self.app.play_local_file(filepath))
        self.add_widget(play_btn)


# =====================================================================
#  搜索结果行
# =====================================================================
class SongGroup(BoxLayout):
    def __init__(self, group, app, **kw):
        super().__init__(orientation="vertical", size_hint_y=None,
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
            play.bind(on_release=lambda *_, s=v: self.app.preview_online(s))
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
                                     tab_pos="top_mid", tab_width=dp(90),
                                     **kw)
        self.app = app
        self.tab_bg = BG
        self.build_local_tab()
        self.build_search_tab()
        self.build_history_tab()
        self.build_settings_tab()

    # ---------------- 本地音乐页 ----------------
    def build_local_tab(self):
        tab = TabbedPanelItem(text="本地音乐")
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        scan_btn = Button(text="扫描本机音频", background_normal="",
                          background_color=ACCENT, font_size=dp(14))
        scan_btn.bind(on_release=lambda *_: self.app.scan_local())
        row.add_widget(scan_btn)
        clear_btn = Button(text="清空列表", background_normal="",
                           background_color=(0.28, 0.30, 0.34, 1), font_size=dp(13))
        clear_btn.bind(on_release=lambda *_: self.app.clear_local_list())
        row.add_widget(clear_btn)
        box.add_widget(row)

        self.scan_status = Label(text="点击扫描本机音频文件", color=GRAY,
                                  size_hint_y=None, height=dp(22), font_size=dp(11),
                                  halign="left", valign="middle")
        self.scan_status.bind(size=self.scan_status.setter("text_size"))
        box.add_widget(self.scan_status)

        sv = ScrollView()
        self.local_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.local_box.bind(minimum_height=self.local_box.setter("height"))
        sv.add_widget(self.local_box)
        box.add_widget(sv)

        tab.add_widget(box)
        self.add_widget(tab)

    # ---------------- 在线搜索页 ----------------
    def build_search_tab(self):
        tab = TabbedPanelItem(text="在线搜索")
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

        go = Button(text="搜索", size_hint_y=None, height=dp(46),
                    background_normal="", background_color=ACCENT, font_size=dp(14))
        go.bind(on_release=lambda *_: self.app.do_search())
        box.add_widget(go)

        self.status_lbl = Label(text="输入关键词后点搜索", color=GRAY,
                                size_hint_y=None, height=dp(22), font_size=dp(11),
                                halign="left", valign="middle")
        self.status_lbl.bind(size=self.status_lbl.setter("text_size"))
        box.add_widget(self.status_lbl)

        sv = ScrollView()
        self.results_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.results_box.bind(minimum_height=self.results_box.setter("height"))
        sv.add_widget(self.results_box)
        box.add_widget(sv)

        tab.add_widget(box)
        self.add_widget(tab)

    # ---------------- 播放历史页 ----------------
    def build_history_tab(self):
        tab = TabbedPanelItem(text="播放历史")
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        self.history_count = Label(text="播放历史 (0)", color=GRAY,
                                   size_hint_y=None, height=dp(24), font_size=dp(12),
                                   halign="left")
        box.add_widget(self.history_count)

        sv = ScrollView()
        self.history_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.history_box.bind(minimum_height=self.history_box.setter("height"))
        sv.add_widget(self.history_box)
        box.add_widget(sv)

        tab.add_widget(box)
        self.add_widget(tab)

    # ---------------- 设置页 ----------------
    def build_settings_tab(self):
        tab = TabbedPanelItem(text="设置")
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        box.add_widget(Label(text="支持格式: MP3/FLAC/WAV/APE/OGG/M4A/AAC/WMA/OPUS",
                             color=(0.55, 0.72, 1, 1), size_hint_y=None,
                             height=dp(24), font_size=dp(12), halign="left"))

        box.add_widget(Label(text="自定义解析接口(每行一个,扩展下载链路)",
                             color=(0.55, 0.72, 1, 1), size_hint_y=None,
                             height=dp(24), font_size=dp(12), halign="left"))
        box.add_widget(Label(
            text="格式:方法|名称|URL模板\n方法 text = 请求后自动提取直链, direct = 模板本身即直链\n例: text|我的接口|http://api/?id={id}",
            color=GRAY, size_hint_y=None, height=dp(60), font_size=dp(11), halign="left"))
        self.api_input = TextInput(text="", multiline=True, background_color=PANEL,
                                   foreground_color=FG, cursor_color=ACCENT, font_size=dp(12))
        box.add_widget(self.api_input)

        save = Button(text="保存设置", size_hint_y=None, height=dp(44),
                      background_normal="", background_color=ACCENT)
        save.bind(on_release=lambda *_: self.app.save_custom_apis())
        box.add_widget(save)

        box.add_widget(Label(text="Foobar Lite Mobile v1.1",
                             color=(0.45, 0.47, 0.5, 1), size_hint_y=None,
                             height=dp(30), font_size=dp(11)))
        tab.add_widget(box)
        self.add_widget(tab)


# =====================================================================
#  底部播放控制栏
# =====================================================================
class PlayerBar(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(70),
                         padding=(dp(8), dp(4)), spacing=dp(6), **kw)
        self.app = app
        self.background_color = PANEL

        self.prev_btn = Button(text="⏮", size_hint_x=0.12, background_normal="",
                               background_color=PANEL, font_size=dp(16))
        self.prev_btn.bind(on_release=lambda *_: app.play_prev())
        self.add_widget(self.prev_btn)

        self.play_btn = Button(text="▶", size_hint_x=0.14, background_normal="",
                               background_color=ACCENT, font_size=dp(18))
        self.play_btn.bind(on_release=lambda *_: app.toggle_play())
        self.add_widget(self.play_btn)

        self.next_btn = Button(text="⏭", size_hint_x=0.12, background_normal="",
                               background_color=PANEL, font_size=dp(16))
        self.next_btn.bind(on_release=lambda *_: app.play_next())
        self.add_widget(self.next_btn)

        info_box = BoxLayout(orientation="vertical", size_hint_x=0.62)
        self.song_name = Label(text="未播放", color=FG, halign="left",
                               valign="middle", shorten=True, shorten_from="right",
                               font_size=dp(13))
        self.song_name.bind(size=self.song_name.setter("text_size"))
        info_box.add_widget(self.song_name)

        self.progress_slider = Slider(min=0, max=100, value=0, size_hint_y=None, height=dp(16))
        self.progress_slider.bind(on_touch_up=lambda inst, touch: app.seek(inst.value) if inst.collide_point(*touch.pos) else None)
        info_box.add_widget(self.progress_slider)
        self.add_widget(info_box)


# =====================================================================
#  App
# =====================================================================
class MobileApp(App):
    title = "Foobar Lite"
    name = "foobarlite"

    def build(self):
        self.settings = load_settings()
        self.local_files = []
        self.current_index = -1
        self.play_mode = 0
        self.loop_modes = ["列表循环", "单曲循环", "随机播放"]
        self.player = None
        self.playing_file = None

        if IS_ANDROID:
            Clock.schedule_once(lambda dt: request_android_permissions(), 1)

        root = BoxLayout(orientation="vertical")
        self.root_ui = RootUI(self)
        root.add_widget(self.root_ui)
        self.player_bar = PlayerBar(self)
        root.add_widget(self.player_bar)

        Clock.schedule_once(lambda dt: self._load_apis_into_ui(), 0)
        Clock.schedule_once(lambda dt: self.load_history_ui(), 0)

        self.timer = Clock.schedule_interval(self.update_progress, 0.5)

        return root

    def _load_apis_into_ui(self):
        lines = []
        for a in self.settings.get("custom_apis", []):
            lines.append("{}|{}|{}".format(a.get("method", "text"),
                                           a.get("name", ""), a.get("url", "")))
        self.root_ui.api_input.text = "\n".join(lines)

    # ---------------- 本地扫描 ----------------
    def scan_local(self):
        self.root_ui.scan_status.text = "正在扫描..."
        self.scan_thread = ScanThread(self)
        self.scan_thread.start()

    def scan_progress(self, count):
        self.root_ui.scan_status.text = "已找到 {} 个音频文件...".format(count)

    def scan_finished(self, files):
        self.local_files = files
        self.root_ui.scan_status.text = "扫描完成，共 {} 首音频".format(len(files))
        self.root_ui.local_box.clear_widgets()
        for f in files:
            self.root_ui.local_box.add_widget(LocalSongRow(f, self))
        toast("扫描完成，共 {} 首".format(len(files)))

    def clear_local_list(self):
        self.local_files = []
        self.root_ui.local_box.clear_widgets()
        self.root_ui.scan_status.text = "列表已清空"

    # ---------------- 播放控制 ----------------
    def play_local_file(self, filepath):
        self.stop_player()
        self.player = SoundLoader.load(filepath)
        if self.player is None:
            toast("无法播放该格式: " + os.path.splitext(filepath)[1])
            return
        self.player.play()
        self.playing_file = filepath
        self.player_bar.play_btn.text = "⏸"
        self.player_bar.song_name.text = os.path.basename(filepath)
        self.current_index = self.local_files.index(filepath) if filepath in self.local_files else -1
        self.add_history(filepath)
        self.player.bind(on_stop=lambda x: self.on_song_end())

    def toggle_play(self):
        if self.player is None:
            if self.local_files:
                self.play_local_file(self.local_files[0])
            return
        if self.player.state == "play":
            self.player.stop()
            self.player_bar.play_btn.text = "▶"
        else:
            self.player.play()
            self.player_bar.play_btn.text = "⏸"

    def stop_player(self):
        if self.player:
            try:
                self.player.stop()
            except:
                pass
        self.player_bar.play_btn.text = "▶"

    def play_prev(self):
        if not self.local_files:
            return
        if self.current_index <= 0:
            idx = len(self.local_files) - 1
        else:
            idx = self.current_index - 1
        self.play_local_file(self.local_files[idx])

    def play_next(self):
        if not self.local_files:
            return
        if self.play_mode == 2:
            idx = random.randint(0, len(self.local_files) - 1)
        elif self.current_index >= len(self.local_files) - 1:
            idx = 0
        else:
            idx = self.current_index + 1
        self.play_local_file(self.local_files[idx])

    def on_song_end(self):
        if self.play_mode == 1:
            if self.playing_file:
                self.play_local_file(self.playing_file)
        else:
            self.play_next()

    def seek(self, value):
        if self.player and self.player.length:
            self.player.seek(self.player.length * value / 100)

    def update_progress(self, dt):
        if self.player and self.player.length:
            pos = self.player.get_pos()
            self.player_bar.progress_slider.value = int(pos * 100 / self.player.length)

    # ---------------- 播放历史 ----------------
    def add_history(self, filepath):
        history = self.settings.get("history", [])
        if filepath in history:
            history.remove(filepath)
        history.insert(0, filepath)
        history = history[:100]
        self.settings["history"] = history
        save_settings(self.settings)
        self.load_history_ui()

    def load_history_ui(self):
        history = self.settings.get("history", [])
        self.root_ui.history_count.text = "播放历史 ({})".format(len(history))
        self.root_ui.history_box.clear_widgets()
        for fp in reversed(history[:50]):
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6),
                            padding=(dp(8), dp(4)))
            name_lbl = Label(text=os.path.basename(fp), color=FG, halign="left",
                             valign="middle", shorten=True, shorten_from="right",
                             font_size=dp(12))
            name_lbl.bind(size=name_lbl.setter("text_size"))
            row.add_widget(name_lbl)
            play_btn = Button(text="▶", size_hint_x=0.15, background_normal="",
                              background_color=ACCENT, font_size=dp(13))
            play_btn.bind(on_release=lambda *_, f=fp: self.play_local_file(f))
            row.add_widget(play_btn)
            self.root_ui.history_box.add_widget(row)

    # ---------------- 在线搜索 ----------------
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
            box.add_widget(Label(text="没有找到结果", color=GRAY,
                                 size_hint_y=None, height=dp(40)))
            self.root_ui.status_lbl.text = "没有找到结果"
            return
        for g in groups:
            box.add_widget(SongGroup(g, self))
        self.root_ui.status_lbl.text = "共 {} 首(点一下展开各音源)".format(len(groups))

    # ---------------- 在线试听 ----------------
    def preview_online(self, song):
        toast("正在解析试听链接...")

        def work():
            cands = ms.resolve_song(song, self.settings.get("custom_apis"))
            best = ms.best_direct(cands)
            if not best:
                Clock.schedule_once(lambda dt: toast("没有可试听的直链"), 0)
                return
            try:
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
                    self.stop_player()
                    self.player = SoundLoader.load(tmp)
                    if self.player is None:
                        toast("当前平台无法试听该格式")
                        return
                    self.player.play()
                    self.player_bar.play_btn.text = "⏸"
                    self.player_bar.song_name.text = "在线试听: " + song.get("name", "")
                    toast("正在试听: " + song.get("name", ""))
                    self.player.bind(on_stop=lambda x: setattr(self.player_bar.play_btn, 'text', '▶'))
                Clock.schedule_once(play, 0)
            except Exception as e:
                Clock.schedule_once(lambda dt: toast("试听失败"), 0)

        threading.Thread(target=work, daemon=True).start()

    # ---------------- 下载 ----------------
    def queue_song(self, song):
        def work():
            cands = ms.resolve_song(song, self.settings.get("custom_apis"))
            best = ms.best_direct(cands)
            if not best:
                Clock.schedule_once(lambda dt: toast("没有可用直链(可能需会员)"), 0)
                return
            ext = ms.guess_ext(best["url"], best.get("quality", ""))
            fn = "{}_{}{}".format(ms.safe_filename(song.get("name", "")),
                                  ms.safe_filename(song.get("artist", "")), ext)
            download_dir = os.path.join(self.user_data_dir, "downloads")
            os.makedirs(download_dir, exist_ok=True)
            target = os.path.join(download_dir, fn)

            Clock.schedule_once(lambda dt: toast("开始下载: " + fn), 0)
            r = ms.http_get(best["url"], timeout=30, stream=True)
            with open(target, "wb") as f:
                for chunk in r.iter_content(65536):
                    if chunk:
                        f.write(chunk)
            r.close()
            Clock.schedule_once(lambda dt: toast("下载完成: " + fn), 0)
        threading.Thread(target=work, daemon=True).start()

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


if __name__ == "__main__":
    MobileApp().run()
