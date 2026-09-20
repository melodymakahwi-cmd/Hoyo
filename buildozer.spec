[app]
title = Deriv Monitor
package.name = derivmonitor
package.domain = org.derivmonitor
source.dir = .
source.include_exts = py,json
version = 0.1
requirements = python3,kivy==2.3.0,websockets,httpx,plyer
orientation = portrait
fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE,POST_NOTIFICATIONS,WAKE_LOCK,FOREGROUND_SERVICE
android.api = 33
android.minapi = 24
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 1
