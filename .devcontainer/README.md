# Silnik AI 3D Studio w GitHub Codespaces

Silnik działa w chmurze GitHuba (darmowy limit konta), nie na Twoim komputerze.

1. Poczekaj na komunikat w terminalu **„Silnik AI 3D Studio działa”** — pierwsze uruchomienie instaluje pakiety (3–5 min).
2. Otwórz link **Strona: https://hauknamatata.github.io/3d-site/?engine=…** z terminala — strona sama zapamięta adres silnika.
3. Agenci AI (Claude Code, Gemini CLI) łączą się z adresem **MCP** z terminala.

Codespace wyłącza się po bezczynności (GitHub → Settings → Codespaces → Default idle timeout, max 240 min).
Uruchom go ponownie z github.com → Code → Codespaces — silnik wystartuje sam.
Adres silnika wyświetla też: `cat /tmp/ai3d-engine-url`. Log: `tail -f /tmp/ai3d-engine.log`.
