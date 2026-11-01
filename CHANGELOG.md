# Changelog

## 1.6.4

- **Fix Analisi Sportiva & Grafici Telemetria**:
  - Risolto bug calcolo `t0` e tempo di applicazione `t_app`: eliminata l'uguaglianza non valida $t_0 == t_x$ ($t_{\text{app}} = 0$). Il punto di inizio applicazione $t_0$ rileva correttamente la transizione dal riposo/zero garantendo $t_0 < t_x$ e $t_{\text{app}} > 0$.
  - Ricalcolata la decadenza per i picchi multipli secondo la specifica sportiva: lo Sprint 1 ($n_1$) funge da riferimento base (`— (Base)`); per ogni sprint successivo ($n_2, n_3, \dots$) la decadenza $\Delta\text{Watt}$ e $\%$ è calcolata rispetto a $n_1$, illustrando chiaramente il decremento delle prestazioni nel tempo.
  - Aggiunti i marcatori e label dei picchi di cadenza sul grafico «Cadenza Istantanea» (allineati ai picchi sprint).
  - Eliminati gli artefatti e i cappi grafici (overshoots da interpolazione spline `smooth=True`) nei tracciati di potenza e cadenza, rendendo il rendering fedele e pulito.

## 1.6.3

- **Restyling e Polish Completo UI Windows Classic**:
  - Risolto definitivamente il contrasto e il tema su macOS: forzata l'appearance Light (`NSAppearanceNameAqua`) a livello Cocoa per impedire che la Dark Mode di sistema renda scuri e illeggibili i pannelli e i testi.
  - Rimossi i rozzi glifi bitmap disegnati manualmente dai pulsanti e dalle schede; ripristinata la tipografia pulita ed elegante in stile utility Windows (Windows XP / 2000 dialog beige `#ece9d8`).
  - Riquadri metriche ridisegnati come pannelli digitali bianchi incassati (`#ffffff` con bevel 3D), etichette scure nitide ad alto contrasto e numeri grandi in grassetto puro nero.
  - Schede e tabelle formattate senza sovrapposizioni o testi troncati nelle colonne.

## 1.6.2

- **Auto-updater macOS & Cross-Platform Fixes**:
  - Risolta la verifica certificati SSL su macOS frozen: caricamento automatico delle CA di sistema (`/etc/ssl/cert.pem` e `certifi`) evitando fallimenti silenziosi del controllo release.
  - Sostituito `sys.exit(0)` con `os._exit(0)` nel thread di aggiornamento per consentire l'uscita immediata del processo principale e l'esecuzione corretta dello script di sostituzione.
  - Prioritizzazione del pacchetto nativo `.pkg` quando `/Applications/WattbikeLogger.app` richiede privilegi di root.
  - `postinstall` configurato per garantire i permessi al gruppo `admin` per futuri aggiornamenti diretti in-place senza prompt password.

## 1.6.1

- Sostituite tutte le emoji con icone grafiche native 16x16 pixel in stile Windows classico
- Nomi modalità semplificati in «Picco Singolo» e «Picco Multiplo» (rimosse le parentesi e suffissi)
- Risolto bug e lag nel passaggio tra schede: transizione istantanea senza reflow, rendering immediato dei dati e fix thread driver
- Migliorato auto-updater macOS: supporto per aggiornamenti di bundle `.app` protetti in `/Applications` tramite prompt amministratore nativo

## 1.6.0

- Deduplicazione cross-source in tempo reale tra ANT+ e Bluetooth LE (CPS) sullo stesso evento
- Calcolo matematico automatico della coppia (Torque $\tau = P / \omega$) e velocità angolare anche per ANT+
- Nuove modalità di analisi sportiva: **Picco Singolo** (TTP / Sprint Max) e **Picco Multiplo** (Repeated Sprints con decadenza)
- Riconoscimento e segmentazione dei picchi multipli tramite la fila di zeri (intervallo di recupero)
- Calcolo indice di decadenza (Fatigue Index): differenza in Watt/% dal picco massimo assoluto e dalla cadenza massima; classifiche ordinate cronologicamente e decrescentemente
- Polish UI stile Windows classico: Notebook a 2 schede («Monitor Live & Grafici» e «Report Analisi Risultati»), 6 riquadri metriche live con Coppia (Nm), marcatori dei picchi sul grafico, pulsante «Apri CSV/Excel»
- Foglio «analisi» formattato automaticamente nell'export Excel (`.xlsx`) accanto ai fogli «raw» e «meta»

## 1.5.4

- BLE: max frequenza sui payload nuovi; dedup solo spam identico; zeri a riposo ~4 Hz
- Cadenza BLE non azzerata sui glitch CSC se stai ancora spingendo
- ANT: dedup solo ritrasmissioni bit-identiche (event_count+raw)

## 1.5.3

- macOS `.app`: `CFBundleShortVersionString` preso da `__version__` (non più fisso 1.4.10)

## 1.5.2

- BLE L/R allineati ad ANT+ (balance Wattbike era specchiato)

## 1.5.1

- BLE: stessi campi ANT+ (cadenza CSC, L/R, media, coppia, ω); zeri a riposo
- Dedup notifiche CPS identiche (la Wattbike rinotificava lo stesso payload)

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
