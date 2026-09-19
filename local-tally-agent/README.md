# GSTR 2 Tally Local Agent

This Windows service connects the hosted application to TallyPrime on the same PC. It uses outbound HTTPS only; do not expose port 9000 or configure router port-forwarding.

1. The release runner builds the executable and installer, embeds the production HTTPS origin, and stages the one customer-facing installer under `deploy/downloads/`. The installer registers a per-user Windows startup entry.
2. The customer downloads and runs the installer once. On first launch the connector opens the normal GSTR2Tally website for sign-in and pairing; the customer does not enter an API URL, token, port, Python path, or command.
3. Credentials are stored with Windows DPAPI and the process uses a named mutex. All future launches are silent and reconnect automatically.

Developer/CI release (never run by a customer):

`.\release-connector.ps1 -Origin https://your-production-origin -Version 1.0.0`

The release runner needs Python, the build requirements, and Inno Setup 6. It produces `local-tally-agent/out/GSTR2TallyConnectorSetup.exe` and automatically stages the same file at `deploy/downloads/GSTR2TallyConnectorSetup.exe`. Deploy that staged file to `/var/www/gstr2tally/downloads/GSTR2TallyConnectorSetup.exe` with the web release. The installer is the only customer-facing artifact. It starts the connector after installation and registers it for future Windows sign-ins.

The agent checks local Tally at `http://127.0.0.1:9000`, sends a heartbeat every few seconds, and claims only jobs assigned to its own device. TLS certificate verification remains enabled for VPS calls. Tally port 9000 is never listened on by the connector and must remain excluded from Nginx/router forwarding.
