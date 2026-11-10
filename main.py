def create_visual_app():
    from client.app import OfficeDeskGearApp

    return OfficeDeskGearApp()


def run_visual(application=None):
    (application or create_visual_app()).run()


def main():
    # Desktop and mobile use the same configured remote API. Starting the UI
    # must never start a database/backend or fall back to a local server.
    run_visual(create_visual_app())


if __name__ == "__main__":
    from client.desktop import launch
    raise SystemExit(launch(main))
