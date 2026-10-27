from __future__ import annotations

from typing import Any, Callable, Optional

import requests

from client.core.config import API_BASE_URL, REQUEST_TIMEOUT


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None,
                 payload: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


class ApiClient:
    def __init__(self, base_url: str = API_BASE_URL, timeout: float = REQUEST_TIMEOUT,
                 transport: Optional[Callable[..., Any]] = None) -> None:
        self.base_url = (base_url or API_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self._token: Optional[str] = None
        self._transport = transport or requests.request
        self.on_auth_lost: Optional[Callable[[str], None]] = None

    @property
    def token(self) -> Optional[str]:
        return self._token

    def set_token(self, token: Optional[str]) -> None:
        self._token = token or None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _message(payload: Any, status_code: int) -> str:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
        return f"Ошибка сервера ({status_code})"

    def _request(self, method: str, path: str, *, params=None, json=None,
                 allow_404: bool = False):
        try:
            response = self._transport(
                method, f"{self.base_url}{path}", params=params, json=json,
                headers=self._headers(), timeout=self.timeout,
            )
        except requests.exceptions.Timeout as error:
            raise ApiError("Сервер не отвечает. Попробуйте позже") from error
        except Exception as error:
            raise ApiError("Ошибка соединения. Попробуйте позже") from error

        status_code = int(getattr(response, "status_code", 0) or 0)
        if allow_404 and status_code == 404:
            return None
        payload = None
        if status_code != 204:
            try:
                payload = response.json()
            except Exception:
                payload = None
        if not 200 <= status_code < 300:
            message = self._message(payload, status_code)
            if status_code == 401 and callable(self.on_auth_lost):
                self.on_auth_lost(message)
            raise ApiError(message, status_code,
                           payload if isinstance(payload, dict) else None)
        return payload

    def register(self, full_name, email, password, role="employee", job_title=""):
        return self._request("POST", "/auth/register", json={
            "full_name": full_name.strip(), "email": email.strip().lower(),
            "password": password, "role": role, "job_title": job_title.strip(),
        })

    def check_connection(self):
        data = self._request("GET", "/system/info")
        if not isinstance(data, dict) or data.get("service") != "office-desk-gear":
            raise ApiError("По этому адресу работает другой сервис")
        return data

    def login(self, email, password):
        return self._request("POST", "/auth/login", json={
            "email": email.strip().lower(), "password": password,
        })

    def logout(self):
        self._request("POST", "/auth/logout")
        self.set_token(None)

    def request_password_reset(self, email):
        return self._request("POST", "/auth/password-reset/request",
                             json={"email": email.strip().lower()})

    def confirm_password_reset(self, email, code, new_password):
        return self._request("POST", "/auth/password-reset/confirm", json={
            "email": email.strip().lower(), "code": code,
            "new_password": new_password,
        })

    def get_me(self):
        return self._request("GET", "/users/me")

    def update_me(self, full_name=None, email=None, job_title=None):
        body = {key: value for key, value in {
            "full_name": full_name, "email": email, "job_title": job_title,
        }.items() if value is not None}
        return self._request("PATCH", "/users/me", json=body)

    def get_desks(self, floor=None, section=None, sockets=None, features=None):
        params = {key: value for key, value in {
            "floor": floor, "section": section, "sockets": sockets,
        }.items() if value is not None}
        if features:
            params["features"] = ",".join(str(value) for value in features)
        result = self._request("GET", "/desks", params=params)
        if isinstance(result, dict) and "items" in result:
            return result.get("items", [])
        return result

    def get_desk_features(self):
        return self._request("GET", "/desk-features")

    def get_bookings(self, desk_id=None, date=None):
        params = {key: value for key, value in {
            "desk_id": desk_id, "date": date,
        }.items() if value is not None}
        return self._request("GET", "/bookings", params=params)

    def create_booking(self, desk_id, planed_at, duration_hours):
        return self._request("POST", "/bookings", json={
            "desk_id": desk_id, "planed_at": planed_at,
            "duration_hours": duration_hours,
        })

    def create_booking_period(self, desk_id, start_date, end_date,
                              daily_duration_hours):
        return self._request("POST", "/bookings/period", json={
            "desk_id": desk_id, "start_date": start_date,
            "end_date": end_date, "daily_duration_hours": daily_duration_hours,
        })

    def cancel_booking(self, booking_id):
        self._request("DELETE", f"/bookings/{booking_id}")

    def get_active_booking(self):
        return self._request("GET", "/bookings/active", allow_404=True)

    def get_booking_history(self):
        return self._request("GET", "/bookings/history")

    def get_my_equipment(self):
        return self._request("GET", "/equipment/my")

    def get_equipment(self, equipment_id):
        return self._request("GET", f"/equipment/{equipment_id}")

    def create_ticket(self, equipment_id, description, photo_url=None):
        body = {"equipment_id": equipment_id, "description": description}
        if photo_url is not None:
            body["photo_url"] = photo_url
        return self._request("POST", "/tickets", json=body)

    def get_my_tickets(self):
        return self._request("GET", "/tickets/my")

    def admin_create_desk(self, **fields):
        return self._request("POST", "/admin/desks", json=fields)

    def admin_update_desk(self, desk_id, **fields):
        return self._request("PATCH", f"/admin/desks/{desk_id}",
                             json={k: v for k, v in fields.items() if v is not None})

    def admin_cancel_booking(self, booking_id, reason):
        self._request("DELETE", f"/admin/bookings/{booking_id}",
                      json={"reason": reason})

    def admin_create_equipment(self, type, socket_needed, desk_id=None,
                               assigned_user_id=None, socket_serial=None, status=None):
        body = {"type": type, "socket_needed": socket_needed}
        body.update({key: value for key, value in {
            "desk_id": desk_id, "assigned_user_id": assigned_user_id,
            "socket_serial": socket_serial, "status": status,
        }.items() if value is not None})
        return self._request("POST", "/admin/equipment", json=body)

    def admin_generate_qr(self, equipment_id):
        return self._request("GET", f"/admin/equipment/{equipment_id}/qr")

    def admin_assign_equipment(self, equipment_id, assigned_user_id=None, desk_id=None):
        body = {key: value for key, value in {
            "assigned_user_id": assigned_user_id, "desk_id": desk_id,
        }.items() if value is not None}
        return self._request("PATCH", f"/admin/equipment/{equipment_id}/assign",
                             json=body)

    def admin_update_ticket_status(self, ticket_id, status):
        return self._request("PATCH", f"/admin/tickets/{ticket_id}/status",
                             json={"status": status})

    def admin_get_analytics(self, start_date=None, end_date=None):
        params = {key: value for key, value in {
            "start_date": start_date, "end_date": end_date,
        }.items() if value is not None}
        return self._request("GET", "/admin/analytics", params=params)

    def admin_list_users(self):
        return self._request("GET", "/admin/users")

    def admin_list_equipment(self):
        return self._request("GET", "/admin/equipment")

    def admin_list_tickets(self):
        return self._request("GET", "/admin/tickets")


__all__ = ["ApiClient", "ApiError", "TEST_API"]
