"""Keep actionable network failures visible without weakening TLS validation."""
from pathlib import Path
import socket
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
from urllib3.exceptions import MaxRetryError
from client.services.api_client import ApiClient, ApiError


class NetworkErrorTests(unittest.TestCase):
    def request_error(self, error):
        def transport(*_args, **kwargs):
            self.assertNotIn("verify", kwargs)
            raise error
        api = ApiClient(transport=transport)
        try:
            with self.assertRaises(ApiError) as caught:
                api.check_connection()
            self.assertEqual(api._active, 0)
            return str(caught.exception)
        finally:
            api.close()

    def test_nested_dns_failure_has_its_own_message(self):
        nested = MaxRetryError(None, "/system/info", reason=socket.gaierror(11001, "getaddrinfo failed"))
        self.assertIn("DNS", self.request_error(requests.exceptions.ConnectionError(nested)))

    def test_tls_failure_does_not_retry_without_verification(self):
        self.assertIn("сертификат", self.request_error(requests.exceptions.SSLError("certificate verify failed")))

    def test_proxy_failure_and_timeout_remain_distinct(self):
        self.assertIn("прокси", self.request_error(requests.exceptions.ProxyError("proxy unavailable")))
        self.assertIn("не отвечает", self.request_error(requests.exceptions.Timeout("timed out")))

    def test_unclassified_connection_failure_keeps_general_message(self):
        self.assertIn("Нет соединения", self.request_error(requests.exceptions.ConnectionError("connection refused")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
