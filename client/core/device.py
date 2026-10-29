"""Small native bridges; URI access is confined to user-selected media."""
from pathlib import Path


class DeviceTools:
    def __init__(self, platform, state, schedule):
        self.platform, self.state, self.schedule = platform, state, schedule
        self._activity_callback = None

    def camera_permission(self, callback):
        if self.platform != "android":
            callback(True)
            return
        from android.permissions import Permission, check_permission, request_permissions
        if check_permission(Permission.CAMERA):
            callback(True)
        else:
            request_permissions([Permission.CAMERA], lambda _p, grants:
                                self.schedule(lambda _dt: callback(bool(grants) and all(grants))))

    def choose_photo_android(self, callback, failure):
        from android import activity
        from jnius import autoclass
        Activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Intent = autoclass("android.content.Intent")
        self.close()

        def result(request_code, result_code, intent):
            if request_code != 2043:
                return
            activity.unbind(on_activity_result=result)
            self._activity_callback = None
            if result_code != -1 or intent is None:
                return
            try:
                uri = intent.getData()
                stream = Activity.getContentResolver().openInputStream(uri)
                ByteArray = autoclass("java.io.ByteArrayOutputStream")
                output = ByteArray()
                try:
                    buffer = bytearray(65536)
                    total = 0
                    while True:
                        count = stream.read(buffer)
                        if count == -1:
                            break
                        total += count
                        if total > 5_000_000:
                            raise ValueError("Изображение должно быть не больше 5 МБ")
                        output.write(buffer, 0, count)
                    path = self.state.media_path("selected-photo.jpg")
                    path.write_bytes(bytes(output.toByteArray()))
                finally:
                    stream.close()
                    output.close()
                self.schedule(lambda _dt: callback(str(path)))
            except Exception:
                self.schedule(lambda _dt: failure("Не удалось прочитать выбранное изображение"))

        self._activity_callback = result
        activity.bind(on_activity_result=result)
        intent = Intent(Intent.ACTION_GET_CONTENT)
        intent.setType("image/*")
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        Activity.startActivityForResult(intent, 2043)

    def share(self, path, mime="image/png"):
        """Export a copy through MediaStore; private cache paths are never exposed."""
        if self.platform != "android":
            import webbrowser
            webbrowser.open(Path(path).resolve().as_uri())
            return str(path)
        from jnius import autoclass, cast
        Intent = autoclass("android.content.Intent")
        Activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Values = autoclass("android.content.ContentValues")
        Images = autoclass("android.provider.MediaStore$Images$Media")
        Downloads = autoclass("android.provider.MediaStore$Downloads")
        Version = autoclass("android.os.Build$VERSION")
        if Version.SDK_INT < 29:
            raise ValueError("Экспорт файлов требует Android 10 или новее")
        values = Values()
        values.put("_display_name", Path(path).name)
        values.put("mime_type", mime)
        values.put("relative_path", "Pictures/OfficeDeskGear" if mime.startswith("image/") else "Download/OfficeDeskGear")
        resolver = Activity.getContentResolver()
        collection = Images.EXTERNAL_CONTENT_URI if mime.startswith("image/") else Downloads.EXTERNAL_CONTENT_URI
        uri = resolver.insert(collection, values)
        stream = resolver.openOutputStream(uri)
        try:
            stream.write(Path(path).read_bytes())
        finally:
            stream.close()
        intent = Intent(Intent.ACTION_SEND)
        intent.setType(mime)
        intent.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", uri))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        Activity.startActivity(Intent.createChooser(intent, "Поделиться или распечатать"))
        return str(path)

    def close(self):
        if self._activity_callback is not None:
            from android import activity
            activity.unbind(on_activity_result=self._activity_callback)
            self._activity_callback = None
