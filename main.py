import os


def run_visual():
    from client.registration import OfficeDeskGearApp

    OfficeDeskGearApp().run()


def main():
    from kivy.utils import platform

    if platform in {"android", "ios"}:
        run_visual()
        return

    from client.core.local_server import LocalServer
    from server.core.config import settings

    with LocalServer(settings.HOST, settings.PORT) as server:
        os.environ.setdefault("DSH_API_URL", server.base_url)
        run_visual()


if __name__ == "__main__":
    main()
