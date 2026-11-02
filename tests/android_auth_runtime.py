"""Positive email-code UI flows against the isolated CI API, never remote secrets."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

import android_runtime as native
from android_remote_runtime import RemoteSuite,adb

native.PACKAGE='org.officedeskgear.officedeskgearqa'


class AuthSuite(RemoteSuite):
    def __init__(self,directory):
        super().__init__(directory)
        self.sensitive=False
        self.raw_directory=Path(tempfile.mkdtemp(prefix='odg-auth-private-',dir=os.getenv('RUNNER_TEMP')))
        self.email='verify-'+self.tag+'@example.invalid'
        self.password='Qa-'+secrets.token_hex(10)

    def screenshot(self,label):
        output_directory=self.directory
        self.directory=self.raw_directory
        try: result=super().screenshot(label)
        finally: self.directory=output_directory
        picture=self.picture.copy()
        if self.sensitive:
            from PIL import ImageDraw
            ImageDraw.Draw(picture).rectangle((0,70,picture.width,picture.height),fill='#182130')
        picture.save(self.directory/f'{self.sequence:03d}-{label}.png')
        # Raw OCR and code-containing screenshots stay outside uploaded artifacts.
        return result

    def save(self,passed):
        (self.directory/'result.json').write_text(json.dumps({'passed':passed,
            'kind':'QA APK and isolated PostgreSQL API with email verification enabled',
            'checks':self.results},ensure_ascii=False,indent=2),encoding='utf-8')

    def code(self,purpose='Подтверждение электронной почты'):
        from server.core.config import settings
        end=time.monotonic()+15
        while time.monotonic()<end:
            paths=sorted((settings.DATA_DIR/'outbox').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
            for path in paths:
                value=json.loads(path.read_text())
                if value.get('email')==self.email and value.get('purpose')==purpose: return value['code']
            time.sleep(.3)
        raise AssertionError('Isolated outbox message missing')

    def verify_current(self):
        self.find('Подтвердить почту')
        self.sensitive=True
        self.fill('Код подтверждения',self.code())
        self.primary()
        self.find('Почта подтверждена')
        self.sensitive=False

    def registration(self):
        self.click('Создать аккаунт')
        for label,value in (('ФИО','QAVerify'+self.tag),('Рабочая почта',self.email),
                            ('Пароль от 6 символов',self.password),('Повторите пароль',self.password)):
            self.fill(label,value)
        self.primary()
        self.verify_current()

    def enter(self):
        self.fill('Рабочая почта',self.email); self.fill('Пароль',self.password)
        self.primary(); self.find('Быстрые действия',timeout=50,scroll=True)
        assert self.state()['user']['is_verified']

    def reset_password(self):
        self.enter(); self.logout()
        self.click('Забыли пароль?')
        self.fill('Рабочая почта',self.email)
        self.top(); self.click('Получить код',scroll=True)
        self.find('восстановления')
        self.sensitive=True
        self.fill('Код из письма',self.code('Восстановление пароля'))
        self.password='Changed-'+secrets.token_hex(10)
        self.fill('Новый пароль от 6 символов',self.password)
        self.fill('Повторите новый пароль',self.password)
        self.primary(); self.find('Пароль изменён')
        self.sensitive=False
        self.enter()

    def change_email(self):
        self.home(); self.open_profile()
        self.email='changed-'+self.tag+'@example.invalid'
        self.fill('Рабочая почта',self.email)
        self.primary(); self.find('Новая почта ожидает подтверждения')
        assert not self.state().get('credential')
        self.verify_current(); self.enter(); self.logout()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('tests/artifacts/android-auth-runtime'))
    args=parser.parse_args(); suite=AuthSuite(args.output)
    try:
        from server.core.config import settings
        assert settings.ENVIRONMENT=='test' and settings.MAIL_MODE=='outbox' and settings.REQUIRE_EMAIL_VERIFICATION
        suite.launch(); suite.find('С возвращением',timeout=90)
        suite.stage('Registration and email-code confirmation',suite.registration)
        suite.stage('Reset password by email code and sign in again',suite.reset_password)
        suite.stage('Changed email revokes session and needs confirmation',suite.change_email)
        suite.save(True)
    finally:
        import shutil
        shutil.rmtree(suite.raw_directory)
        (args.output/'logcat.txt').write_text(suite.logcat(app_only=True),encoding='utf-8')
        adb('shell','am','force-stop',native.PACKAGE,required=False)


if __name__=='__main__': main()
