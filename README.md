# Wattbike ANT+ → Excel

Programmino Python che legge i **dati raw ANT+** dalla **Wattbike Pro / Trainer** tramite la chiavetta USB ANT+ (es. Decathlon) e li salva in un file **Excel (.xlsx)** — senza elaborazioni: li processerai tu dopo.

La Wattbike trasmette come **ANT+ Bicycle Power** (device type 11): potenza istantanea, potenza media, cadenza, e (se presenti) pagine di coppia. Ogni pacchetto ricevuto diventa una riga, con anche i **8 byte grezzi** in esadecimale.

## Requisiti

- Python 3.10+
- Chiavetta USB ANT+ (Garmin / Dynastream / Decathlon compatibile, tipicamente VID `0FCF`)
- Wattbike Pro o Trainer accesa

### Installazione

```bash
cd wattbike-ant-excel
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### Driver chiavetta

**Windows**

1. Inserisci la chiavetta.
2. Se non è già usata da Zwift/altro software ANT, installa il driver **libusb-win32** con [Zadig](https://zadig.akeo.ie/):
   - Options → *List All Devices*
   - seleziona lo stick ANT (VID `0FCF`, PID spesso `1008` o `1009`)
   - scegli **libusb-win32** (non WinUSB)
   - Install Driver → ristacca la chiavetta
3. Chiudi Zwift / TrainerRoad / ecc. mentre registri (la stick è usata in esclusiva).

**Linux**

```bash
sudo python -m openant.udev_rules
# poi scollega e ricollega la chiavetta
```

**macOS**: di solito basta `libusb` (`brew install libusb`).

## Uso

### 1) Trova l’ID della Wattbike

Accendi la bici, pedala un po’, poi:

```bash
python -m wattbike_logger scan
```

Annota il `device_id` stampato (es. `12345`).

### 2) Registra e salva Excel

```bash
# default: RX-scan (radio al 100%) — cattura quasi tutti i broadcast (~0.25 s)
python -m wattbike_logger record -i 54434 -o sessione.xlsx --csv

# solo quando i watt cambiano davvero (1 riga ≈ 1 pedalata)
python -m wattbike_logger record -i 54434 -o sessione.xlsx --csv --unique-events

# canale paired (sconsigliato: spesso 1 pacchetto su 3–4)
python -m wattbike_logger record -i 54434 --mode paired -o sessione.xlsx
```

Alla fine della sessione stampa la **frequenza reale** ricevuta (Hz e dt mediano).

### Frequenza dati (0.2 s?)

Due livelli diversi:

1. **Broadcast ANT+** (~4 Hz, ogni **~0.25 s**): la bici *ritrasmette* lo stesso pacchetto. Con `--mode scan` (default) la stick ascolta al 100% e li prende quasi tutti. In `--mode paired` il canale “a sincronismo” ne perde molti → nei test ~ogni **2.2 s** (salta `event_count`).
2. **Watt davvero nuovi**: sulla Wattbike l’`event_count` sale circa **1 volta per giro di pedale**. A 90 rpm ≈ ogni **0.67 s** (non ogni 0.2 s). Non si può inventare potenza più veloce di così dallo stream ANT+.

- **0.20 s esatti (5 Hz) di watt grezzi**: non previsti. Opzioni: usare i broadcast a 0.25 s, oppure **interpolare offline** sulla griglia 0.2 s (non più raw).
- Se RX-scan va in timeout: scollega/ricollega la chiavetta; il logger fa fallback automatico a paired.
### 3) Prova senza hardware (demo)

```bash
python -m wattbike_logger demo -d 30 -o prova.xlsx --csv
```

## Colonne Excel (foglio `raw`)

| Colonna | Significato |
|--------|-------------|
| `timestamp_iso` | orario locale di ricezione |
| `elapsed_s` | secondi dall’inizio registrazione |
| `device_id` | ID ANT+ |
| `page` | numero pagina ANT+ (es. `16` = 0x10 power) |
| `page_name` | `standard_power` / `standard_torque` / altro |
| `event_count` | contatore aggiornamento ANT+ (byte 1) |
| `instantaneous_power_w` | watt istantanei |
| `average_power_w` | watt medi (dal protocollo / istantanea in scan) |
| `cadence_rpm` | cadenza |
| `left_power_w` / `right_power_w` | bilanciamento se disponibile |
| `torque_nm` | coppia (pagina torque) |
| `angular_velocity_rad_s` | velocità angolare |
| `raw_bytes_hex` | **8 byte payload ANT+ grezzi** |

Il foglio `meta` contiene info sulla sessione.

## Note

- Frequenza tipica ~4 Hz per la pagina potenza: una sessione da 1 h ≈ migliaia di righe (va bene per Excel/CSV).
- Non è l’export “workout file” del monitor Wattbike: è lo **stream live ANT+** come lo vedono Zwift e simili.
- Se la scansione non trova nulla: pedala, avvicina la stick, spegni altri app ANT, controlla i driver.
- Su alcune Wattbike compare anche come Fitness Equipment: questo tool intercetta il profilo **PowerMeter**, che è quello usato per potenza/cadenza.

## Licenza

Uso libero per allenamento / analisi personale.
