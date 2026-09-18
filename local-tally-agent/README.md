# GSTR 2 Tally Local Agent

This Windows service connects the hosted application to TallyPrime on the same PC. It uses outbound HTTPS only; do not expose port 9000 or configure router port-forwarding.

1. Build and sign the executable as a developer, then build `GSTR2TallyConnector.iss` with Inno Setup. The installer writes the signed release origin into the bundle and registers a per-user Windows startup entry.
2. The customer downloads and runs the installer once. On first launch the connector opens the normal GSTR2Tally website for sign-in and pairing; the customer does not enter an API URL, token, port, Python path, or command.
3. Credentials are stored with Windows DPAPI and the process uses a named mutex. All future launches are silent and reconnect automatically.

Developer build:

`.\build-exe.ps1 -Origin https://your-production-origin`

Then open `GSTR2TallyConnector.iss` in Inno Setup and compile it. The installer is the only customer-facing artifact. It starts the connector after installation and registers it for future Windows sign-ins.

The agent checks local Tally at `http://127.0.0.1:9000`, sends a heartbeat every few seconds, and claims only jobs assigned to its own device. TLS certificate verification remains enabled for VPS calls. Tally port 9000 is never listened on by the connector and must remain excluded from Nginx/router forwarding.
