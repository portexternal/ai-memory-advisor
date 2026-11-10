AI Memory Advisor 0.3.0 (thesis prototype)
==========================================

What it is
----------
A small Windows program that learns which apps you open and when. For the first 10 days it only
watches. From day 11 it predicts the heavy app you are about to open and reads that app's files
into memory in advance, so the app starts faster. Windows 10 or 11, no administrator rights.

How to use it
-------------
1. Put AIMemoryAdvisor.exe in a folder where it can stay (for example Documents). Do not move it later.
2. Double-click it. Read the notice and press Yes.
3. That is all. It runs in the background and starts by itself when you sign in.
4. Use the PC as usual for 10 days. Open your heavy apps (games, Blender, video editors) as you normally do.

To see what it learned: double-click the exe again and press Yes. A report opens in the browser.
To stop and remove it: double-click the exe again and press No.
To delete everything it stored: delete the folder %LOCALAPPDATA%\AIMemoryAdvisor.

Terms and privacy
-----------------
Read TERMS.txt and PRIVACY.txt in this folder before you start. Pressing Yes on the first-run
notice means you accept them. Short version: free research prototype, no warranty; everything it
records stays on your computer; sharing data is optional and only happens if you send a file yourself.

What it records
---------------
Program names (for example blender.exe), start and stop times, idle periods, free memory, and
the list of files each app opens while starting.
It does NOT record window titles, keystrokes, screenshots or file contents.
It never uses the network. All data stays on this computer in %LOCALAPPDATA%\AIMemoryAdvisor.

What to expect
--------------
Days 1 to 10: the report says "Learning: day N of 10". Nothing is pre-loaded.
A day only counts when the PC was used that day.
Day 11 on: the report shows the apps it prepares, each prediction, and the time to first window
with and without a prefetch.
It only prepares apps that were opened at least 3 times and need at least 200 MB of files.
If you use two or more heavy apps around the same time of day, the memory is shared between
them by how much you used each one lately, so switching apps still finds files ready.
It stays out of the way: it pauses when the CPU or disk is busy, when a heavy app is already
running, and it never uses more than 40 % of installed memory. Windows can take the cached
memory back at any moment.

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
Help improve it (optional)
--------------------------
Double-click "Share my data.cmd". It writes an anonymised copy of your usage data to a zip file
(program names, weekday and time of day, launch times, no dates, no file names, no user name).
Nothing is uploaded. You read the file first and decide whether to send it. Details: docs/SHARING.md
in the project repository, https://github.com/portexternal/ai-memory-advisor

Windows warning
---------------
The file is not code-signed, so Windows SmartScreen may show "Windows protected your PC".
Press "More info", then "Run anyway". The full source code is in the project repository.

Settings (optional)
-------------------
%LOCALAPPDATA%\AIMemoryAdvisor\config.json, for example "observe_days": 10. Restart the program
after a change (run it, press No, run it again).
