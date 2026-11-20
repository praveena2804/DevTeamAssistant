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
