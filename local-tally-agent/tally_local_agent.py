"""Outbound-only Windows connector for GSTR 2 Tally.

Install this program on the same Windows computer as TallyPrime.  It never
opens a public listener: all traffic is HTTPS from this process to the VPS,
while Tally traffic remains on 127.0.0.1:9000.
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path.home() / "AppData" / "Local" / "GSTR2TallyAgent"
CONFIG_PATH = APP_DIR / "agent.json"
LOG_PATH = APP_DIR / "agent.log"
OUTBOX_PATH = APP_DIR / "result-outbox.json"
COMPANY_QUERY = b"""<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE><ID>GSTRLocalAgentCompanies</ID></HEADER><BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE><COLLECTION NAME=\"GSTRLocalAgentCompanies\"><TYPE>Company</TYPE><FETCH>NAME,GSTREGISTRATIONNUMBER</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"""
SERIAL_QUERY = b"""<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>FUNCTION</TYPE><ID>$$LicenseInfo</ID></HEADER><BODY><DESC><FUNCPARAMLIST><PARAM>SerialNumber</PARAM></FUNCPARAMLIST></DESC></BODY></ENVELOPE>"""


@dataclass(frozen=True)
class Config:
    server_url: str
    token: str
    tally_url: str = "http://127.0.0.1:9000"
    poll_seconds: int = 3


def load_config() -> Config:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return Config(**data)


def api(config: Config, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        config.server_url.rstrip("/") + path, data=data,
        headers={"X-Tally-Agent-Token": config.token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
        return response.status, json.loads(response.read().decode() or "{}")


def tally_post(config: Config, body: bytes, headers: dict | None = None) -> bytes:
    safe_headers = {str(key): str(value) for key, value in (headers or {}).items()
                    if str(key).lower() not in {"host", "content-length", "connection"}}
    safe_headers.setdefault("Content-Type", "application/xml; charset=utf-8")
    request = urllib.request.Request(config.tally_url, data=body, headers=safe_headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def read_tally_identity(config: Config) -> dict:
    try:
        company_xml = tally_post(config, COMPANY_QUERY)
        serial_xml = tally_post(config, SERIAL_QUERY)
        company_root, serial_root = ET.fromstring(company_xml), ET.fromstring(serial_xml)
        company = next(iter(company_root.iter("COMPANY")), None)
        return {
            "tally_reachable": True,
            "company_name": ((company.get("NAME") if company is not None else "") or "").strip(),
            "company_gstin": ((company.findtext("GSTREGISTRATIONNUMBER") if company is not None else "") or "").strip().upper(),
            "tally_serial": (serial_root.findtext(".//DATA/RESULT") or serial_root.findtext(".//RESULT") or "").strip(),
        }
    except (OSError, urllib.error.URLError, ET.ParseError) as exc:
        logging.info("Tally is unavailable: %s", exc)
        return {"tally_reachable": False, "company_name": "", "company_gstin": "", "tally_serial": ""}


def execute_job(config: Config, job: dict) -> dict:
    """Execute one server-authorized local Tally request exactly once."""
    payload = job.get("payload") or {}
    body = payload.get("body", payload.get("xml"))
    if not isinstance(body, str) or not body.strip():
        return {"success": False, "error": "Missing Tally job payload."}
    try:
        response = tally_post(config, body.encode("utf-8"), payload.get("headers"))
        text = response.decode("utf-8", "replace")
        # XML reports carry an explicit STATUS; JSONEx success is returned as
        # JSON and is handed unchanged to Django's existing parser.
        created = errors = 0
        try:
            root = ET.fromstring(text)
            errors = int(root.findtext(".//LINEERRORS") or "0")
            created = int(root.findtext(".//CREATED") or "0")
            success = (root.findtext(".//HEADER/STATUS") or "1").strip() == "1" and errors == 0
        except ET.ParseError:
            success = bool(text.strip())
        return {"success": success, "acknowledgement": {"created": created, "errors": errors, "raw_response": text[:20000]}}
    except Exception as exc:
        logging.exception("Tally job failed")
        return {"success": False, "error": str(exc)}


def run() -> None:
    config = load_config()
    while True:
        try:
            # A Tally response is written locally before it is uploaded. A
            # network drop after voucher creation therefore retries only the
            # acknowledgement, never the voucher request.
            if OUTBOX_PATH.exists():
                pending = json.loads(OUTBOX_PATH.read_text(encoding="utf-8"))
                api(config, pending["path"], pending["result"])
                OUTBOX_PATH.unlink()
            api(config, "/api/gst-tally/local-agent/heartbeat/", read_tally_identity(config))
            status, job = api(config, "/api/gst-tally/local-agent/next-job/")
            if status == 200 and job.get("job_id"):
                result = execute_job(config, job)
                path = f"/api/gst-tally/local-agent/jobs/{job['job_id']}/result/"
                OUTBOX_PATH.write_text(json.dumps({"path": path, "result": result}), encoding="utf-8")
                api(config, path, result)
                OUTBOX_PATH.unlink()
        except urllib.error.HTTPError as exc:
            logging.warning("Server rejected agent request: %s", exc.code)
        except Exception:
            logging.exception("Agent loop failed; retrying")
        time.sleep(max(1, config.poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url")
    parser.add_argument("--token")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.install:
        if not args.server_url or not args.token:
            parser.error("--install requires --server-url and --token")
        CONFIG_PATH.write_text(json.dumps({"server_url": args.server_url, "token": args.token}, indent=2), encoding="utf-8")
        print(f"Configured {CONFIG_PATH}. Run this program at Windows sign-in using Task Scheduler.")
        return
    run()


if __name__ == "__main__":
    main()
