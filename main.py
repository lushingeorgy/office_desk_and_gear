import os
from pathlib import Path


def create_visual_app():
    from client.app import OfficeDeskGearApp

    return OfficeDeskGearApp()


def run_visual(application=None):
    (application or create_visual_app()).run()


def main():
    from kivy.utils import platform
    application = create_visual_app()

    if platform in {"android", "ios"}:
        run_visual(application)
        return

    if os.environ.get("DSH_API_URL") or os.environ.get("ODG_CLIENT_ONLY", "0") == "1":
        run_visual(application)
        return

    from client.core.storage import LocalState

    directory = os.environ.get("ODG_CLIENT_DATA") or str(Path(application.user_data_dir) / "private")
    if LocalState(directory, platform).api_url:
        run_visual(application)
        return

    from client.core.local_server import LocalServer
    from server.core.config import settings

    with LocalServer(settings.HOST, settings.PORT) as server:
        os.environ.setdefault("DSH_API_URL", server.base_url)
        run_visual(application)


if __name__ == "__main__":
    main()
