[app]
title = Game SoundTracks
package.name = gsd
package.domain = com.gsd.local   # <-- NOT a real domain; just an identifier
version = 0.1.0
entrypoint = main.py
source.dir = .
source.include_exts = py,html,js,css,png,jpg,jpeg,svg,ico

# Python deps (keep it lean)
requirements = python3,kivy,flask,requests,beautifulsoup4,urllib3,certifi,chardet,idna
garden_requirements = androidx_webview

# Permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.permissions += READ_MEDIA_AUDIO
android.allow_cleartext = 1   # allow http://127.0.0.1

android.api = 33
android.minapi = 21
android.ndk_api = 21

[buildozer]
log_level = 2
