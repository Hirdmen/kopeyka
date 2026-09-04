[app]

# (str) Title of your application
title = Копейка

# (str) Package name
package.name = kopeyka

# (str) Package domain (needed for android/ios packaging)
package.domain = org.kopeyka

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include
source.include_exts = py,png,jpg,kv,atlas,json

# (list) Exclusions using pattern matching
source.exclude_patterns = *.pyc,__pycache__,build,dist,.git,.github,*.spec

# (str) Application versioning
version = 1.0.1

# (list) Application requirements
requirements = python3,kivy==2.3.0,kivymd==1.2.0,pypdf,requests

# (list) Supported orientations
orientation = portrait

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (str) android.permissions string to inject in AndroidManifest.xml
android.permissions = INTERNET, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE

# (int) Target Android API
android.api = 33

# (int) Minimum API your APK will support
android.minapi = 24

# (str) Android NDK version to use
android.ndk = 25b

# (bool) If True, then automatically accept SDK license
android.accept_sdk_license = True

# (str) The Android arch to build for
android.arch = arm64-v8a


[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug)
log_level = 2

# (int) Display warning if buildozer is run as root
warn_on_root = 1