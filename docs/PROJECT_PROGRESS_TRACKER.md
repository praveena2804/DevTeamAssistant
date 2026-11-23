# DevTeam AI — Development Progress

**Run 1 — Part A**

I created the basic application settings. The application now checks its settings before it starts, refuses unknown or unsafe settings, and has a low-memory mode for this laptop.

**Run 2 — Part A**

I added the logging system. The application now records what happens, links related actions together, and hides passwords and secret keys in its logs.

**Run 3 — Part A**

I created the database and its backup. The application can now store information and makes a backup copy before it changes the database layout.

**Run 4 — Part A**

I built a basic back office (API) and web page that talk to each other. The page shows whether the system is healthy. This is only a shell; it does no real work yet.

**Run 5 — Part A**

I added setup scripts, a computer health check, automatic tests, and security scans. All tests passed on this laptop. GitHub's online checks could not be run because Git use is not allowed yet.

**Run 6 — Part A**

I measured how fast four small AI models run on this laptop. The first attempt was unfair to the models, so I kept it marked as invalid and ran it again; all four then passed. No model has been chosen yet; that is your decision.

**Run 7 — Planning only**

No development changes were made. I checked how AI models will be chosen for each task (not built yet) and wrote the plan for Part B.

**Run 8 — Tracker only**

No development changes were made. I simplified this progress page.

**Run 9 — Part B**

I saved Part A as the first version in your local Git history, then built the project "front door". You can now create projects, upload BRDs (PDF, Word, text or Markdown), and read the extracted text, where every piece shows the page, section and paragraph it came from. Damaged, locked, disguised or oversized files are refused with a plain explanation while the good files go through. Only someone with the local key on this computer can use it, each project's files are kept apart, and every action, including refused ones, is written to a log that cannot be changed. All tests passed. Very large PDFs (300 pages) take about 40 seconds to read. You haven't reviewed it yet, and there is still no AI involved.

**Run 10 — Part B release**

No new features were built. You approved Part B, so I re-ran all the tests (all passed), marked it as version 0.2.0, and saved it into the main line of your local Git history. I then wrote the plan for Part C, the safety system all agents will go through, and listed the decisions I need from you before building it.

**Run 11 — Part C planning**

No development changes were made. I recorded your Part C decisions, wrote out the full table of proposed values and limits for you to review, corrected one limit I had set too high for this laptop, and broke Part C into its four checkpoints.

**Run 12 — Part C, checkpoint C.1**

I recorded your approval of the values and limits (with 3 minutes per AI call), then built the first of Part C's four checkpoints: the fixed "forms" that every agent and part of the system must use to pass work around. These forms now refuse, on their own, paths that try to leave the project folder, an agent borrowing another agent's job, an agent claiming tests passed or approving anything, requirements the AI invented without a source, and BRD text dressed up as system instructions. I wrote attack tests for all of these, and proved each protection matters by switching it off and watching the tests catch it. All tests passed, including everything from Parts A and B. Nothing is enforced while the system runs yet; that starts in the next checkpoint, which I have not begun.

**Run 13 — Part C, checkpoint C.2**

You asked me to push everything to GitHub, but GitHub refused: the account signed in on this computer is not allowed to write to your repository, so nothing was sent. I then built the second checkpoint: the system's rulebook now decides, for every agent and every action, whether it is allowed, refuses everything not explicitly permitted, and logs each refusal; editing the rule files cannot give an agent more power than the specification allows. BRD text is now handed to the AI clearly marked as "data, not instructions", and agent answers that claim tests passed or contain secrets are rejected. Your sign-in key was moved into Windows Credential Manager: all four of your checks passed, and only then was the old key file deleted; the app and the sign-in link work as before. Along the way the tests caught and I fixed three real problems, including a place where a secret could have slipped into a log. All tests passed.

**Run 14 — Part C, checkpoint C.3**

I built the third checkpoint. Agents can now only touch files through five controlled tools (there is no way to run commands), and every file path is checked against the real disk, so tricks such as hidden shortcuts that point outside the project folder are refused. Every tool use, allowed or refused, goes into the activity log, and file changes are recorded with before-and-after fingerprints. The activity log is now tamper-evident: each entry is chained to the one before, and the Activity page shows "Log intact" (or exactly where it was broken). Your existing log entries were kept exactly as they were and are covered by the check. Your database was backed up and upgraded, and the app works as before. The tests found and I fixed one real design problem along the way. One attack test could not run on this computer because Windows does not allow it for your account; the closely related junction attack was tested and blocked. All other tests passed.

**Run 15 — Part C, checkpoint C.4**

I built the last checkpoint of Part C. Every agent now has spending limits (time, AI usage, number of attempts and tool uses) that are checked before each step, and work stops cleanly when a limit is reached. Approvals can only be given by a person with the right role, are tied to one exact version of the specification, and fake or reused approvals are refused. The AI connection is now a single controlled doorway that refuses to send secrets, makes the AI handle one request at a time, and is connected only to a fake AI for testing. The ten agent folders now exist with their job descriptions but no behaviour yet, and automatic checks stop any agent code from reaching files, the network, the database or another agent directly. A short demo command shows allowed and refused actions and the tamper check. Your database was backed up and upgraded, and the app works as before. The tests found and I fixed two real problems, and showed one limit had no test, which I added. All tests passed except the one symlink test this computer cannot run.

**Run 16 — Part C final check**

No features were built or changed. I ran every check on the finished Part C: all tests (all passed except the one symlink test this computer cannot run), the code-quality checks, a security check of every outside package the project uses (no known problems), and a scan for leaked secrets (none). I checked your real database without changing it: the activity log is intact, and your sign-in key is still safely in Windows Credential Manager. I ran the demonstration and wrote the Part C completion report for your approval.

**Run 17 — Part C release**

No new features were built. You approved Part C, so I re-ran the tests and checks after marking it as version 0.3.0, and saved it into the main line of your local Git history with the Part C approval tags. Nothing was sent to GitHub, and Part D has not been started.
