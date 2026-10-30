# Changelog

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
