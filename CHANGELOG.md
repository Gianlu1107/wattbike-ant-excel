# Changelog

## 1.5.0

- Bluetooth LE in parallelo all'ANT+: CPS/CSC della Wattbike Pro/Trainer (stesso flusso Hub/Zwift)
- GUI: checkbox «Anche Bluetooth»; Start anche solo-BLE se la bici è in radio
- CLI: `ble-scan`, `record --ble`; colonna Excel `source` (`ant+` / `ble`)

## 1.4.10

- Vendor `wdi-simple.exe` + `libusb-1.0.dll` in resources (CI Windows affidabile)

## 1.4.9

- Comando `diagnose` (JSON su file) per verificare chiavetta da remoto/CI
- Console allocata per i comandi CLI su Windows (exe windowed)
- CI: macOS 15; release anche se una piattaforma fallisce

## 1.4.8

- Windows: `wdi-simple` usa `--dest` in AppData (evita Access denied su cartella `usb_driver` in conflitto)

## 1.4.7

- CI: URL libusb Windows corretto (`v1.0.30`); download DLL non blocca se fallisce

## 1.4.6

- CI: `wdi-simple` OK; `libusb-1.0.dll` opzionale (non blocca più la release Windows)

## 1.4.5

- Fix script build `wdi-simple` (parser PowerShell / patch config.h)

## 1.4.4

- Fix build CI di `wdi-simple` (MSBuild quoting / config.h paths)

## 1.4.3

- Windows: `pnputil` con INF non firmati fallisce (`0xE000022F`) — installazione via **libwdi/wdi-simple** (come Zadig), silenziosa
- Reset PnP solo su `VID_0FCF` (non più su device «Participant» ecc.)
- Log driver in-app senza spam dallo stato hotplug
- Bundle `wdi-simple.exe` + `libusb-1.0.dll` nel Setup Windows

## 1.4.2

- Windows: rileva la chiavetta anche in «Altri dispositivi» (warning) e installa driver di funzione libusb-win32 + WinUSB
- Installazione driver silenziosa (niente finestre Terminale): solo UAC + log nella finestra dell’app
- Pannello log driver in-app; setup Inno con `runhidden`

## 1.4.1

- Universale/dinamico: Device ID default **0 (auto)**; rileva qualsiasi PowerMeter
- Driver Windows: filtri per tutti i PID ANT rilevati (PnP + seed), non solo lista fissa
- Linux udev: regola per **qualsiasi** stick VID `0fcf`
- Mostra USB PID della stick e Device ID ANT+ rilevato in sessione

## 1.4.0

- Plug-and-play ANT+: all’avvio prepara i driver in automatico (Windows libusb-win32, Linux udev, macOS libusb)
- Hotplug: rileva la chiavetta appena collegata e abilita Start quando è apribile
- Pulsante **Driver** per riparazione manuale; Setup Windows configura i driver in post-install
- Pacchetto `.deb` installa le regole udev ANT+; più PID USB supportati
- `setup-drivers` funziona anche dall’eseguibile frozen

## 1.3.5

- macOS `.pkg` installa sempre in `/Applications` (`BundleIsRelocatable=false`)

## 1.3.4

- Fix auto-update su macOS con `.pkg` (`Exec format error`)
- Zip `.app` per client vecchi; documentazione Gatekeeper

## 1.3.3

- Installer nativi: macOS `.pkg`, Windows `Setup.exe`, Linux `.deb`

## 1.3.2

- Fix `accessible`; grafici; Start senza chiavetta disabilitato

## 1.3.1

- Grafici tkinter puro; CI Node 24; UPX

## 1.3.0

- GUI desktop, driver auto, aggiornamenti, build multi-OS, RX-scan
