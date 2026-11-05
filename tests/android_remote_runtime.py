"""Opt-in black-box acceptance of the shipping APK against the staging API.

Only real Android touches/type events drive the app. HTTP reads independently
verify persisted results. The operations scope creates fixture actors through
the API; it does not count that setup as UI registration coverage.
Accounts are created by the app UI with random, never-published passwords.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import uuid
import xml.etree.ElementTree as ET

import android_runtime as native
from android_runtime import NativeSmoke,adb,normalized
from client.core.config import DEFAULT_API_URL
from client.services.api_client import ApiClient,ApiError

native.PACKAGE='org.officedeskgear.officedeskgear'
PACKAGE=native.PACKAGE


class RemoteSuite(NativeSmoke):
    def __init__(self,directory):
        super().__init__(directory)
        self.actors={}
        self.results=[]
        self.role=None
        self.tag=uuid.uuid4().hex[:8]
        self.number=900000+int(self.tag[:5],16)%90000
        self.spare_number=self.number+1
        day=dt.date.today()+dt.timedelta(days=8)
        while day.weekday()!=0: day+=dt.timedelta(days=1)
        self.day=day
        self.menu=False
        self.ids={}
        self.scope='full'

    def stage(self,name,function):
        start=time.monotonic()
        try:
            function()
            self.fatal_check()
            self.results.append({'name':name,'status':'PASS','seconds':round(time.monotonic()-start,2)})
            print('PASS:',name,flush=True)
            self.screenshot('evidence-'+str(len(self.results)))
        except Exception as error:
            self.results.append({'name':name,'status':'FAIL','error':str(error),'seconds':round(time.monotonic()-start,2)})
            self.screenshot('failure')
            self.save(False)
            optional={'photo','gallery_ticket','persistence','offline','forbid_admin','desk_views','edit_desk',
                      'edit_equipment','roles','qr','analytics','delete_unused','notifications'}
            if getattr(function,'__name__','') in optional:
                print('FAIL:',name,':',str(error),flush=True)
                self.launch(); self.find('Быстрые действия',timeout=90,scroll=True)
                return False
            raise
        self.save(False)

    def save(self,passed):
        report={'passed':passed,'kind':'Shipping ARM64 APK on Android emulator against remote HTTPS staging API',
                'api':DEFAULT_API_URL,'tag':self.tag,'checks':self.results,
                'scope':self.scope,
                'actors':{role:{k:v for k,v in value.items() if k!='password'} for role,value in self.actors.items()},'ids':self.ids,
                'limitations':['Real phone optical QR/camera quality and physical printer are outside emulator coverage.',
                               'Positive email-code confirmation is tested separately against the isolated emulator API.']}
        (self.directory/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

    def top(self):
        w,h=self.size
        for _ in range(10): adb('shell','input','swipe',20,int(h*.3),20,int(h*.84),'220')
        self.stop_scroll()

    def bottom(self):
        w,h=self.size
        for _ in range(10): adb('shell','input','swipe',20,int(h*.82),20,int(h*.28),'220')
        self.stop_scroll()

    def stop_scroll(self):
        _,h=self.size
        adb('shell','input','swipe',20,int(h*.5),20,int(h*.5),'200')
        time.sleep(.5)

    def find(self,phrase,*,timeout=28,scroll=False):
        target=normalized(phrase)
        if scroll: timeout=max(timeout,80)
        end=time.monotonic()+timeout
        swipes=0
        while time.monotonic()<end:
            for text,x,y in self.screenshot('screen'):
                if self.matches(text,target): return x,y
            self.fatal_check()
            if scroll and swipes<24:
                w,h=self.size
                a,b=(.65,.42) if self.menu else (.8,.3)
                x=w//2 if self.menu else 20
                adb('shell','input','swipe',x,int(h*a),x,int(h*b),'800' if self.menu else '350')
                if not self.menu: self.stop_scroll()
                swipes+=1
            time.sleep(.35)
        raise AssertionError('UI text did not appear: '+phrase)

    @staticmethod
    def matches(text,target):
        if ' '+target+' ' in ' '+text+' ': return True
        if target.startswith('qa') and target.isascii():
            # OCR confuses l/1 and O/0 in the randomized fixture names.
            table=str.maketrans({'0':'o','1':'l','5':'s','8':'b'})
            return target.replace(' ','').translate(table) in text.replace(' ','').translate(table)
        return False

    def ready_words(self):
        end=time.monotonic()+35
        busy=('сохраненные данные обновляем','загрузка','сохраняем','обновляем сессию',
              'входим в аккаунт','создаем профиль','дождитесь завершения текущего запроса')
        while time.monotonic()<end:
            words=self.screenshot('ready')
            if not any(any(text.startswith(prefix) for prefix in busy) for text,_,_ in words): return words
            self.fatal_check();time.sleep(.7)
        raise AssertionError('UI request did not finish before interaction')

    def click(self,phrase,*,scroll=False):
        self.ready_words()
        self.find(phrase,scroll=scroll)
        target=normalized(phrase)
        for text,x,y in self.ready_words():
            if self.matches(text,target):
                adb('shell','input','tap',x,y);time.sleep(.8);return
        raise AssertionError('UI target moved before tap: '+phrase)

    def fill(self,label,value):
        self.top()
        x,y=self.field_point(label)
        adb('shell','input','tap',x,y)
        time.sleep(.25)
        adb('shell','input','keyevent','123')
        adb('shell','input','keyevent',*(['67']*128))
        assert re.fullmatch(r'[A-Za-z0-9@._:+/\-]+',str(value)), 'Only ASCII fixtures are typed'
        adb('shell','input','text',str(value))
        time.sleep(.6)
        adb('shell','input','keyevent','4')
        time.sleep(.4)

    def press(self,label):
        self.top()
        self.click(label,scroll=True)

    def primary(self):
        self.ready_words()
        self.screenshot('primary')
        try: bounds=native.primary_button_bounds(self.picture)
        except AssertionError:
            self.bottom(); self.screenshot('primary-bottom')
            bounds=native.primary_button_bounds(self.picture)
        left,top,right,bottom=bounds
        adb('shell','input','tap',(left+right)//2,(top+bottom)//2)
        time.sleep(1)

    def select(self,label,option):
        self.top()
        x,y=self.field_point(label)
        adb('shell','input','tap',x,y); time.sleep(.5)
        self.menu=True
        try: self.click(option,scroll=True)
        finally: self.menu=False

    def field_point(self,label):
        words=self.ready_words(); target=normalized(label)
        # Filled Material fields shrink their hints to 12 px. OCR sometimes
        # reads the three Cyrillic letters in ФИО as Latin DUC. In a recognized
        # form use its actual paired outline edges and documented field order.
        layouts={
            'С возвращением':['Рабочая почта','Пароль'],
            'Создать аккаунт':['ФИО','Должность','Рабочая почта','Пароль от 6 символов','Повторите пароль'],
            'Подтвердить почту':['Код подтверждения'],
            'Восстановить пароль':['Рабочая почта','Код из письма','Новый пароль от 6 символов','Повторите новый пароль'],
            'Мой профиль':['ФИО','Рабочая почта','Должность','Телефон','Фото профиля'],
            'Рабочие места':['Поиск стола на сервере','Дата'],
            'Фильтры столов':['Этаж','Секция','Минимум розеток','Сортировать по','Порядок'],
            'Новое рабочее место':['Номер стола','Этаж','Секция','Число розеток','Доступность'],
            'Изменить рабочее место':['Номер стола','Этаж','Секция','Число розеток','Доступность'],
            'Добавить оборудование':['Тип оборудования','Модель','Серийный номер','Инвентарный номер','Рабочее место','Требуется розетка','Статус'],
            'Изменить оборудование':['Тип оборудования','Модель','Серийный номер','Инвентарный номер','Требуется розетка','Статус'],
            'Бронь на день':['Дата и время начала','Часов в день'],
            'Бронь на период':['Дата и время начала','Часов в день','Конец периода'],
            'Изменить бронь':['Дата и время начала','Часов в день'],
            'Закрепление техники':['Сотрудник','Рабочее место'],
            'Сотрудники и роли':['Роль'],
            'Сообщить о проблеме':['Тип неисправности','Опишите проблему','Фотография'],
            'Сканировать QR-код':['Код или ID устройства'],
            'Сканировать':['Код или ID устройства'],
        }
        if self.ids.get('ticket'):
            layouts['Обращение '+str(self.ids['ticket'])]=['Новый статус','Комментарий IT-специалиста']
        for title,labels in layouts.items():
            if label not in labels or not any(self.matches(text,normalized(title)) and y<210 for text,_,y in words): continue
            import cv2,numpy as np
            frame=np.asarray(self.picture)
            # Rounded outlines change slightly with Android density and
            # antialiasing; retain their geometry, not a single pixel palette.
            mask=cv2.inRange(frame,np.array([19,27,42]),np.array([85,100,130]))
            mask|=cv2.inRange(frame,np.array([94,128,210]),np.array([184,218,255]))
            contours=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
            edges=[cv2.boundingRect(c) for c in contours]
            lefts=sorted((b for b in edges if 16<=b[0]<=32 and b[2]<=4 and 30<=b[3]<=110),key=lambda b:b[1])
            boxes=[]
            for x,y,w,h in edges:
                if w>self.size[0]*.6 and 44<=h<=130 and cv2.countNonZero(mask[y:y+h,x:x+w])<w*h*.3:
                    boxes.append((x,y,x+w,y+h))
            for x,y,w,h in lefts:
                right=next((b for b in edges if b[0]>self.size[0]*.85 and abs(b[1]-y)<=2 and abs(b[3]-h)<=2),None)
                if right and not any(abs((a[1]+a[3])/2-(y+h/2))<8 for a in boxes): boxes.append((x,y,right[0]+right[2],y+h))
            boxes.sort(key=lambda box:box[1])
            index=labels.index(label)
            if len(boxes)<=index: break
            left,top,right,bottom=boxes[index]
            (self.directory/f'{self.sequence:03d}-field.bounds.json').write_text(json.dumps({'form':title,'field':label,'bounds':boxes[index]}))
            return (left+right)//2,(top+bottom)//2
        for text,x,y in words:
            if text==target and y>=220: return x,y+12
        x,y=self.find(label,scroll=True)
        return x,y+12

    def home(self):
        self.click('Главная')
        time.sleep(.5)

    def logout(self):
        self.home()
        self.open_profile()
        self.press('Выйти из аккаунта')
        self.wait_state(lambda s:not s.get('credential') and not s.get('user'),'Logout did not erase credential')
        assert not self.state().get('cache'), 'Logout retained private cache'
        self.role=None

    def register(self,role):
        name={'employee':'QAEmployee','office_manager':'QAManager','it_admin':'QAAdmin'}[role]+self.tag
        actor={'name':name,'email':role.replace('_','')+'-'+self.tag+'@example.invalid','password':'Qa-'+secrets.token_hex(10),'role':role}
        self.click('Создать аккаунт')
        self.find('ФИО')
        self.fill('ФИО',name)
        self.select('Должность',{'employee':'Сотрудник','office_manager':'Офис-менеджер','it_admin':'администратор'}[role])
        self.fill('Рабочая почта',actor['email'])
        self.fill('Пароль от 6 символов',actor['password'])
        self.fill('Повторите пароль',actor['password'])
        self.primary()
        self.find('Профиль создан',timeout=35)
        self.actors[role]=actor
        self.login(role)
        state=self.state()
        assert state['user']['role']==role and state['user']['is_verified']
        actor['id']=state['user']['id']

    def fixture_actor(self,role):
        name={'employee':'QAEmployee','office_manager':'QAManager','it_admin':'QAAdmin'}[role]+self.tag
        actor={'name':name,'email':role.replace('_','')+'-'+self.tag+'@example.invalid',
               'password':'Qa-'+secrets.token_hex(10),'role':role}
        api=ApiClient(DEFAULT_API_URL)
        try:
            user=api.register(full_name=name,job_title=role,email=actor['email'],password=actor['password'],role=role)
            assert user['role']==role and user['is_verified']
            actor['id']=user['id'];self.actors[role]=actor
        finally: api.close()

    def home_map_context(self):
        # Ongoing booking is API fixture setup; all navigation and cancellation
        # below are actual UI operations in the shipping APK.
        start=dt.datetime.now().replace(second=0,microsecond=0)-dt.timedelta(minutes=5)
        end=start+dt.timedelta(hours=2)
        with self.api('employee') as api:
            desks=api.get_desks(start_at=start.isoformat(),end_at=end.isoformat(),limit=200)
            desk=next(x for x in desks if x['number']<900000 and x['is_free'] and x['is_operational'])
            booking=api.create_booking(desk['id'],start.isoformat(),2)
            features=api.get_desk_features()
        self.ids.update(home_booking=booking['id'],home_desk=desk['id'],home_desk_number=desk['number'])
        cancelled=False
        try:
            self.login('employee');self.find('Ваше место сегодня',scroll=True)
            self.click('Столы');self.fill('Дата',self.day.isoformat())
            self.top();self.primary();self.ready_words()
            self.bottom();self.click('Следующие 20',scroll=True)
            self.find('Записи 21',scroll=True)
            self.home();self.press('Показать на плане')
            self.find('Стол '+str(desk['number']),scroll=True)
            self.top();self.find(start.date().isoformat())
            self.find('Записи 1',scroll=True)
            self.find('Доступность за '+start.strftime('%d.%m %H:%M'),scroll=True)
            self.screenshot('home-map-after-page-two')
            self.top();self.click('Параметры фильтра и сортировки',scroll=True)
            excluded=next(x for x in features if x['id'] not in desk['features'])
            x,y=self.find(excluded['name'],scroll=True)
            adb('shell','input','tap',36,y);time.sleep(.5)
            self.primary();self.find('Столы не найдены',scroll=True)
            self.home();self.press('Показать на плане')
            self.find('Стол '+str(desk['number']),scroll=True)
            self.find('Записи 1',scroll=True)
            self.screenshot('home-map-clears-incompatible-feature')
            self.home();self.click('Открыть бронь',scroll=True)
            self.find('Бронь '+str(booking['id']))
            self.press('Отменить бронирование');self.primary()
            self.find('Мои бронирования')
            with self.api('employee') as api:
                assert next(x for x in api.get_booking_history() if x['id']==booking['id'])['is_cancelled']
            cancelled=True
        finally:
            if not cancelled:
                with self.api('employee') as api: api.cancel_booking(booking['id'])

    def login(self,role):
        actor=self.actors[role]
        self.find('С возвращением')
        self.fill('Рабочая почта',actor['email'])
        self.fill('Пароль',actor['password'])
        self.primary()
        self.wait_state(lambda s:s.get('user',{}).get('email')==actor['email'] and s.get('credential'),'Login was not persisted')
        self.find('Быстрые действия',scroll=True)
        self.role=role

    @contextmanager
    def api(self,role=None):
        actor=self.actors[role or self.role]
        api=ApiClient(DEFAULT_API_URL)
        token=api.login(actor['email'],actor['password'])
        api.set_token(token['access_token'])
        try: yield api
        finally:
            try: api.logout()
            finally: api.close()

    def admin(self):
        self.home()
        self.press('Управление офисом')
        self.find('Управление офисом')

    def desk_search(self,number):
        self.click('Столы')
        self.fill('Поиск стола на сервере',str(number))
        self.top(); self.click('Поиск и обновление')
        self.find('Стол '+str(number),scroll=True)
        self.click('Подробнее и забронировать',scroll=True)
        self.find('Розеток')

    def create_desk(self,number):
        self.admin()
        self.click('Добавить рабочее место')
        for label,value in (('Номер стола',number),('Этаж',3),('Секция',2),('Число розеток',4)):
            self.fill(label,str(value))
        self.primary()
        self.find('Стол '+str(number))
        with self.api() as api:
            item=next(x for x in api.get_desks(search=str(number)) if x['number']==number)
        assert item['floor']==3 and item['section']==2 and item['sockets']==4
        self.ids['desk' if number==self.number else 'spare_desk']=item['id']

    def create_equipment(self,spare=False):
        self.admin(); self.click('Реестр оборудования')
        self.click('Добавить оборудование')
        tail='B' if spare else 'A'
        inventory='QA'+self.tag+tail
        for label,value in (('Тип оборудования','Printer' if spare else 'Laptop'),('Модель','QAModel'),('Серийный номер','QASN'+self.tag+tail),('Инвентарный номер',inventory)):
            self.fill(label,value)
        self.primary(); self.find('Оборудование сохранено')
        with self.api() as api: item=next(x for x in api.admin_list_equipment() if x.get('inventory_number')==inventory)
        self.ids['spare_equipment' if spare else 'equipment']=item['id']

    def open_equipment(self,spare=False):
        self.admin(); self.click('Реестр оборудования')
        text=f"{self.ids['spare_equipment' if spare else 'equipment']} "+('Printer' if spare else 'Laptop')
        self.click(text,scroll=True)
        self.find('Инвентарный номер')

    def assign(self):
        self.open_equipment()
        self.press('Назначить или снять с сотрудника')
        self.select('Сотрудник',self.actors['employee']['name'])
        self.primary(); self.find('Laptop')
        with self.api() as api: item=api.get_equipment(self.ids['equipment'])
        assert item['assigned_user_id']==self.actors['employee']['id']

    def profile(self):
        self.home(); self.open_profile()
        new_name=self.actors['employee']['name']+'X'
        self.fill('ФИО',new_name)
        self.fill('Телефон','79990001122')
        self.primary(); self.find('Профиль сохранён')
        self.actors['employee']['name']=new_name
        with self.api() as api: user=api.get_me()
        assert user['full_name']==new_name and user['phone']=='79990001122'

    def persistence(self):
        self.home()
        state=self.state(); credential=state['credential']; user=state['user']['id']
        assert credential['kind']=='android-keystore'
        assert not re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',json.dumps(state))
        self.launch(); self.find('Быстрые действия',scroll=True)
        restored=self.state()
        assert restored['user']['id']==user and restored['credential']==credential
        self.ids['restored_user_id']=user

    def forbid_admin(self):
        with self.api() as api:
            for path in ('/admin/users','/admin/equipment','/admin/analytics'):
                try: api._request('GET',path)
                except ApiError as error: assert error.status_code==403
                else: raise AssertionError('Employee accessed '+path)

    def desk_views(self):
        self.click('Столы'); self.find('Рабочие места')
        self.top(); self.click('Показать план этажа')
        self.find('Показать списком')
        self.click('Показать списком')
        self.find('Рабочие места')
        self.top(); self.click('Параметры фильтра и сортировки')
        self.fill('Этаж','2')
        self.select('Сортировать по','Розеткам')
        self.select('Порядок','По убыванию')
        self.primary(); self.find('Рабочие места')
        self.top(); self.click('Параметры фильтра и сортировки')
        self.press('Сбросить'); self.find('Рабочие места')
        self.bottom(); self.click('Следующие 20')
        self.find('Записи 21',scroll=True)
        self.bottom(); self.click('Предыдущие 20')

    def book(self,period=False):
        self.desk_search(self.number)
        self.click('Забронировать на период' if period else 'Забронировать на день',scroll=True)
        day=self.day+dt.timedelta(days=7) if period else self.day
        self.fill('Дата и время начала',day.isoformat()+'T09:00')
        self.fill('Часов в день','1')
        if period: self.fill('Конец периода',(day+dt.timedelta(days=2)).isoformat())
        self.primary()
        self.find('Мои бронирования')
        with self.api() as api: items=api._request('GET','/bookings')
        candidates=[x for x in items if x['desk_id']==self.ids['desk'] and not x['is_cancelled']]
        assert len(candidates)==(4 if period else 1),len(candidates)
        if not period: self.ids['booking']=candidates[0]['id']
        else: self.ids['period_booking']=max(candidates,key=lambda x:x['planed_at'])['id']

    def open_booking(self,admin=False):
        if admin:
            self.admin(); self.click('Бронирования сотрудников')
        else: self.click('Брони')
        # The shipping list intentionally shows the real desk number, not a PK.
        target=self.day.strftime('%d.%m.%Y')+' 11:00' if admin else 'Стол '+str(self.number)
        self.click(target,scroll=True)
        self.find('Бронь '+str(self.ids['booking']))

    def edit_booking(self):
        self.open_booking()
        self.click('Изменить дату и длительность',scroll=True)
        self.fill('Дата и время начала',self.day.isoformat()+'T11:00')
        self.fill('Часов в день','2')
        self.primary()
        self.find('Мои бронирования')
        with self.api() as api: items=api._request('GET','/bookings')
        item=next(x for x in items if x['id']==self.ids['booking'])
        assert '11:00' in item['planed_at'] and int(item['duration'].split(':')[0])==2

    def admin_cancel(self):
        self.open_booking(True)
        self.fill('Причина отмены','QACancellation')
        self.press('Отменить от имени администратора')
        self.primary()
        self.find('Все бронирования')
        with self.api('employee') as api: item=next(x for x in api.get_booking_history() if x['id']==self.ids['booking'])
        assert item['is_cancelled'] and item.get('cancellation_reason')=='QACancellation'

    def scanner(self):
        self.click('Техника'); self.click('Сканировать')
        self.allow_permission()
        self.fill('Код или ID устройства',str(self.ids['equipment']))
        self.primary()
        self.find('Laptop')

    def ticket(self):
        self.click('Техника')
        self.click(str(self.ids['equipment'])+' Laptop',scroll=True)
        self.click('Сообщить о проблеме',scroll=True)
        self.select('Тип неисправности','Программная ошибка')
        self.fill('Опишите проблему','QATicket'+self.tag)
        self.primary()
        self.find('История обращения',scroll=True)
        with self.api() as api: item=next(x for x in api.get_my_tickets() if self.tag in x['description'])
        self.ids['ticket']=item['id']

    def process_ticket(self):
        self.admin(); self.click('Входящие обращения')
        self.click(str(self.ids['ticket'])+' Новая',scroll=True)
        self.select('Новый статус','В работе')
        self.primary()
        self.fill('Комментарий IT-специалиста','QADiagnostics'+self.tag)
        self.press('Добавить комментарий')
        self.find('QADiagnostics'+self.tag,scroll=True)
        self.select('Новый статус','Решена'); self.primary()
        self.select('Новый статус','Закрыта'); self.primary()
        with self.api('employee') as api:
            item=next(x for x in api.get_my_tickets() if x['id']==self.ids['ticket'])
            events=api.ticket_events(self.ids['ticket'])
        assert item['status']=='closed' and any(self.tag in x['text'] for x in events)

    def gallery_ticket(self):
        # Stage an image file for Android's real system picker. No app-private
        # files or state are changed; selection and upload use the shipping UI.
        with self.api('it_admin') as api: data=api.admin_generate_qr(self.ids['equipment'])
        fixture=self.directory/'gallery-fixture.png'
        fixture.write_bytes(base64.b64decode(data['qr_code_base64'].split(',')[-1]))
        adb('push',str(fixture.resolve()),'/sdcard/Download/ODG-gallery-fixture.png')
        adb('shell','am','broadcast','-a','android.intent.action.MEDIA_SCANNER_SCAN_FILE',
            '-d','file:///sdcard/Download/ODG-gallery-fixture.png',required=False)
        self.click('Техника');self.click(str(self.ids['equipment'])+' Laptop',scroll=True)
        self.click('Сообщить о проблеме',scroll=True)
        self.fill('Опишите проблему','QAGallery'+self.tag)
        self.press('Выбрать фото из галереи');time.sleep(2)
        picked=False
        for _ in range(4):
            adb('shell','uiautomator','dump','/sdcard/qa-picker.xml')
            tree=ET.fromstring(adb('shell','cat','/sdcard/qa-picker.xml'))
            nodes=list(tree.iter('node'))
            candidates=[node for node in nodes if node.get('text')=='ODG-gallery-fixture.png']
            if not candidates:
                candidates=[node for node in nodes if node.get('resource-id','').endswith(('/icon_thumb','/thumbnail')) and node.get('bounds')]
            if candidates:
                node=candidates[0];x1,y1,x2,y2=map(int,re.findall(r'\d+',node.get('bounds','')))
                adb('shell','input','tap',(x1+x2)//2,(y1+y2)//2);picked=True;break
            downloads=next((node for node in nodes if node.get('text')=='Downloads'),None)
            if downloads:
                x1,y1,x2,y2=map(int,re.findall(r'\d+',downloads.get('bounds','')))
                adb('shell','input','tap',(x1+x2)//2,(y1+y2)//2)
            else: adb('shell','input','keyevent','82')
            time.sleep(1)
        assert picked,'No image was visible in Android system picker'
        self.find('selected-photo',timeout=30,scroll=True)
        self.primary();self.find('История обращения',scroll=True)
        with self.api() as api:
            item=next(x for x in api.get_my_tickets() if 'QAGallery'+self.tag in x['description'])
            assert item['photo_url']
            target=self.directory/'gallery-upload.jpg'
            api.download_private(item['photo_url'],target)
        self.ids['photo_ticket']=item['id']
        self.click('Посмотреть фото',scroll=True);self.find('Фотография')
        self.screenshot('ticket-private-photo');self.native_back()

    def notifications(self):
        self.home(); self.press('Уведомления')
        self.find('Обращение',scroll=True)
        self.click('Отметить прочитанным',scroll=True)
        with self.api() as api: assert any(x['is_read'] for x in api.get_notifications())

    def photo(self):
        self.home(); self.open_profile()
        self.press('Сделать фото'); time.sleep(2); self.allow_permission()
        self.find('Фотография',timeout=25)
        time.sleep(3); self.screenshot('emulated-camera')
        self.primary()
        self.primary(); self.find('Профиль сохранён')
        with self.api() as api:
            user=api.get_me()
            assert user['avatar_url']
            target=self.directory/'uploaded-avatar.jpg'
            api.download_private(user['avatar_url'],target)
            from PIL import Image
            with Image.open(target) as image: assert min(image.size)>100
        self.home(); self.open_profile()
        self.press('Посмотреть фото профиля')
        self.find('Фотография'); self.screenshot('private-avatar')
        self.native_back()

    def offline(self):
        import socket
        from urllib.parse import urlsplit
        host=socket.gethostbyname(urlsplit(DEFAULT_API_URL).hostname)
        assert re.fullmatch(r'[0-9.]+',host)
        self.home(); self.find('Быстрые действия',scroll=True)
        before=self.state(); assert before.get('cache',{}).get('home')
        adb('root'); adb('wait-for-device')
        rule=['OUTPUT','-d',host,'-p','tcp','--dport','443','-j','REJECT']
        adb('shell','iptables','-I',*rule)
        try:
            self.launch(); self.find('Быстрые действия',timeout=90,scroll=True)
            self.find('Нет связи',timeout=45,scroll=True)
            state=self.state()
            assert state['user']['id']==before['user']['id'] and state['credential']==before['credential']
            assert state['cache']['home']['payload']==before['cache']['home']['payload']
        finally: adb('shell','iptables','-D',*rule,required=False)
        self.home(); self.find('Быстрые действия',scroll=True)
        self.fatal_check()

    def own_cancel(self):
        original=self.ids['booking']
        self.ids['booking']=self.ids['period_booking']
        try:
            self.open_booking()
            self.press('Отменить бронирование'); self.primary()
            self.find('Мои бронирования')
            with self.api() as api: item=next(x for x in api.get_booking_history() if x['id']==self.ids['booking'])
            assert item['is_cancelled']
        finally: self.ids['booking']=original

    def roles(self):
        # The list is sorted by full_name: Demo Colleague is the first row.
        self.admin(); self.click('Сотрудники и роли')
        for option,role in (('Офис-менеджер','office_manager'),('Сотрудник','employee')):
            self.top(); self.find('Demo Colleague')
            self.select('Роль',option)
            self.top(); self.click('Сохранить роль')
            self.find('Demo Colleague'); self.primary()
            self.find('Сотрудники и роли')
            with self.api() as api: user=next(x for x in api.admin_list_users() if x['email']=='colleague@demo.invalid')
            assert user['role']==role

    def edit_desk(self):
        self.desk_search(self.number)
        self.press('Редактировать стол')
        self.fill('Число розеток','6')
        self.select('Доступность','Временно недоступен')
        self.primary(); self.find('Стол '+str(self.number))
        with self.api() as api:
            item=api.get_desk(self.ids['desk'])
            assert item['sockets']==6 and not item['is_operational']
        self.press('Редактировать стол')
        self.select('Доступность','В эксплуатации')
        self.primary(); self.find('Стол '+str(self.number))
        with self.api() as api: assert api.get_desk(self.ids['desk'])['is_operational']

    def edit_equipment(self):
        self.open_equipment(); self.press('Изменить устройство')
        self.fill('Модель','QAModelUpdated')
        self.primary(); self.find('Оборудование сохранено')
        with self.api() as api: assert api.get_equipment(self.ids['equipment'])['model']=='QAModelUpdated'

    def native_back(self):
        adb('shell','input','keyevent','4'); time.sleep(1)

    def wait_chooser(self):
        for _ in range(15):
            focus=adb('shell','dumpsys','window','displays')
            if any(x in focus for x in ('ChooserActivity','ResolverActivity','IntentResolver')): return
            time.sleep(.5)
        raise AssertionError('Native share chooser did not open')

    def qr(self):
        self.open_equipment()
        self.press('для печати')
        self.find('оборудования')
        self.find('Сохранить поделиться',scroll=True)
        self.click('Сохранить поделиться')
        self.wait_chooser(); self.screenshot('qr-share'); self.native_back()
        self.top(); self.click('Распечатать QR-код',scroll=True)
        self.find('Save as PDF',timeout=25)
        self.screenshot('qr-print-preview'); self.native_back()
        with self.api() as api: data=api.admin_generate_qr(self.ids['equipment'])
        import cv2,numpy as np
        decoded,_,_=cv2.QRCodeDetector().detectAndDecode(cv2.imdecode(np.frombuffer(base64.b64decode(data['qr_code_base64'].split(',')[-1]),np.uint8),cv2.IMREAD_COLOR))
        assert decoded==data['qr_code_url'] and str(self.ids['equipment']) in decoded

    def analytics(self):
        self.admin(); self.click('Аналитика и отчёт')
        self.find('Аналитика офиса')
        self.press('Экспорт отчёта CSV')
        self.wait_chooser(); self.screenshot('csv-share'); self.native_back()
        self.press('Сохранить отчёт на сервере')
        self.find('сохранён на сервере')
        self.press('Архив отчётов')
        self.find('Архив отчётов')
        self.click('Экспортировать JSON',scroll=True)
        self.wait_chooser(); self.screenshot('json-share'); self.native_back()

    def delete_unused(self):
        self.open_equipment(True)
        self.press('Удалить устройство'); self.primary()
        with self.api() as api: assert all(x['id']!=self.ids['spare_equipment'] for x in api.admin_list_equipment())
        self.desk_search(self.spare_number)
        self.press('Удалить стол'); self.primary()
        with self.api() as api: assert all(x['id']!=self.ids['spare_desk'] for x in api.get_desks(limit=200))

    def reset_screen(self):
        self.click('Забыли пароль?')
        self.find('Восстановить пароль')
        self.fill('Рабочая почта',self.actors['employee']['email'])
        self.top(); self.click('Получить код',scroll=True)
        self.find('восстановления')
        self.press('Ко входу')
        self.fill('Рабочая почта',self.actors['employee']['email'])
        self.click('Подтвердить рабочую почту')
        self.find('Подтвердить почту')
        self.press('Ко входу')

    def allow_permission(self):
        focus=adb('shell','dumpsys','window','displays')
        if 'permissioncontroller' not in focus.lower(): return
        adb('shell','uiautomator','dump','/sdcard/qa-window.xml')
        xml=ET.fromstring(adb('shell','cat','/sdcard/qa-window.xml'))
        for node in xml.iter('node'):
            if 'permission_allow_foreground_only_button' in node.get('resource-id','') or node.get('text')=='While using the app':
                x1,y1,x2,y2=map(int,re.findall(r'\d+',node.get('bounds','')))
                adb('shell','input','tap',(x1+x2)//2,(y1+y2)//2); time.sleep(2); return
        raise AssertionError('Camera permission dialog could not be approved')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apk',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('tests/artifacts/android-remote-runtime'))
    parser.add_argument('--scope',choices=('full','operations','home-map'),default='full')
    args=parser.parse_args()
    suite=RemoteSuite(args.output)
    suite.scope=args.scope
    try:
        assert os.getenv('ODG_REMOTE_ACCEPTANCE')=='1'
        api=ApiClient(DEFAULT_API_URL)
        try: assert api.check_connection()['service']=='office-desk-gear'
        finally: api.close()
        assert 'arm64-v8a' in adb('shell','getprop','ro.product.cpu.abilist')
        adb('install','-r',str(args.apk.resolve()))
        adb('shell','pm','clear',PACKAGE)
        adb('shell','wm','size','480x960'); adb('shell','wm','density','160')
        adb('logcat','-c')
        suite.launch(); suite.find('С возвращением',timeout=90)
        if args.scope=='home-map':
            suite.fixture_actor('employee')
            suite.stage('Current desk map resets page date and incompatible features',suite.home_map_context)
            suite.stage('Home-map fixture logout clears session and cache',suite.logout)
            suite.save(True)
            return
        for role in ('employee','office_manager','it_admin'):
            if args.scope=='operations':
                suite.fixture_actor(role)
            else:
                suite.stage('Registration and selected role '+role,lambda role=role:suite.register(role))
                if role=='employee':
                    suite.stage('Profile update persists on remote server',suite.profile)
                    suite.stage('Emulated camera capture upload and private viewer',suite.photo)
                    suite.stage('Keystore and cold restart retain login',suite.persistence)
                    suite.stage('Offline cold restart cache and network recovery',suite.offline)
                    suite.stage('Employee admin requests rejected with 403',suite.forbid_admin)
                suite.stage('Logout '+role,suite.logout)
        suite.login('office_manager')
        suite.stage('Desk list filters sorting map pagination',suite.desk_views)
        suite.stage('Create desk and actual displayed number',lambda:suite.create_desk(suite.number))
        suite.stage('Create unused desk for deletion',lambda:suite.create_desk(suite.spare_number))
        suite.stage('Edit desk parameters',suite.edit_desk)
        suite.logout(); suite.login('it_admin')
        suite.stage('Create equipment',suite.create_equipment)
        suite.stage('Create unused equipment',lambda:suite.create_equipment(True))
        suite.stage('Edit equipment model',suite.edit_equipment)
        suite.stage('Change and restore employee role',suite.roles)
        suite.stage('Assign equipment to employee',suite.assign)
        suite.stage('Server QR sharing and print preview',suite.qr)
        suite.logout(); suite.login('employee')
        suite.stage('Manual QR ID opens assigned equipment',suite.scanner)
        suite.stage('Create booking',suite.book)
        suite.stage('Edit booking',suite.edit_booking)
        suite.stage('Create three-day booking period',lambda:suite.book(True))
        suite.stage('Employee cancels own period booking',suite.own_cancel)
        suite.stage('Create IT ticket',suite.ticket)
        suite.stage('Android gallery file upload and ticket photo viewer',suite.gallery_ticket)
        suite.logout(); suite.login('office_manager')
        suite.stage('Office manager cancellation with reason',suite.admin_cancel)
        suite.logout(); suite.login('it_admin')
        suite.stage('IT statuses comments and history',suite.process_ticket)
        suite.stage('Analytics archive CSV and JSON',suite.analytics)
        suite.stage('Delete unused equipment and desk',suite.delete_unused)
        suite.logout(); suite.login('employee')
        suite.stage('Read server notifications',suite.notifications)
        suite.stage('Final logout removes session and cache',suite.logout)
        suite.stage('Password reset and verification screens',suite.reset_screen)
        suite.stage('Current desk map resets page date and incompatible features',suite.home_map_context)
        suite.stage('Home-map fixture logout clears session and cache',suite.logout)
        passed=all(result['status']=='PASS' for result in suite.results)
        suite.save(passed)
        if not passed: raise AssertionError('One or more independent native checks failed; see result.json')
        print('Remote native checks passed:',len(suite.results))
    except Exception as error:
        suite.save(False)
        print(type(error).__name__+': '+str(error),file=sys.stderr)
        raise
    finally:
        (args.output/'logcat.txt').write_text(suite.logcat(app_only=True),encoding='utf-8')
        adb('shell','am','force-stop',PACKAGE,required=False)


if __name__=='__main__': main()
