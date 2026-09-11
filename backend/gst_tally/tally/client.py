import http.client
import json
import socket
import threading
from urllib.parse import urlparse

from django.conf import settings

from .json_response_parser import parse_json_response
from .response_parser import parse_response


class TallyConnectionError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def endpoint(base_url=None):
    parsed = urlparse(base_url or settings.TALLY_BASE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TallyConnectionError("TALLY_INVALID_URL", "Configured Tally URL is invalid")
    return parsed, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def tcp_probe(host, port, timeout=None):
    try:
        with socket.create_connection(
            (host, int(port)), timeout=timeout or settings.TALLY_CONNECT_TIMEOUT
        ):
            pass
        return True, "", ""
    except ConnectionRefusedError:
        return False, "TALLY_CONNECTION_REFUSED", "Tally refused the TCP connection"
    except socket.timeout:
        return False, "TALLY_CONNECTION_TIMEOUT", "TCP connection to Tally timed out"
    except socket.gaierror as exc:
        return False, "TALLY_HOST_UNREACHABLE", f"Tally host is not reachable: {exc}"
    except OSError as exc:
        return False, "TALLY_PORT_UNREACHABLE", f"Tally port is not reachable: {exc}"


class TallyClient:
    def __init__(self, base_url=None, connect_timeout=None, read_timeout=None, timeout=None):
        self.base_url = (base_url or settings.TALLY_BASE_URL).rstrip("/")
        self.connect_timeout = connect_timeout or min(
            timeout or settings.TALLY_CONNECT_TIMEOUT,
            settings.TALLY_CONNECT_TIMEOUT,
        )
        self.read_timeout = read_timeout or timeout or settings.TALLY_READ_TIMEOUT
        self.last_http_status = None
        self._connection = None
        self._connection_endpoint = None
        self._connection_lock = threading.Lock()

    def close(self):
        connection = self._connection
        self._connection = None
        self._connection_endpoint = None
        if connection is not None:
            connection.close()

    def _open_connection(self, parsed, host, port):
        connected, code, message = tcp_probe(host, port, self.connect_timeout)
        if not connected:
            raise TallyConnectionError(code, message)
        connection_class = (
            http.client.HTTPSConnection if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(host, port, timeout=self.connect_timeout)
        connection.connect()
        if connection.sock:
            connection.sock.settimeout(self.read_timeout)
        self._connection = connection
        self._connection_endpoint = (parsed.scheme, host, port)
        return connection

    def post(self, payload, headers=None):
        if not settings.TALLY_ENABLED or settings.TALLY_MOCK:
            raise TallyConnectionError("TALLY_DISABLED", "Tally integration is disabled")

        parsed, host, port = endpoint(self.base_url)
        endpoint_key = (parsed.scheme, host, port)
        with self._connection_lock:
            connection = self._connection
            if connection is None or self._connection_endpoint != endpoint_key:
                self.close()
                connection = self._open_connection(parsed, host, port)
            try:
                body = payload if isinstance(payload, bytes) else payload.encode("utf-8")
                path = parsed.path or "/"
                if parsed.query:
                    path += f"?{parsed.query}"

                request_headers = dict(headers or {"Content-Type": "application/xml; charset=utf-8"})
                if settings.TALLY_HTTP_KEEPALIVE:
                    request_headers.setdefault("Connection", "keep-alive")
                connection.request(
                    "POST",
                    path,
                    body=body,
                    headers=request_headers,
                )
                response = connection.getresponse()
                self.last_http_status = response.status
                raw = response.read()
                if not settings.TALLY_HTTP_KEEPALIVE or response.will_close:
                    self.close()
                return raw
            except ConnectionRefusedError as exc:
                self.close()
                raise TallyConnectionError(
                    "TALLY_CONNECTION_REFUSED", "Tally refused the HTTP connection"
                ) from exc
            except socket.timeout as exc:
                self.close()
                raise TallyConnectionError(
                    "TALLY_READ_TIMEOUT", "Tally accepted the connection but did not return an HTTP response"
                ) from exc
            except (http.client.HTTPException, OSError) as exc:
                self.close()
                raise TallyConnectionError(
                    "TALLY_INVALID_RESPONSE", f"Invalid response from Tally: {exc}"
                ) from exc

    def import_data(self, payload):
        return parse_response(self.post(payload))

    def import_data_batch(self, payload):
        """Send one multi-voucher Import Data envelope over the reused link."""
        return parse_response(self.post(payload))

    def import_json(self, payload, object_id="All Masters"):
        """Import native Tally JSONEx data.

        The body is kept as native JSON (not converted to XML).  The same
        JSONEx master structure can therefore be used by code and by Tally's
        manual JSON import flow.
        """
        headers = {
            "Content-Type": "application/json",
            "version": "1",
            "tallyrequest": "Import",
            "type": "Data",
            "id": object_id,
            "detailed-response": "Yes",
        }
        body = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        raw = self.post(body, headers=headers)
        return parse_json_response(raw)
