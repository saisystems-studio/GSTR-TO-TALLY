import http.client
import json
import socket
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

    def post(self, payload, headers=None):
        if not settings.TALLY_ENABLED or settings.TALLY_MOCK:
            raise TallyConnectionError("TALLY_DISABLED", "Tally integration is disabled")

        parsed, host, port = endpoint(self.base_url)
        connected, code, message = tcp_probe(host, port, self.connect_timeout)
        if not connected:
            raise TallyConnectionError(code, message)

        connection_class = (
            http.client.HTTPSConnection if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(host, port, timeout=self.connect_timeout)
        stage = "connect"

        try:
            connection.connect()
            if connection.sock:
                connection.sock.settimeout(self.read_timeout)

                stage = "read"
                body = payload if isinstance(payload, bytes) else payload.encode("utf-8")
                path = parsed.path or "/"
                if parsed.query:
                    path += f"?{parsed.query}"

                connection.request(
                    "POST",
                    path,
                    body=body,
                    headers=headers or {"Content-Type": "application/xml; charset=utf-8"},
                )
                response = connection.getresponse()
                self.last_http_status = response.status
                return response.read()
        except ConnectionRefusedError as exc:
            raise TallyConnectionError(
                "TALLY_CONNECTION_REFUSED", "Tally refused the HTTP connection"
            ) from exc
        except socket.timeout as exc:
            code = "TALLY_CONNECT_TIMEOUT" if stage == "connect" else "TALLY_READ_TIMEOUT"
            message = (
                "HTTP connection to Tally timed out"
                if stage == "connect"
                else "Tally accepted the connection but did not return an HTTP response"
            )
            raise TallyConnectionError(code, message) from exc
        except (http.client.HTTPException, OSError) as exc:
            raise TallyConnectionError(
                "TALLY_INVALID_RESPONSE", f"Invalid response from Tally: {exc}"
            ) from exc
        finally:
            connection.close()

    def import_data(self, payload):
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
