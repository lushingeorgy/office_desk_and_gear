"""SMTP wire delivery, TLS trust and connection cleanup without external mail."""
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path
import socketserver
import ssl
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.core.config import settings
from server.core.exceptions import ServiceError
from server.services.mail_service import send_code


class SmtpCatcher(socketserver.TCPServer):
    allow_reuse_address = True
    request_queue_size = 1

    def __init__(self, reject_data=False):
        self.messages = []
        self.commands = []
        self.closed = threading.Event()
        self.active_connections = 0
        self.reject_data = reject_data
        super().__init__(("127.0.0.1", 0), SmtpHandler)


class SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(2)
        self.server.active_connections += 1
        try:
            self.wfile.write(b"220 local SMTP test receiver\r\n")
            for _ in range(64):
                line = self.rfile.readline(4096)
                if not line:
                    return
                command = line.split(b" ", 1)[0].strip().upper()
                self.server.commands.append(command)
                if command == b"EHLO":
                    self.wfile.write(b"250-localhost\r\n250 SIZE 65536\r\n")
                elif command in {b"HELO", b"MAIL", b"RCPT", b"RSET"}:
                    self.wfile.write(b"250 OK\r\n")
                elif command == b"DATA":
                    self.wfile.write(b"354 End data with a dot\r\n")
                    data = bytearray()
                    while len(data) <= 65536:
                        part = self.rfile.readline(65536)
                        if not part:
                            raise AssertionError("SMTP client disconnected before ending DATA")
                        if part == b".\r\n":
                            break
                        data.extend(part[1:] if part.startswith(b"..") else part)
                    else:
                        raise AssertionError("SMTP test message exceeded its bounded size")
                    self.server.messages.append(bytes(data))
                    if self.server.reject_data:
                        self.wfile.write(b"554 smtp.internal.example rejected password-do-not-echo\r\n")
                    else:
                        self.wfile.write(b"250 Message accepted\r\n")
                elif command == b"QUIT":
                    self.wfile.write(b"221 Closing connection\r\n")
                    return
                else:
                    self.wfile.write(b"502 Unsupported command\r\n")
        finally:
            self.server.active_connections -= 1
            self.server.closed.set()


@contextmanager
def local_smtp(reject_data=False):
    server = SmtpCatcher(reject_data=reject_data)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02),
                              name="odg-test-smtp", daemon=True)
    worker.start()
    try:
        with patch.multiple(settings, ENVIRONMENT="production", MAIL_MODE="smtp", SMTP_HOST="127.0.0.1",
                            SMTP_PORT=server.server_address[1], SMTP_TLS=False, SMTP_USER="", SMTP_PASSWORD="",
                            SMTP_FROM="sender@example.test"):
            yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        if worker.is_alive():
            raise AssertionError("SMTP test receiver did not stop")


class MailServiceTests(unittest.TestCase):
    def test_production_smtp_delivers_parseable_code_and_closes_connection(self):
        with local_smtp() as server:
            send_code("recipient@example.test", "482910", "Восстановление пароля")
            self.assertTrue(server.closed.wait(2), "SMTP connection remained open after sending")
            self.assertEqual(0, server.active_connections)
            self.assertIn(b"QUIT", server.commands)
            self.assertEqual(1, len(server.messages))
            message = BytesParser(policy=policy.default).parsebytes(server.messages[0])
            self.assertEqual("sender@example.test", message["From"])
            self.assertEqual("recipient@example.test", message["To"])
            self.assertIn("код подтверждения", str(message["Subject"]))
            body = message.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("482910", body)
            self.assertIn("Восстановление пароля", body)

    def test_smtp_rejection_returns_safe_503_and_closes_connection(self):
        with local_smtp(reject_data=True) as server:
            with self.assertRaises(ServiceError) as raised:
                send_code("recipient@example.test", "482910", "Подтверждение электронной почты")
            self.assertEqual(503, raised.exception.status_code)
            for private in ("smtp.internal.example", "password-do-not-echo", "482910", "recipient@example.test"):
                self.assertNotIn(private, str(raised.exception))
            self.assertTrue(server.closed.wait(2), "Failed SMTP delivery left the connection open")
            self.assertEqual(0, server.active_connections)
            self.assertIn(b"QUIT", server.commands)

    def test_starttls_requires_trusted_certificate_and_hostname(self):
        sequence, contexts, connections = [], [], []

        class FakeSmtp:
            def __init__(self, *_args, **_kwargs):
                self.closed = False
                connections.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.closed = True

            def starttls(self, *, context):
                sequence.append("tls")
                contexts.append(context)

            def login(self, *_credentials):
                sequence.append("login")

            def send_message(self, _message):
                sequence.append("send")

        with patch.multiple(settings, MAIL_MODE="smtp", SMTP_HOST="smtp.example.test", SMTP_TLS=True,
                            SMTP_USER="test-user", SMTP_PASSWORD="test-password", SMTP_FROM="sender@example.test"):
            with patch("server.services.mail_service.smtplib.SMTP", FakeSmtp):
                send_code("recipient@example.test", "482910", "Подтверждение электронной почты")
        self.assertEqual(["tls", "login", "send"], sequence)
        self.assertEqual(ssl.CERT_REQUIRED, contexts[0].verify_mode)
        self.assertTrue(contexts[0].check_hostname)
        self.assertTrue(connections[0].closed)

    def test_untrusted_tls_certificate_is_safe_failure_and_closes_connection(self):
        connections = []

        class RejectingTls:
            def __init__(self, *_args, **_kwargs):
                self.closed = False
                connections.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.closed = True

            def starttls(self, *, context):
                raise ssl.SSLCertVerificationError("smtp.private.example certificate rejected")

        with patch.multiple(settings, MAIL_MODE="smtp", SMTP_HOST="smtp.private.example", SMTP_TLS=True):
            with patch("server.services.mail_service.smtplib.SMTP", RejectingTls):
                with self.assertRaises(ServiceError) as raised:
                    send_code("recipient@example.test", "482910", "Подтверждение электронной почты")
        self.assertEqual(503, raised.exception.status_code)
        self.assertNotIn("smtp.private.example", str(raised.exception))
        self.assertTrue(connections[0].closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
