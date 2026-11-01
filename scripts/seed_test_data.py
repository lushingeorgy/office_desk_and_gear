"""Explicit staging-only reset and rich demo data; credentials stay private."""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
from pathlib import Path
import secrets
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1] if not globals().get('__file__','<stdin>').startswith('<') else Path.cwd()
sys.path.insert(0,str(ROOT))
import server.database.base
from server.core.config import settings
from server.core.security import hash_password
from server.core.time import office_now
from server.database.base import Base
from server.database.session import SessionLocal,engine
from server.models.users import User
from server.models.desks import Desk
from server.models.desk_features import DeskFeature
from server.models.equipment import Equipment
from server.models.bookings import Booking
from server.models.activity import Notification
from server.repositories.activity_repository import ActivityRepository
from server.repositories.equipment_repository import EquipmentRepository
from server.repositories.ticket_repository import TicketRepository
from server.repositories.report_repository import ReportRepository
from server.services.activity_service import ActivityService
from server.services.ticket_service import TicketService
from server.services.report_service import ReportService
from sqlalchemy import inspect,select,text


def main():
    os.umask(0o077)
    if settings.ENVIRONMENT!='staging' or os.getenv('ODG_CONFIRM_TEST_RESET')!='office_desk_gear':
        raise RuntimeError('Reset requires staging and ODG_CONFIRM_TEST_RESET=office_desk_gear')
    if settings.MAIL_MODE!='outbox' or settings.REQUIRE_EMAIL_VERIFICATION:
        raise RuntimeError('Demo setup requires explicitly configured outbox mode')
    names=set(Base.metadata.tables)
    with engine.begin() as db:
        assert db.scalar(text('SELECT current_database()'))=='office_desk_gear'
        assert db.scalar(text('SELECT current_schema()'))=='public'
        assert set(inspect(db).get_table_names(schema='public'))==names|{'alembic_version'}
        quote=db.dialect.identifier_preparer.quote
        db.exec_driver_sql('TRUNCATE TABLE '+', '.join('public.'+quote(name) for name in sorted(names))+' RESTART IDENTITY CASCADE')
    accounts=[]
    now=office_now().replace(microsecond=0)
    tomorrow=dt.datetime.combine(now.date()+dt.timedelta(days=1),dt.time(9))
    with SessionLocal() as db:
        role_emails={'employee':('employee@demo.invalid','Demo Employee','Сотрудник'),
                     'it_admin':('it@demo.invalid','Demo IT Admin','IT-администратор'),
                     'office_manager':('office@demo.invalid','Demo Office Manager','Офис-менеджер')}
        people={}
        for role,(email,name,title) in role_emails.items():
            password='Demo-'+secrets.token_hex(10)
            user=User(email=email,full_name=name,job_title=title,role=role,password_hash=hash_password(password),is_verified=True)
            db.add(user); db.flush()
            people[role]=user
            accounts.append(dict(id=user.id,email=email,password=password,role=user.role))
        for key,name in (('colleague','Demo Colleague'),('visitor','Demo Visitor')):
            password='Demo-'+secrets.token_hex(10)
            user=User(full_name=name,email=key+'@demo.invalid',password_hash=hash_password(password),role='employee',is_verified=True,job_title='Сотрудник')
            db.add(user); db.flush(); people[key]=user
            accounts.append(dict(id=user.id,email=user.email,password=password,role=user.role))
        db.commit()

        features={}
        for number,name in ((1,'Дополнительный монитор'),(2,'USB Type-C'),(3,'Тихая зона'),(4,'Регулируемый стол')):
            item=DeskFeature(id=number,name=name,description='Demo feature')
            db.add(item); features[number]=item
        db.flush()
        desks={}
        for number in [*range(101,121),*range(201,206)]:
            if number not in desks:
                desk=Desk(number=number,floor=number//100,section=1 if number%100<=10 else 2,sockets=2 if number%2 else 4,is_free=number!=202)
                desk.feature_records=[features[1],features[2 if number%2 else 3]]
                db.add(desk); desks[number]=desk
        db.flush()
        devices=[Equipment(type='Ноутбук',model='ThinkPad Demo',inventory_number='DEMO-001',socket_serial='DEMO-SN-001',socket_needed=True,status='assigned',assigned_user_id=people['employee'].id),
                 Equipment(type='Монитор',model='Display Demo',inventory_number='DEMO-002',socket_serial='DEMO-SN-002',socket_needed=True,status='assigned',desk_id=desks[101].id)]
        db.add_all(devices)
        for index,kind,desk,user,status in (
            (3,'Принтер',None,people['visitor'],'assigned'),(4,'Планшет',None,None,'in_stock'),
            (5,'Клавиатура',None,people['employee'],'assigned'),(6,'Сканер',None,None,'in_stock'),
            (7,'Док-станция',desks[102],None,'assigned'),(8,'Монитор',desks[201],None,'assigned'),
            (9,'Ноутбук',None,people['colleague'],'assigned'),(10,'Планшет',None,None,'in_repair'),
            (11,'Маршрутизатор',None,None,'written_off'),(12,'Проектор',None,None,'in_stock')):
            item=Equipment(type=kind,model='Demo Model '+str(index),inventory_number=f'DEMO-{index:03}',socket_serial=f'DEMO-SN-{index:03}',
                           socket_needed=True,desk_id=desk.id if desk else None,assigned_user_id=user.id if user else None,status=status)
            db.add(item); devices.append(item)
        db.flush()
        employee=people['employee']; admin=people['it_admin']
        for person,number,start,hours,cancelled in (
            (employee,101,now-dt.timedelta(minutes=30),2,False),
            (employee,102,tomorrow,2,False),
            (people['colleague'],201,tomorrow,3,False),
            (employee,101,dt.datetime.combine(now.date()-dt.timedelta(days=1),dt.time(9)),4,False),
            (employee,102,dt.datetime.combine(now.date()-dt.timedelta(days=2),dt.time(10)),2,True),
            (people['visitor'],205,tomorrow+dt.timedelta(days=2),2,False)):
            db.add(Booking(user_id=person.id,desk_id=desks[number].id,planed_at=start,duration=dt.timedelta(hours=hours),is_cancelled=cancelled,
                           cancellation_reason='Demo cancellation' if cancelled else None,cancelled_at=now if cancelled else None))
        db.commit()

        import qrcode
        picture=qrcode.make(settings.QR_BASE_URL+'/'+str(devices[0].id))
        stream=io.BytesIO(); picture.save(stream,format='PNG')
        activity=ActivityService(ActivityRepository(db))
        photo=activity.upload_image(employee,base64.b64encode(stream.getvalue()).decode())['photo_url']
        employee.avatar_url=photo
        db.commit()
        tickets=TicketService(TicketRepository(db),EquipmentRepository(db))
        for person,device,description,transitions in (
            (employee,devices[0],'Demo laptop does not turn on',()),
            (employee,devices[4],'Demo keyboard needs diagnostics',('in_progress',)),
            (people['colleague'],devices[8],'Demo laptop driver issue',('in_progress','resolved')),
            (people['visitor'],devices[2],'Demo printer replacement completed',('in_progress','resolved','closed'))):
            ticket=tickets.create_ticket(SimpleNamespace(equipment_id=device.id,description=description,photo_url=photo if person.id==employee.id else None),person.id)
            for target in transitions: tickets.update_status(ticket.id,target,admin.id)
            if transitions: activity.comment(ticket.id,admin,'Demo diagnostic comment')
        db.add(Notification(user_id=employee.id,text='Тестовые данные готовы для проверки приложения'))
        db.add(Notification(user_id=people['office_manager'].id,text='Проверьте тестовую загрузку рабочих мест'))
        db.commit()
        ReportService(ReportRepository(db),TicketRepository(db)).save_report(admin.id,now.date()-dt.timedelta(days=7),now.date())
        db.execute(text("SELECT setval(pg_get_serial_sequence('public.desk_features','id'),(SELECT max(id) FROM desk_features),true)"))
        db.commit()
        counts={name:db.scalar(text('SELECT count(*) FROM public.'+engine.dialect.identifier_preparer.quote(name))) for name in sorted(names)}
    credentials=settings.DATA_DIR/'.env.demo-users.json'
    credentials.write_text(json.dumps({'accounts':accounts,'seeded_at':now.isoformat(),'tomorrow':tomorrow.isoformat()},ensure_ascii=False,indent=2),encoding='utf-8')
    credentials.chmod(0o600)
    print(json.dumps({'reset_completed':True,'database':'office_desk_gear','rows':counts,'credentials_saved_privately':True},ensure_ascii=False))
    engine.dispose()


if __name__=='__main__':
    main()
