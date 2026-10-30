# Сборка и проверка Android

Клиент собирается отдельно от FastAPI и PostgreSQL. В APK входят KivyMD,
сетевой клиент, локальный кэш и компоненты камеры. Адрес сервера задаётся
на экране входа; для удалённого размещения нужен адрес HTTPS вида
`https://office.example.org/api/v1`. Секреты сервера в APK не включаются.

В этой среде Windows нет настроенного Linux toolchain и подключённого
Android-устройства. APK здесь не собирался; успешная установка и работа
камеры должны быть подтверждены указанной ниже проверкой на телефоне.

## Зафиксированная конфигурация

| Компонент | Значение |
| --- | --- |
| Buildozer | `76bac28545c66bbcc671d49aca553a747d69608e` |
| python-for-android develop | `1c9a4b4f817847bd8505d972d1a0e6f965929a88` |
| Python на телефоне и hostpython | `3.14.2` |
| Kivy и KivyMD | `2.3.1` и `2.0.0` |
| Material You | `3.0.4`, локальный рецепт |
| MaterialShapes и PyCairo | `0.3` и `1.28.0` |
| Android | arm64, Android 10 и новее, target API 35 |
| NDK | `28c`, native API `24` |
| Хост сборки | Linux, Python 3.11, Java 17 |

Минимальный Android API приложения 29 допускает использование библиотек,
собранных для native API 24. Эти параметры задаются отдельно. Native API 24
совпадает с тегом опубликованного ARM64 wheel Material You для CPython 3.14.
Флаг `p4a.extra_args = --allow-minsdk-ndkapi-mismatch` явно разрешает
упаковщику сочетание native API 24 и минимального API приложения 29;
минимальная поддерживаемая версия Android остаётся 10.

Обычный рецепт Material You в указанном p4a содержит версию 2.0.10, тогда как
KivyMD 2 требует версию не ниже 3.0.3. Поэтому `p4a-recipes/materialyoucolor`
использует официальный архив 3.0.4 с SHA-256. Стандартный PyProjectRecipe
может установить соответствующий Android wheel либо собрать C++17 из
проверенного исходного архива. PyCairo и libcairo уже представлены рецептами
в выбранном p4a; отдельный рецепт MaterialShapes не нужен.

## Сборка через GitHub Actions

После размещения репозитория в GitHub откройте Actions → Android APK →
Run workflow. Файл `.github/workflows/android.yml` устанавливает зависимости,
собирает debug APK и сохраняет его как `OfficeDeskGear-android-debug`.
Само наличие workflow не подтверждает успешной сборки: дождитесь зелёного
результата и скачайте APK из артефактов конкретного запуска.

## Сборка в Linux

Используйте отдельную копию проекта в пути без пробелов, например
`~/office_desk_gear`. Для WSL нужна Linux-файловая система домашнего каталога,
а не каталог OneDrive или `/mnt/c/...`.

```bash
sudo apt-get update
sudo apt-get install -y build-essential git zip unzip autoconf automake \
  libtool libltdl-dev pkg-config zlib1g-dev libncurses-dev libtinfo6 \
  cmake ninja-build libffi-dev libssl-dev autopoint gettext ccache patch \
  openjdk-17-jdk python3-venv python3-dev
python3.11 -m venv .buildenv
source .buildenv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  'git+https://github.com/kivy/buildozer.git@76bac28545c66bbcc671d49aca553a747d69608e' \
  'Cython==0.29.34'
buildozer -v android debug
```

На хосте должен быть установлен Python 3.11. Версия целевого Python 3.14.2
собирается инструментами p4a независимо от версии хоста. При изменении
нативных зависимостей выполните `buildozer android clean` перед новой сборкой.
Первый запуск скачивает SDK, NDK и исходные архивы; необходимы сеть и свободное
место на диске. OpenCV нужен для распознавания QR и увеличивает время сборки.

## Приёмка на телефоне

1. Установите полученный APK через файловый менеджер или `adb install -r bin/*.apk`.
2. Укажите HTTPS API и проверьте регистрацию, письмо с кодом, вход и восстановление пароля.
3. Войдите, закройте приложение, остановите его в настройках Android и откройте снова:
   пользователь должен остаться в аккаунте до истечения или отзыва токена.
4. Отключите сеть и перезапустите клиент: ранее загруженные сведения должны
   показываться с отметкой кэша; изменение данных должно требовать соединения.
5. Включите сеть, создайте, измените и отмените бронь; проверьте конфликт
   одновременного бронирования со второго клиента.
6. Откройте QR-сканер, выдайте доступ к камере, считайте код и выйдите со страницы:
   индикатор использования камеры должен погаснуть. Повторите после сворачивания.
7. Загрузите выбранную фотографию в обращение, измените статус под IT-аккаунтом
   и проверьте уведомление, комментарии и историю у автора.
8. Под IT-аккаунтом создайте технику, назначьте её сотруднику, сохраните QR и
   откройте системное меню передачи файла. Проверьте экспорт отчёта.
9. Нажмите «Выйти», перезапустите приложение и убедитесь, что личный кэш
   предыдущего пользователя недоступен новому аккаунту.

Результаты этой проверки следует записать с моделью телефона, версией Android
и идентификатором сборки. Проверка desktop-интерфейса её не заменяет.

## Первичные источники

- [Зависимости и Buildozer в KivyMD](https://github.com/kivymd/KivyMD/blob/master/README.md)
- [Официальные файлы Material You 3.0.4](https://pypi.org/project/materialyoucolor/3.0.4/)
- [Рецепты и сборка Python-пакетов в p4a](https://github.com/kivy/python-for-android/blob/develop/doc/source/recipes.rst)
- [Рецепт PyCairo](https://github.com/kivy/python-for-android/blob/1c9a4b4f817847bd8505d972d1a0e6f965929a88/pythonforandroid/recipes/pycairo/__init__.py)
- [Проверки Android native API в p4a](https://github.com/kivy/python-for-android/blob/1c9a4b4f817847bd8505d972d1a0e6f965929a88/pythonforandroid/recommendations.py)
