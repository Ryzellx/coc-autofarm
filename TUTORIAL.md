# Tutorial: from zero to farming

This assumes a Windows PC with nothing installed. If you already have Python and
an emulator with Clash of Clans, skip to step 4.

Time: about 20 minutes, most of it waiting on `pip install`.

---

## 0. What you need

| Thing | Why |
|---|---|
| Windows 10/11 | the GUI and the ADB path are built for it |
| Python 3.11 or 3.12 | 3.13 breaks the torch/easyocr wheels |
| An Android emulator | LDPlayer 9 recommended; MuMu, BlueStacks, MEmu, Nox all work |
| Clash of Clans installed **inside the emulator**, logged in | the bot drives the emulator, not your phone |
| ~4 GB free disk | torch + easyocr are not small |

**Do not run this on your main phone.** Use an emulator, and assume the account
can be banned — Supercell does ban botters.

---

## 1. Install Python

1. Go to <https://www.python.org/downloads/release/python-3129/> and download
   **Windows installer (64-bit)**.
2. Run it. On the first screen, tick **"Add python.exe to PATH"** — this is the
   step people skip, and then nothing works.
3. Click *Install Now*.

Verify: open Command Prompt and run

```
py -3.12 --version
```

You should see `Python 3.12.9`. If it says "not recognized", reinstall and tick
the PATH box.

---

## 2. Put the project somewhere simple

Extract `coc-autofarm-v6.zip` to `C:\coc-autofarm`.

Not Desktop, not OneDrive — OneDrive syncs while the bot writes logs and
settings, and you get file locks. `C:\coc-autofarm` is fine.

Check the folder contains `main.py`, `requirements.txt`, `models\best.pt`.

---

## 3. Install the dependencies

Open Command Prompt **in that folder** (type `cmd` in the Explorer address bar
and press Enter), then:

```
run.bat
```

On the first run it asks to create `.venv` — answer `Y`. It downloads a few GB;
5–15 minutes depending on connection. Go make coffee.

When it finishes, the app window opens. Close it for now, we need the emulator
ready first.

If you prefer doing it by hand:

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 4. Prepare the emulator

1. Install **LDPlayer 9** (or your emulator of choice).
2. Install Clash of Clans from the Play Store inside it, log into the account
   you are willing to lose.
3. Set the resolution **once**: LDPlayer → Settings → Display → `1280x720`,
   DPI 240. Do not change it later. Every calibrated coordinate is normalised,
   but the screenshot comparison assumes a stable size.
4. Turn ADB on:
   * **LDPlayer 9** — Settings → Others → *ADB local connection* → ON
   * **MuMu 12** — Settings → Advanced → *ADB debugging*
   * **BlueStacks 5** — Settings → Advanced → *Android Debug Bridge*
   * **MEmu** — Settings → ADB
   * **NoxPlayer** — Settings → General → *Root/ADB*
5. Note the port. LDPlayer's first instance is **5555**; MuMu is **16384**;
   BlueStacks is 5555 then 5565; MEmu 21503; Nox 62001.

Verify from Command Prompt:

```
adb devices
```

You should see `127.0.0.1:5555   device`. If the list is empty, ADB is still off,
or the port is different — check the emulator's own settings screen, it always
shows the port.

> No `adb` on PATH? It ships with the emulator. For LDPlayer it is at
> `C:\LDPlayer\LDPlayer9\adb.exe`. You can point the app at it in
> Advanced → Connection → adb path.

---

## 5. First connect

Open the app (`run.bat` again — the environment already exists so it starts
immediately).

In the header:

1. Emulator dropdown → pick yours (`LDPlayer 9`).
2. The serial field fills itself (`127.0.0.1:5555`). Change it if your port
   differs.
3. Press **Connect**.

The status chip goes amber → green and `connected 127.0.0.1:5555 · 1280x720`
appears in the activity log. If it stays red, the line in the activity log says why
(`connect failed: ...`).

---

## 6. Calibrate the buttons

The bot does not look for buttons with a model — it taps fixed points you show
it once. This is why it does not break when the game updates.

Press **Calibrate** next to Start on the Farm page. It opens the calibration
window, and when you close it the app reloads the result by itself — no restart
needed. From a terminal the same thing is:

```
.venv\Scripts\python.exe calibrate.py
```

If you press Start before calibrating, the app tells you exactly which buttons
are missing and offers to open the calibration window.

A window opens with a live screenshot of the emulator and a list of buttons.
Click each button **in the emulator screenshot**, in the order shown:

| # | Button | Where |
|---|---|---|
| 1 | Attack button | village, bottom-right |
| 2 | Find Match | the search screen, green button |
| 3 | Next base | search screen, right side |
| 4 | Attack | confirm the battle |
| 5 | Goblin troop | battle, bottom troop bar |
| 6 | Surrender | battle, bottom-left |
| 7 | Surrender confirm | the red button on the quit dialog |
| 8 | Return Home | post-battle screen |
| 9–11 | Retry spots 1–3 | three taps around the village centre, used when a scan fails |

Then the three **training** controls, which are optional but needed for the
train task:

| # | Button | Where |
|---|---|---|
| 12 | army_open | the army button in the village, bottom-left |
| 13 | troop_pick | one troop icon inside the army panel |
| 14 | army_close | the panel's X |

Press **Save**. This writes `controls` into `settings.json` in the project
folder.

> If you only want raiding, buttons 1–11 are enough; the trainer stays off and
> says `calibrate: ...` instead of pretending.

---

## 7. Optional: collector templates

The collect task needs to recognise the collector bubbles. Crop one:

```
.venv\Scripts\python.exe capture_template.py
```

Drag a box around **one** collector bubble, name it `collect`, save. Repeat as
`collect_gold` / `collect_elixir` / `collect_dark` if they look different.

Skip this and collect is simply off. Nothing else breaks.

---

## 8. Optional: an upgrade plan

Only needed for the plan-based *Upgrade buildings* task. Smart upgrade and auto
mode do not need it.

```
.venv\Scripts\python.exe record_plan.py
```

Click through one upgrade on the live screenshot, press *New step* for the next,
then *Save plan*. One step is replayed per interval.

---

## 9. First real run — no auto mode

1. Make sure the emulator is showing the **village**, not the app drawer.
2. In the app's Farm page set the "Attack when above" thresholds. Defaults
   (500K gold / 50K elixir) are sane; raise them if you only want fat bases.
3. Press **Start farm**.
4. Watch the first two raids. You should see, in the log:

```
tap attack_btn_home at (1187, 655) wait 0.52s
search: Gold 640K / Elixir 601K -> above threshold
attack: 46 goblins deployed
battle ended: +741K Gold +702K Elixir
```

If the log shows taps but the game does nothing, the coordinates are stale —
re-run `calibrate.py`. If it attacks but reads 0 loot, the post-battle screen
changed; check that button 8 (Return Home) is right.

**Stop** halts it. On Windows, **F12** also stops it from anywhere.

---

## 10. Turn on auto mode

Once a manual run works, the rest is one switch.

1. Farm page → **AUTO MODE** → on. The start button becomes **Start auto**.
2. Advanced page → **Smart upgrade**:
   * `Dry run` ON the first time. The bot logs *"would upgrade X"* and taps
     nothing. Run it for a few raids and check you agree with its taste.
   * Set **Storages full at** to your real storage capacity (gold / elixir /
     dark). Wrong numbers only change how long it farms, it does not break.
3. Dry run OFF, then **Start auto**.

From here it runs itself:

* a builder is free → it upgrades one thing per raid, best tier first,
  Town Hall included;
* every builder busy → it farms;
* storages full and nothing to upgrade → it stops farming, tells you, and
  checks for a free builder every 10 minutes;
* still full after 3 hours → it stops itself and notifies you.

Walls are on automatically in auto mode: they are the only thing that still
soaks up loot when every builder is busy.

---

## 11. Daily checks

| Check | Where |
|---|---|
| Is it still running? | the status chip in the header |
| Did it upgrade anything? | Tasks page, *Smart upgrade* state |
| Why did it stop? | last lines of the activity log |
| Loot per hour | the totals line under the buttons |

Restart it whenever you like — settings, calibration and templates all persist
in `settings.json` and the `templates/` / `plans/` folders.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ADB Error` on startup | emulator off, or ADB toggle off | start the emulator, enable ADB, check the port |
| `adb devices` is empty | wrong port | each emulator shows its port in settings; type it in the header |
| Connects, then taps land nowhere | resolution changed since calibration | set the emulator back to the calibrated size, or re-calibrate |
| `Cannot start: button positions missing` | calibration never saved | press **Calibrate**, set all 11, press Save |
| `no upgrade bubbles` | all builders busy, or nothing affordable | normal — it farms instead |
| Smart upgrade skips everything | OCR cannot read the card | run with dry run off once and watch; if names never appear, the card layout changed |
| Collect never runs | no template and no spots | run `capture_template.py`, or set `collect_spots` in settings.json |
| Train says `calibrate: ...` | buttons 12–14 not set | re-run `calibrate.py` |
| GUI opens then closes | dependency missing | run `run.bat` again; it repairs the venv |
| Two windows fighting | two instances running | close one; one bot per emulator |

---

## The honest part

* This breaks Supercell's Terms of Service. Accounts get banned. The humaniser
  makes the input look less mechanical; it does not make you invisible.
* Loot per hour is roughly 400–800K gold in the trophy range the defaults
  target. It is a farm bot, not a money printer.
* Anything the OCR cannot read is skipped rather than guessed. That is
  deliberate: a bot that guesses taps random buildings.
