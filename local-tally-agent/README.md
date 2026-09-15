# GSTR 2 Tally Local Agent

This Windows service connects the hosted application to TallyPrime on the same PC. It uses outbound HTTPS only; do not expose port 9000 or configure router port-forwarding.

1. Provision the agent from an authenticated GSTR 2 Tally user session. Store the returned token only on this PC.
2. During development run `python tally_local_agent.py --install --server-url https://YOUR-VPS --token YOUR_ONE_TIME_TOKEN`, then `python tally_local_agent.py`.
3. Register automatic sign-in startup:

```powershell
.\build-exe.ps1
.\dist\GSTR2TallyAgent.exe --install --server-url https://YOUR-VPS --token YOUR_ONE_TIME_TOKEN
.\install-startup.ps1 -AgentExecutable C:\Path\GSTR2TallyAgent.exe
```

The agent checks local Tally at `http://127.0.0.1:9000`, sends a heartbeat every few seconds, and claims only jobs assigned to its own device. TLS certificate verification remains enabled for VPS calls.
