AI Memory Advisor 0.2.0 for Mac (thesis prototype, experimental)
================================================================

What it is
----------
A small background program that learns which apps you open and when. For the first 10 days it
only watches. From day 11 it predicts the heavy app you are about to open and reads that app's
files into memory in advance, so the app starts faster. macOS 12 or newer, no administrator rights.

Pick the right download: "apple-silicon" for M1 / M2 / M3 / M4 Macs, "intel" for older Intel Macs.

How to start it
---------------
1. Unzip and put the AIMemoryAdvisor-mac folder where it can stay (for example Documents).
   Do not move it later.
2. The app is not signed by Apple, so macOS blocks a normal double-click the first time.
   Right-click AIMemoryAdvisor.app, choose Open, then Open again.
   If macOS still refuses: open Terminal and run
       xattr -dr com.apple.quarantine ~/Documents/AIMemoryAdvisor-mac
   (change the path if you put the folder somewhere else), then double-click the app.
3. Read the notice and press Yes. It now runs in the background, with no Dock icon,
   and starts by itself when you log in.

The three helper files in the same folder
-----------------------------------------
Open report.command     shows what it has learned and done (opens in the browser)
Stop.command            stops it and removes it from login
Share my data.command   optional, see below
The first time, right-click each one and choose Open (same Apple check as the app).
To delete everything it stored: delete ~/Library/Application Support/AIMemoryAdvisor

Getting the best result
-----------------------
The program learns your routine, so give it one to learn:
- Leave it running every day. Do not stop it between sessions. It starts by itself at sign-in.
- Open your heavy apps at your usual times, the way you normally work or play. Same app around
  the same time on the same weekdays is what it learns fastest.
- Use the computer on as many of the 10 learning days as you can. A day without use is skipped,
  not counted, so nothing is lost, it just takes longer.
- Open each heavy app at least 3 times during the learning days, or it will not be prepared.
- Do not change the clock or time zone, and do not delete the data folder, during the test.
- No fixed routine? Still fine. Predictions will be weaker at first and improve as days add up.
Terms and privacy
-----------------
Read TERMS.txt and PRIVACY.txt in this folder before you start. Pressing Yes on the first-run
notice means you accept them. Short version: free research prototype, no warranty; everything it
records stays on your computer; sharing data is optional and only happens if you send a file yourself.

What it records
---------------
Program names (for example Blender), start and stop times, idle periods, free memory, and the
list of files each app needs to start.
It does NOT record window titles, keystrokes, screenshots or file contents, and it does not ask
for Screen Recording or Accessibility permission.
It never uses the network. All data stays in ~/Library/Application Support/AIMemoryAdvisor.

What to expect
--------------
Days 1 to 10: the report says "Learning: day N of 10". Nothing is pre-loaded.
Day 11 on: the report shows the apps it prepares, each prediction, and the time to first window
with and without a prefetch.
It only prepares apps that were opened at least 3 times and need at least 200 MB of files.
It pauses when the CPU or disk is busy or a heavy app is already running, and never uses more
than 40 % of installed memory. macOS can take the cached memory back at any moment.

Honest limits of the Mac version
--------------------------------
- New and less tested than the Windows version. It was built and checked on Apple's cloud Macs,
  not yet over a full 10 days on a personal Mac.
- Macs have fast internal SSDs, so the time saved per launch is expected to be small. The gain
  is larger for apps on an external or slower drive.
- macOS does not let a normal program list another app's memory-mapped files, so the launch
  file set is the app bundle plus the files the app holds open.
- Apps inside /System (Safari, TextEdit, and so on) are logged but never prepared.

Help improve it (optional)
--------------------------
Double-click "Share my data.command". It writes an anonymised copy of your usage data to a zip
file (program names, weekday and time of day, launch times, no dates, no file names, no user name).
Nothing is uploaded. You read the file first and decide whether to send it. Details: docs/SHARING.md
in the project repository, https://github.com/portexternal/ai-memory-advisor