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

**Run 18 — GitHub connection**

No development changes were made. I checked the project's link to GitHub: it already points to your repository, praveena2804/DevTeamAssistant, which is still empty. The push failed again because this computer is signed in to GitHub as a different account (Praveena-ROR), so nothing was sent. Your sign-in as praveena2804 is needed before Parts A to C can be uploaded.

**Run 19 — GitHub backup and Part D plan**

No features were built. Parts A to C are now safely on GitHub: the main line and all six version and approval labels were uploaded to praveena2804/DevTeamAssistant, and I checked that GitHub matches this computer exactly. I then read the plans and the finished Part C and wrote the Part D plan, which explains how the project will move through all its stages with stand-in agents, wait for your approvals, and survive a crash or restart. Part D has not been started; the plan is waiting for your approval.

**Run 20 — Part D, step 1 (foundations)**

I uploaded the plan to GitHub and checked it matches this computer. Then I laid the groundwork for the assembly line: added the LangGraph library, wrote down the full list of workflow steps and which step may follow which, added the database tables the background worker will need (job queue, worker lock, progress events, spec versions, plans), and added rules that stop agents and lower layers from ever touching the workflow engine. A check while writing the step list found a mistake in my own plan (a "cancel" could, on paper, have led into building), which I fixed. Nothing runs yet; the stand-in agents and the workflow itself come in the next steps.

**Run 21 — Part D, step 2 (Head Agent rules and service steps; partly blocked)**

I wrote the Head Agent's planning rules as plain code with no AI: it builds a fixed task list, checks any task list and names every problem it finds (circular steps, unknown agents, writing outside allowed folders, missing requirements and more), picks the next step in the right order, and decides what happens after a failure (refusals are never retried, a failed task gets one more try, a crash is not counted as a failure). I added the service steps that are not built yet as honest stand-ins that can only say "not done", and the real step that locks the approved specification after re-checking your approval. Following your new rule, nothing can use the fake AI, and that is now enforced by a check. I also listed every library's licence, as you asked for orjson, and found a second MPL-licensed library (certifi) that has been there since Part A. The ten stand-in agents were not built: your rule needs a real AI connection, and the platform has none until Part F, so this step is blocked until you decide how to proceed.

**Run 22 — Part D, steps 2 to 5 (completed in one run)**

Following your decision, I connected the platform to the AI model that runs on this laptop (Ollama), through the existing AI gateway only, and nowhere else: it refuses any address that is not this computer and ignores proxy settings. All ten agents now do a small but genuine job with the local AI. The analyst lists the BRD's requirements with their sources, the build agents write design notes, and the documenter writes run notes from the facts only, each labelled "not built, tested or verified". I then built the full workflow with its approval stops, the background worker that runs it (it survives being killed and carries on without redoing finished work), the controls to pause, resume and cancel, and the browser pages to start a run, approve each step, watch progress live, and see the trace and history. Full runs with the real AI completed several times; one long test was spoiled because the laptop went to sleep, and I reran it. The fake AI from Part C is still used only by Part C's own tests. Nothing was pushed to GitHub, and your real database was not changed (it will be backed up and updated the next time you start the app).

**Run 23 — Part D upload and Part E plan**

I uploaded the finished Part D work to GitHub as its own branch, separate from the main line, because you have not approved Part D yet, and checked that GitHub now holds exactly the same six commits and the same version tags as this laptop. I then read the specification, roadmap, working plan and trackers again and wrote the plan for Part E: the sealed, offline Docker room where generated code will be allowed to run, and the two starter kits every generated application will begin from. No Part E code was written; the plan waits for your decisions, starting with which Docker you can use under its licence terms.

**Run 24 — Part E plan, second version**

Following your choice of the free Docker Engine inside WSL instead of Docker Desktop, and your rule that everything must be free, I reworked the Part E plan. I looked (without changing anything) at the Ubuntu system already installed in WSL on this laptop: it is ready for Docker, but Docker is not installed yet. The plan now explains how Docker will be installed from Ubuntu's own free packages, how the platform will connect to it safely using private certificates so no other program can use it, and lists every tool and library with its free licence. No Part E code was written; the plan waits for your approval.

**Run 25 — Part E plan, third version**

Following your review, I corrected the Part E plan. Licences are now handled in sensible groups: the free operating-system parts inside the containers may carry GPL licences, while the libraries that end up in generated applications must be permissive, and all of it is checked automatically. Before anything depends on them, three small trial checks will prove that the secure connection to Docker works with the locked key, that the React starter kit builds without internet, and that files move in and out of containers safely; if any of them fails, I stop and ask you. PostgreSQL is moved to Part K. I also wrote down exactly what "free only" means and where every download comes from. I recorded your Docker decision in its decision document. No Part E code was written and nothing was installed; these changes are not yet committed.

**Run 26 — Part D approved**

You approved Part D. I marked it as version 0.4.0, joined it into the main line of the project on this laptop and labelled it, as was done for Parts A to C. Nothing was uploaded to GitHub.

**Run 27 — Part E**

I built Part E, the safe place where generated code will be built and tested. Docker now runs inside the Ubuntu system on this laptop (the free version, not Docker Desktop), and the platform talks to it over a locked connection whose key is kept encrypted, with its password in Windows Credential Manager. Every build or test runs in a fresh, throw-away container with no internet, a read-only system, no administrator rights and fixed limits on memory, processor and time, and the container is deleted afterwards. Only ten fixed build and test steps can be run; there is no way to type an arbitrary command. I made two starter kits (React and plain HTML, each with a FastAPI and SQLite back end) and both passed their own tests inside the sandbox without internet. One verification was first recorded as "blocked" because the laptop was short of memory while I ran other checks at the same time; it passed when re-run on its own. The sandbox can be checked from the new Sandbox page. The project runs from Part D do not use the sandbox yet; that comes in a later part. The full test run, including a complete project run with the real AI model, found two faults that I fixed: the background worker could stop at start-up when the starter-kit folder was missing, and one long sandbox test switched Docker off and left the tests after it without Docker. I also fixed an older test that failed by chance about once in sixteen runs.

**Run 28 — Part E approved**

You asked me to finish Part E completely. I ran every check again, fixed what they found, marked Part E as version 0.5.0, joined it into the main line of the project and labelled it, and uploaded all of it to GitHub, where I checked that everything matches what was tested here.

**Run 29 — Part F planning**

I wrote the plan for Part F, which completes how the platform talks to the local AI models and adds search over the uploaded documents. I first checked what earlier parts had already built, so the plan reuses that work instead of redoing it. An independent review of the first draft found eighteen problems, including work that belongs to a later part, and I corrected all of them. The plan lists the decisions I need from you, such as approving the AI models and choosing the document-search model. No code was written and nothing was committed or uploaded.

**Run 30 — Part F**

I built Part F, which governs how the platform uses the local AI models and adds search over the uploaded documents. A settings file now says which approved model does which job; if a model runs out of memory or is too slow, the platform first gives it less text, then tries the smaller model, and if nothing fits it pauses the work safely instead of failing or calling anything outside this computer. Every model call is now recorded without its content, and the prompts live in checked files that cannot be changed silently. I added a way to score prompts against sample cases, and a check that will stop changes once a prompt needs scoring. Uploaded documents are now turned into a searchable index on this computer (a small local model plus ChromaDB), and searches only ever return the same project's own text and references. ChromaDB came with four published security warnings that concern only its server mode, which the platform never uses; you accepted it on that basis. A new Settings page shows the models and limits. Everything was tested, including runs with the real AI model, and the work was uploaded to GitHub as its own branch; it is not yet joined to the main line.
