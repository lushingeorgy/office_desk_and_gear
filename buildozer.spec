[app]
title = Office Desk & Gear
package.name = officedeskgear
package.domain = org.officedeskgear
source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,atlas,ttf,json
source.include_patterns = client/*,main.py
source.exclude_dirs = .git,.venv,.buildenv,.venv-build,.buildozer,bin,venv,server,migrations,tests,docs,deploy,scripts,db_backup,.clientdata,.github,p4a-recipes
source.exclude_patterns = .env*,*.dump,*.log,*.db,*secret*,*token*
version = 1.1.0
requirements = python3==3.14.2,hostpython3==3.14.2,kivy==2.3.1,kivymd==2.0.0,pillow,requests,certifi,urllib3,idna,charset-normalizer,plyer==2.1.0,pyjnius,opencv,numpy,materialyoucolor==3.0.4,materialshapes==0.3,pycairo==1.28.0,asynckivy==0.6.4,asyncgui==0.6.3,exceptiongroup,android
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,CAMERA
android.api = 35
android.minapi = 29
android.ndk = 28c
android.ndk_api = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.private_storage = True
android.allow_backup = False
android.debug_artifact = apk
android.release_artifact = aab
p4a.bootstrap = sdl2
p4a.branch = develop
p4a.commit = 1c9a4b4f817847bd8505d972d1a0e6f965929a88
p4a.local_recipes = ./p4a-recipes

[buildozer]
log_level = 2
warn_on_root = 1
