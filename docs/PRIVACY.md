# Privacy notice

Version 1.0, 10 November 2026. Who is responsible: the maintainer of the AI Memory Advisor project
(GitHub account `portexternal`). Contact: https://github.com/portexternal/ai-memory-advisor/issues
(public: do not put personal information in an issue).

## 1. The short version
- The program records data **on your computer only**. It has no network code and sends nothing.
- The project receives data from you **only if you create a share file yourself and send it**.
- You can stop, delete everything, or never share. The program works the same either way.

## 2. What is recorded on your computer
| Recorded | Not recorded |
|---|---|
| Program names (for example `blender.exe`) | Window titles, web addresses, document names |
| When each program starts, stops and comes to the front | Keystrokes, mouse movement, clipboard |
| When the computer is idle | Screenshots, audio, camera |
| Free memory, every few minutes | File contents |
| The list of files each program needs to start (paths and sizes) | Passwords, accounts, messages |

Where: `%LOCALAPPDATA%\AIMemoryAdvisor` on Windows, `~/Library/Application Support/AIMemoryAdvisor`
on Mac. Why: to predict which program you open next and prepare it. How long: until you delete the
folder. Who can see it: only people with access to your user account on that computer.

## 3. Your choices on your own computer
Stop the program at any time. Delete the data folder at any time. Edit `config.json` to change how
it behaves. Nothing is lost elsewhere, because nothing was sent elsewhere.

## 4. Optional sharing
Sharing is voluntary and separate. The program asks, you answer Yes or No, and it then only writes
a zip file to your disk. **You** decide whether to send that file to the project.

**What is in the share file**
- Program names.
- For each start, stop, focus change and idle period: the weekday, the time of day, and a day
  counter (day 0, 1, 2 ...). No calendar dates.
- Launch times, and each prediction with its result.
- The size and file count of each program's launch set. No file names or paths.
- Memory size, processor thread count, operating system version.
- A random install id, so two files from one computer can be joined, and so your data can be
  found and deleted if you ask.

**What is never in it**: your name, user name, computer name, IP address, dates, file names or
paths, window titles.

**Why the project wants it**: to train and test better predictions, to measure which programs
gain from pre-loading, and for research and publications about that.

**What you agree to when you send a share file**
- You send it of your own free choice, and it describes your own use of the computer.
- The project may store and analyse it for the purposes above.
- The project may publish results, and may publish a data set that contains only the fields
  listed above.
- Program names can reveal something about you (for example a medical or a company program).
  Read `share.json` first and delete any line you do not want to share.

**Withdrawing**: ask for deletion at any time, quoting the install id from your share file. Your
data is then deleted from the project's storage. Data already published in an anonymised data set,
or already used in published results, cannot be pulled back out; say so before sending if that
matters to you.

**How long it is kept**: for as long as the project is active, then deleted or kept only as the
anonymised data set.

**Who it is shared with**: nobody outside the project, except as the published anonymised data set.
It is never sold and never used for advertising.

## 5. Your rights
Depending on where you live (for example under Thailand's PDPA or the EU's GDPR) you may have the
right to access, correct, delete or object to the use of your data, and to complain to your data
protection authority. Use the contact above. The install id is the only way the project can find
your data, because the share file holds nothing else that identifies you.

## 6. Children
The program is not aimed at children. If you are not old enough to consent where you live, a
parent or guardian must agree before you use it or share data.

## 7. Changes
If this notice changes, the version number changes and the program asks again before it continues.