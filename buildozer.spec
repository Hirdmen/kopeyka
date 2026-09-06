[app]

title = Копейка
package.name = kopeyka
package.domain = org.kopeyka
version = 1.0.2

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
source.exclude_patterns = *.pyc,__pycache__,build,dist,.git,.github,*.spec

requirements = hostpython3==3.11.9,python3==3.11.9,kivy==2.3.1,kivymd==2.0.0,pillow,asynckivy,asyncgui,materialshapes,materialyoucolor==3.0.3,pypdf,requests,certifi
orientation = portrait
fullscreen = 0

android.permissions = INTERNET, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE, ACCESS_NETWORK_STATE

android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a


[buildozer]

log_level = 2
warn_on_root = 1