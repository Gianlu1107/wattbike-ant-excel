# Wattbike ANT+ Logger

Registra i **dati raw ANT+** dalla **Wattbike Pro / Trainer** (chiavetta USB ANT+) e li salva in **Excel / CSV**. Include una **GUI** desktop, setup driver automatico e aggiornamenti da GitHub Releases.

Profilo usato: **ANT+ Bicycle Power** (device type 11) — potenza, cadenza, byte grezzi.

## Download (consigliato)

Dalla [pagina Releases](https://github.com/Gianlu1107/wattbike-ant-excel/releases) scarica **un solo installer** per la tua piattaforma:

| File | Piattaforma |
|------|-------------|
| `WattbikeLogger-windows-x64-Setup.exe` | Windows (wizard Inno Setup) |
| `WattbikeLogger-macos-arm64.pkg` | macOS Apple Silicon (Installer.app) |
| `WattbikeLogger-linux-x64.deb` | Linux x64 (Debian/Ubuntu) |

> macOS Intel: i runner GitHub `macos-13` non sono più disponibili; su Intel usa `pip install` + `python -m wattbike_logger`, oppure una macchina Apple Silicon.

### Installazione

**macOS** — doppio click sul `.pkg` → segui i passaggi (Benvenuto → Licenza → Installa). L’app va in **Applicazioni** e si apre senza Terminale. Se Gatekeeper blocca: clic destro → Apri → Apri.

**Windows** — esegui `WattbikeLogger-windows-x64-Setup.exe` e segui il wizard (cartella, scorciatoie, Avvia). SmartScreen: Altre info → Esegui comunque.

**Linux (Debian/Ubuntu)** — doppio click sul `.deb` oppure:

```bash
sudo apt install ./WattbikeLogger-linux-x64.deb
# oppure: sudo dpkg -i WattbikeLogger-linux-x64.deb
```

All’avvio l’app verifica i driver ANT+ e cerca aggiornamenti.

Per pubblicare una release: `git tag v1.3.3 && git push origin v1.3.3` (GitHub Actions crea pkg / Setup.exe / deb).

## Requisiti (da sorgente)

- Python 3.10+
- Chiavetta USB ANT+ (VID `0FCF`, es. Garmin / Decathlon)
- Wattbike accesa

```bash
git clone https://github.com/Gianlu1107/wattbike-ant-excel.git
cd wattbike-ant-excel
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
```

## Driver ANT+

All’avvio (GUI) o con `python -m wattbike_logger setup-drivers`:

| OS | Automatico |
|----|------------|
| Windows | Filtro **libusb-win32** (UAC); fallback Zadig |
| Linux | Regole **udev** (`pkexec` / `sudo`) |
| macOS | Verifica **libusb** (`brew` se manca); l’exe ufficiale la include |

Manuale se serve: [Zadig](https://zadig.akeo.ie/) → stick ANT → **libusb-win32**. Chiudi Zwift/TrainerRoad durante la registrazione.

## Uso GUI

```bash
python -m wattbike_logger
# oppure: ./run.sh   |   run.bat
```

1. Device ID (es. `54434`, oppure `0` = qualsiasi)
2. **Start** → countdown **3 · 2 · 1 · VIA!** (apre la chiavetta)
3. Pedala — metriche e grafici live
4. **Stop** → scegli dove salvare Excel/CSV

## Uso CLI

```bash
python -m wattbike_logger scan
python -m wattbike_logger record -i 54434 -o sessione.xlsx --csv
python -m wattbike_logger record -i 54434 --unique-events -o sessione.xlsx
python -m wattbike_logger demo -d 30 -o prova.xlsx --csv
python -m wattbike_logger setup-drivers
```

Default registrazione: **RX-scan** (radio al 100%). `--mode paired` è più stabile ma perde molti pacchetti.

### Frequenza dati

- Broadcast ANT+ tipico: **~0.25 s** (ritrasmissioni incluse, in RX-scan)
- Watt *nuovi* (`event_count`): circa **1 per giro di pedale** (es. ~0.67 s a 90 rpm)
- **0.20 s** di watt grezzi nuovi non sono previsti dal profilo / dalla Wattbike

## Colonne Excel (`raw`)

| Colonna | Significato |
|--------|-------------|
| `timestamp_iso` / `elapsed_s` | orario e secondi di sessione |
| `device_id` / `device_type` | ID e tipo ANT+ |
| `page` / `page_name` / `event_count` | pagina e contatore aggiornamento |
| `instantaneous_power_w` / `average_power_w` | watt |
| `cadence_rpm` | cadenza |
| `left_power_w` / `right_power_w` | bilanciamento se presente |
| `raw_bytes_hex` | 8 byte payload grezzi |

Foglio `meta`: info sessione e statistiche frequenza.

## Sviluppo

```bash
pip install -r requirements.txt -r requirements-build.txt
python smoke_test.py
pyinstaller --noconfirm WattbikeLogger.spec
```

Installer locali:

```bash
# macOS → out/WattbikeLogger-macos-arm64.pkg
./scripts/macos/pkg/build_pkg.sh

# Linux → out/WattbikeLogger-linux-x64.deb
./scripts/linux/build_deb.sh

# Windows (con Inno Setup 6 installato) → out/WattbikeLogger-windows-x64-Setup.exe
iscc /DMyAppVersion=1.3.3 /DSourceExe=dist\WattbikeLogger-windows-x64.exe /DOutputDir=%cd%\out scripts\windows\WattbikeLogger.iss
```

Vedi [CHANGELOG.md](CHANGELOG.md).

## Licenza

[MIT](LICENSE) — uso libero per allenamento / analisi personale.
