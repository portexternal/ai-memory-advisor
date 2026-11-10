# Sharing your data (optional)

The predictor is weak for people without a fixed routine. A pool of real usage data from many
different people is the way to fix that. Sharing is opt-in, and the program never uploads anything.

## How
Double-click `Share my data.cmd` next to the exe, or run `AIMemoryAdvisor.exe --share`.
It explains what goes in, asks Yes or No, then writes one zip into
`%LOCALAPPDATA%\AIMemoryAdvisor\share\`. Open it and read `share.json` before you decide.

## What is in the file
| In | Not in |
|---|---|
| Program names (for example `blender.exe`) | Dates (only day 0, 1, 2 ... and the weekday) |
| Weekday and time of day of each start, stop, focus change, idle period | File names and paths |
| Time to first window, and whether a prefetch came before it | User name, PC name, IP address |
| Each prediction and its outcome (hit or false) | Window titles, keystrokes, screenshots |
| Size and file count of each app's launch set | Anything about file contents |
| RAM size, CPU thread count, Windows version | |
| A random install id, so two files from one PC can be joined | |

Program names can say something about you. If one of them is private, delete those lines from
`share.json` before sending, or do not send the file.

## Where to send it
There is no private upload channel yet. It is the next thing on the roadmap.
Until then you can attach the zip to a GitHub issue in this repository, but issues are **public**:
only do that if you are fine with the file being readable by anyone.

## Terms
Sending a share file means you agree to section 4 of `docs/PRIVACY.md`: what the project may do with it,
how long it is kept, and how to have it deleted (quote the install id inside the file).

## What it is used for
Training and testing better launch predictors, and measuring which apps gain from a prefetch.
Any data set published from it will hold only what the table above lists.
