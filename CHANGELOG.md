# Changelog

## 1.3.2

- Fix crash driver status (`accessible`)
- Installer per OS: macOS `.app` + `Install Wattbike Logger.command` (niente Terminale / Gatekeeper)
- Windows zip con script PowerShell; Linux zip con `install.sh`
- GUI windowed senza console
- Grafici con assi, griglia e scale utili (W / rpm / tempo)
- Start disabilitato se nessuna chiavetta ANT+ è collegata

## 1.3.1

- Grafici in tkinter puro (niente matplotlib/numpy) → eseguibili più leggeri
- CI: action aggiornate a runtime Node 24; UPX dove disponibile

## 1.3.0

- GUI desktop (Start/Stop, countdown 3-2-1-VIA, live metrics, charts)
- Auto-setup driver ANT+ per Windows / macOS / Linux
- Auto-update da GitHub Releases
- Build multi-piattaforma (Windows x64, macOS arm64, Linux x64)
- Registrazione RX-scan con fallback paired; export Excel/CSV

## 1.2.0

- Interfaccia grafica iniziale e packaging PyInstaller

## 1.1.0

- RX-scan, filtro metriche, frequenza/cadenza documentate
