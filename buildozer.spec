[app]
# ------------------------------------------------------------------
#  Foobar Lite Mobile —— 安卓打包配置(buildozer)
#  只做在线搜索 + 多链路下载
# ------------------------------------------------------------------
title = Foobar Lite
package.name = foobarlite
package.domain = org.foobarlite

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,ttf
source.exclude_dirs = bin,.buildozer,__pycache__

version = 1.0

# certifi 必须带,否则安卓上 HTTPS 请求会因找不到 CA 证书而失败
requirements = python3,kivy==2.3.1,requests,certifi,urllib3,chardet,idna

orientation = portrait
fullscreen = 0
# 允许后台继续下载
android.allow_backup = True

# ------------------------------------------------------------------
#  权限
#  INTERNET              联网搜索 / 下载(必需)
#  READ/WRITE_EXTERNAL_STORAGE  写入公共 Music 目录(Android 10 及以下)
#  MANAGE_EXTERNAL_STORAGE      Android 11+ 写公共目录(需用户手动授权)
# ------------------------------------------------------------------
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

android.api = 33
android.minapi = 21
android.accept_sdk_license = True

# 目标 CPU 架构。arm64-v8a 覆盖 2016 年后的绝大多数手机。
# 要兼容老机型就把下面改成:  android.archs = arm64-v8a, armeabi-v7a
android.archs = arm64-v8a

# 首次构建会自动下载 Android SDK / NDK(约 3-6 GB),放在磁盘空间充足的盘上
android.sdk_path =
android.ndk_path =

# 构建日志详细程度
log_level = 2
warn_on_root = 1

[buildozer]
log_level = 2
warn_on_root = 1
